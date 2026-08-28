"""kindo-asr — 本地 ASR Provider 服务（技术方案 §11.2）。

契约（Hub → kindo-asr，第一方内部接口）：
  POST /asr/utterances                     {utterance_id, format, sample_rate, channels}
  POST /asr/utterances/{uid}/feed          binary PCM（可多次）
  POST /asr/utterances/{uid}/finish     -> {text, confidence?, language?, metadata}
  POST /asr/utterances/{uid}/cancel
  GET  /health                          -> {status, model, ready}

隐私（架构 §13 / PRD §13）：音频仅内存缓冲，finish/cancel 后立即释放；不落盘、不写日志。
引擎：sherpa-onnx Paraformer（中文）。未配置模型时 health=ready:false（status=no_model），
finish 返回 503 —— 诚实降级，不做任何假转写。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s kindo-asr %(message)s")
logger = logging.getLogger("kindo-asr")


def _resolve_model_dir() -> Path:
    import os

    env = os.environ.get("KINDO_ASR_MODEL_DIR")
    if env:
        return Path(env)
    # 开发环境：仓库内 apps/kindo-asr/model；容器：/app/model
    here = Path(__file__).resolve()
    for base in (here.parent.parent.parent, Path("/app")):
        candidate = base / "model"
        if candidate.is_dir():
            return candidate
    return here.parent.parent.parent / "model"


MODEL_DIR = _resolve_model_dir()


class Utterance:
    def __init__(self, utterance_id: str, sample_rate: int, channels: int):
        self.id = utterance_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunks: list[np.ndarray] = []
        self.lock = threading.Lock()

    def feed(self, data: bytes) -> None:
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        with self.lock:
            self.chunks.append(samples)

    def drain(self) -> np.ndarray:
        with self.lock:
            if not self.chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self.chunks)
            self.chunks = []
            return audio


class OfflineRecognizerEngine:
    """sherpa-onnx Paraformer 离线识别（整段 utterance 一次转写）。"""

    name = "sherpa-onnx-paraformer-zh"

    def __init__(self, model_dir: Path):
        import os

        model_file = next(model_dir.rglob("model.int8.onnx"), None) or next(
            model_dir.rglob("model.onnx"), None
        )
        if model_file is None:
            raise FileNotFoundError(f"{model_dir} 下未找到 model.int8.onnx / model.onnx")
        tokens = next(model_dir.rglob("tokens.txt"))
        # 热词偏置（2026-08-26 儿童语音治理，不换模型）：每行一词的文本文件，
        # 经 KINDO_ASR_HOTWORDS_FILE 挂载（如儿童高频的动画角色名）；未配置不启用。
        hotwords_file = os.environ.get("KINDO_ASR_HOTWORDS_FILE", "").strip()
        kwargs = dict(
            paraformer=str(model_file),
            tokens=str(tokens),
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
        )
        if hotwords_file and Path(hotwords_file).is_file():
            kwargs["hotwords_file"] = hotwords_file
            kwargs["hotwords_score"] = float(os.environ.get("KINDO_ASR_HOTWORDS_SCORE", "1.5"))
            logger.info("ASR 热词已启用: %s", hotwords_file)
        try:
            self.recognizer = self._build(kwargs)
        except TypeError:
            # 当前 sherpa-onnx 版本不支持热词参数：退回基础加载（不失败）
            kwargs.pop("hotwords_file", None)
            kwargs.pop("hotwords_score", None)
            self.recognizer = self._build(kwargs)
        self.model_name = model_file.parent.name
        logger.info("ASR 模型加载完成: %s", self.model_name)

    @staticmethod
    def _build(kwargs: dict):
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_paraformer(**kwargs)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> dict:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        self.recognizer.decode_stream(stream)
        result = stream.result
        text = result.text.strip()
        # Paraformer 无 token 级置信度输出：非空文本视为可接受（0.9），空文本 0.0；不伪造精确数值
        confidence = 0.9 if text else 0.0
        return {
            "text": text,
            "confidence": confidence,
            "language": "zh",
            "metadata": {"model": self.model_name, "timestamps": len(result.timestamps)},
        }


class AsrState:
    def __init__(self) -> None:
        self.utterances: dict[str, Utterance] = {}
        self.engine: OfflineRecognizerEngine | None = None
        self.engine_error: str | None = None
        self._load_engine()

    def _load_engine(self) -> None:
        if not MODEL_DIR.exists():
            self.engine_error = f"模型目录不存在: {MODEL_DIR}"
            logger.warning("ASR 引擎未加载：%s", self.engine_error)
            return
        try:
            self.engine = OfflineRecognizerEngine(MODEL_DIR)
        except Exception as exc:
            self.engine_error = str(exc)[:300]
            logger.warning("ASR 引擎加载失败: %s", exc)


state = AsrState()
app = FastAPI(title="kindo-asr", version="0.1.0")


class OpenBody(BaseModel):
    utterance_id: str
    format: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1


@app.get("/health")
def health():
    if state.engine is not None:
        return {"status": "ready", "ready": True, "model": state.engine.model_name}
    return {"status": "no_model", "ready": False, "model": None, "error": state.engine_error}


@app.post("/asr/utterances")
def open_utterance(body: OpenBody):
    if state.engine is None:
        raise HTTPException(503, detail={
            "error": "asr_model_not_configured",
            "hint": "请将 Paraformer 模型解压到 apps/kindo-asr/model/（见 README）",
        })
    if body.format != "pcm_s16le":
        raise HTTPException(400, detail="仅支持 pcm_s16le")
    if body.sample_rate != 16000 or body.channels != 1:
        raise HTTPException(400, detail="仅支持 16kHz mono")
    state.utterances[body.utterance_id] = Utterance(
        body.utterance_id, body.sample_rate, body.channels
    )
    return {"accepted": True}


@app.post("/asr/utterances/{utterance_id}/feed")
async def feed(utterance_id: str, request: Request):
    ut = state.utterances.get(utterance_id)
    if ut is None:
        raise HTTPException(404, detail="utterance 不存在")
    data = await request.body()
    if data:
        ut.feed(data)
    return Response(status_code=202)


@app.post("/asr/utterances/{utterance_id}/finish")
def finish(utterance_id: str):
    ut = state.utterances.pop(utterance_id, None)  # 音频随 finish 释放（不落盘）
    if ut is None:
        raise HTTPException(404, detail="utterance 不存在")
    if state.engine is None:
        raise HTTPException(503, detail="asr_model_not_configured")
    audio = ut.drain()
    if audio.size == 0:
        return {"text": "", "confidence": 0.0, "language": "zh", "metadata": {"empty": True}}
    return state.engine.transcribe(audio, ut.sample_rate)


@app.post("/asr/utterances/{utterance_id}/cancel")
def cancel(utterance_id: str):
    state.utterances.pop(utterance_id, None)
    return {"cancelled": True}
