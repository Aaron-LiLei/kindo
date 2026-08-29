"""kindo-tts — 本地克隆 TTS Provider 服务（技术方案 §6.7，PRD TTS-005~007）。

契约（Hub → kindo-tts，第一方内部接口）：
  PUT    /v1/voice           {prompt_wav_base64, prompt_text} -> 204（声纹仅内存缓存）
  DELETE /v1/voice           -> 204（清除内存声纹）
  POST   /v1/synthesis       {tts_id, text} -> audio/wav（24kHz 单声道 PCM16）
  GET    /health             -> {status, model, ready, voice_loaded}

隐私（架构 §13 / PRD TTS-007）：参考音频与合成结果仅内存处理，不落盘、不写日志；
服务仅容器网络内可达（compose 用 expose，不映射宿主端口）。
引擎：sherpa-onnx ZipVoice-Distill（零样本克隆，中英双语，int8 ONNX，纯 CPU）。
未配置模型时 /health 返回 ready:false（no_model 降级），synthesis 返回 503——
诚实降级，不做任何假合成（Hub 侧自动回退 Android 系统 TTS）。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s kindo-tts %(message)s")
logger = logging.getLogger("kindo-tts")

# 单句上限（技术方案 §6.7）：Hub 分句缓冲 ≤100 字符，这里放宽到 200 兜底
_MAX_TEXT_CHARS = 200
# 参考音频时长边界（秒）：过短克隆不像，过长拖慢推理且降低质量（ZipVoice 建议 <10s）
_MIN_PROMPT_SECONDS = 2.0
_MAX_PROMPT_SECONDS = 20.0
_SAMPLE_RATE = 24000


def _resolve_model_dir() -> Path:
    env = os.environ.get("KINDO_TTS_MODEL_DIR")
    if env:
        return Path(env)
    # 开发环境：仓库内 apps/kindo-tts/model；容器：/app/model
    here = Path(__file__).resolve()
    for base in (here.parent.parent.parent, Path("/app")):
        candidate = base / "model"
        if candidate.is_dir():
            return candidate
    return here.parent.parent.parent / "model"


MODEL_DIR = _resolve_model_dir()


class VoicePrompt:
    """已加载的参考音频（家长声纹）。仅内存，DELETE 即清。"""

    def __init__(self, samples: np.ndarray, sample_rate: int, text: str):
        self.samples = samples
        self.sample_rate = sample_rate
        self.text = text


class ZipVoiceEngine:
    """sherpa-onnx ZipVoice-Distill 离线克隆合成。"""

    name = "sherpa-onnx-zipvoice-distill-int8-zh-en"

    def __init__(self, model_dir: Path, num_threads: int, num_steps: int):
        import sherpa_onnx

        vocoder = model_dir / "vocos_24khz.onnx"
        model_files = {
            "encoder": next(model_dir.rglob("encoder*.onnx"), None),
            "decoder": next(model_dir.rglob("decoder*.onnx"), None),
            "tokens": next(model_dir.rglob("tokens.txt"), None),
            "lexicon": next(model_dir.rglob("lexicon.txt"), None),
        }
        missing = [k for k, v in model_files.items() if v is None] + (
            [] if vocoder.is_file() else ["vocoder"]
        )
        if missing:
            raise FileNotFoundError(f"{model_dir} 下缺少 {missing}（下载方式见 apps/kindo-tts/README）")
        data_dir = model_dir / "espeak-ng-data"
        if not data_dir.is_dir():
            raise FileNotFoundError(f"{model_dir} 下缺少 espeak-ng-data")
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                    tokens=str(model_files["tokens"]),
                    encoder=str(model_files["encoder"]),
                    decoder=str(model_files["decoder"]),
                    data_dir=str(data_dir),
                    lexicon=str(model_files["lexicon"]),
                    vocoder=str(vocoder),
                ),
                num_threads=num_threads,
                debug=False,
                provider="cpu",
            )
        )
        config.validate()
        self.tts = sherpa_onnx.OfflineTts(config)
        self.num_steps = num_steps

    def synthesize(self, text: str, prompt: VoicePrompt) -> np.ndarray:
        gen = self._generation_config(prompt)
        audio = self.tts.generate(text, gen)
        if audio.samples is None or len(audio.samples) == 0:
            raise RuntimeError("合成结果为空")
        return np.clip(np.asarray(audio.samples, dtype=np.float32), -1.0, 1.0)

    def _generation_config(self, prompt: VoicePrompt):
        import sherpa_onnx

        gen = sherpa_onnx.GenerationConfig()
        gen.reference_audio = prompt.samples
        gen.reference_sample_rate = prompt.sample_rate
        gen.reference_text = prompt.text
        gen.num_steps = self.num_steps
        return gen


class State:
    def __init__(self):
        self.engine: ZipVoiceEngine | None = None
        self.engine_error: str | None = None
        self.prompt: VoicePrompt | None = None
        self.lock = threading.Lock()  # 合成串行化：单模型短句顺序生成

    def load_engine(self) -> None:
        num_threads = int(os.environ.get("KINDO_TTS_NUM_THREADS", "2"))
        num_steps = int(os.environ.get("KINDO_TTS_NUM_STEPS", "4"))
        t0 = time.time()
        try:
            self.engine = ZipVoiceEngine(MODEL_DIR, num_threads, num_steps)
            logger.info("模型加载完成: %s (%.1fs)", MODEL_DIR, time.time() - t0)
        except Exception as exc:  # noqa: BLE001 - 降级启动（no_model）
            self.engine = None
            self.engine_error = str(exc)[:200]
            logger.warning("模型未加载（no_model 降级）: %s", self.engine_error)

    @property
    def ready(self) -> bool:
        return self.engine is not None


state = State()


def decode_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """PCM16 WAV → (float32 mono samples, sample_rate)。多声道取首声道。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getcomptype() != "NONE" or w.getsampwidth() != 2:
            raise ValueError("仅支持 PCM16 WAV")
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


def pcm16_wav_bytes(samples: np.ndarray, sample_rate: int = _SAMPLE_RATE) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


app = FastAPI(title="kindo-tts", docs_url=None, redoc_url=None, openapi_url=None)


class VoiceBody(BaseModel):
    prompt_wav_base64: str = Field(min_length=1)
    prompt_text: str = Field(min_length=1, max_length=500)


class SynthesisBody(BaseModel):
    tts_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)


@app.on_event("startup")
def startup() -> None:
    state.load_engine()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if state.ready else "no_model",
        "model": ZipVoiceEngine.name if state.ready else None,
        "ready": state.ready,
        "voice_loaded": state.prompt is not None,
        "error": state.engine_error,
    }


@app.put("/v1/voice")
def set_voice(body: VoiceBody) -> Response:
    try:
        raw = base64.b64decode(body.prompt_wav_base64, validate=True)
        samples, rate = decode_wav_bytes(raw)
    except Exception as exc:
        raise HTTPException(422, detail=f"prompt_wav 不是合法 PCM16 WAV: {exc}") from exc
    if samples.ndim != 1:
        raise HTTPException(422, detail="参考音频须为单声道")
    duration = samples.size / rate
    if not (_MIN_PROMPT_SECONDS <= duration <= _MAX_PROMPT_SECONDS):
        raise HTTPException(
            422, detail=f"参考音频时长 {duration:.1f}s 超出 [{_MIN_PROMPT_SECONDS:.0f},{_MAX_PROMPT_SECONDS:.0f}]s"
        )
    state.prompt = VoicePrompt(samples, rate, body.prompt_text.strip())
    logger.info("声纹已更新: %.1fs@%dHz", duration, rate)
    return Response(status_code=204)


@app.delete("/v1/voice")
def clear_voice() -> Response:
    state.prompt = None
    logger.info("声纹已清除")
    return Response(status_code=204)


@app.post("/v1/synthesis")
def synthesize(body: SynthesisBody) -> Response:
    engine = state.engine
    if engine is None:
        raise HTTPException(503, detail="tts_model_not_configured")
    if state.prompt is None:
        raise HTTPException(409, detail="voice_not_loaded")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, detail="text 为空")
    with state.lock:  # 单模型串行；逐句请求天然顺序
        t0 = time.time()
        try:
            samples = engine.synthesize(text, state.prompt)
        except Exception as exc:
            logger.warning("合成失败 tts_id=%s: %s", body.tts_id[:8], exc)
            raise HTTPException(500, detail="synthesis_failed") from exc
    duration = samples.size / _SAMPLE_RATE
    logger.info("tts_id=%s text=%d字 audio=%.2fs gen=%.2fs rtf=%.2f",
                body.tts_id[:8], len(text), duration, time.time() - t0,
                (time.time() - t0) / max(duration, 0.01))
    return Response(content=pcm16_wav_bytes(samples), media_type="audio/wav")
