"""家长声音样本存储（PRD TTS-005~007 / 技术方案 §6.7）。

样本属家长生物特征：仅存 Hub 本地数据目录 <data_dir>/voice/，经家庭网络录入，
不进日志、不作为 LLM 上下文出站；删除即时生效（同步清除 kindo-tts 内存声纹）。
文件：prompt.wav（24kHz 单声道 PCM16，经 ffmpeg 转码）+ prompt_text.txt。
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("kindo.voice")

MIN_PROMPT_SECONDS = 3.0
MAX_PROMPT_SECONDS = 15.0
MAX_PROMPT_TEXT_CHARS = 200
_SAMPLE_RATE = 24000


class VoicePromptError(Exception):
    """样本校验失败（时长/文本/格式）。"""


@dataclass
class VoiceProfile:
    duration_seconds: float
    prompt_text: str
    sample_rate: int

    def public(self) -> dict:
        return {
            "configured": True,
            "duration_seconds": round(self.duration_seconds, 2),
            "sample_rate": self.sample_rate,
            "prompt_text": self.prompt_text,
        }


def voice_dir(cfg) -> Path:
    return Path(cfg.data_dir) / "voice"


class VoiceStore:
    """cfg 绑定的样本读写（供 TtsService 与 Admin API 共用）。"""

    def __init__(self, cfg):
        self._cfg = cfg
        self._dir = voice_dir(cfg)

    def exists(self) -> bool:
        return (self._dir / "prompt.wav").is_file() and (self._dir / "prompt_text.txt").is_file()

    def load(self) -> VoiceProfile | None:
        if not self.exists():
            return None
        try:
            with wave.open(str(self._dir / "prompt.wav"), "rb") as w:
                duration = w.getnframes() / max(w.getframerate(), 1)
                rate = w.getframerate()
            text = (self._dir / "prompt_text.txt").read_text(encoding="utf-8").strip()
            return VoiceProfile(duration_seconds=duration, prompt_text=text, sample_rate=rate)
        except Exception:
            logger.exception("声音样本读取失败，按未配置处理")
            return None

    def wav_base64(self) -> str | None:
        if not self.exists():
            return None
        import base64

        return base64.b64encode((self._dir / "prompt.wav").read_bytes()).decode("ascii")

    def wav_bytes(self) -> bytes | None:
        if not self.exists():
            return None
        return (self._dir / "prompt.wav").read_bytes()

    def prompt_text(self) -> str:
        return (self._dir / "prompt_text.txt").read_text(encoding="utf-8").strip()

    def fingerprint(self) -> str | None:
        """样本指纹（sha256），用于 kindo-tts 声纹重推去重。"""
        if not self.exists():
            return None
        return hashlib.sha256((self._dir / "prompt.wav").read_bytes()).hexdigest()[:32]

    def save(self, audio_bytes: bytes, prompt_text: str, ffmpeg_path: str = "ffmpeg") -> VoiceProfile:
        """转码并保存样本：任意浏览器录音格式 → 24kHz 单声道 PCM16 WAV。"""
        text = prompt_text.strip()
        if not text:
            raise VoicePromptError("prompt_text 不能为空")
        if len(text) > MAX_PROMPT_TEXT_CHARS:
            raise VoicePromptError(f"prompt_text 超过 {MAX_PROMPT_TEXT_CHARS} 字")
        self._dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self._dir, suffix=".rec", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)
        out_path = self._dir / "prompt.wav"
        try:
            cmd = [
                ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(tmp_path),
                "-ac", "1", "-ar", str(_SAMPLE_RATE), "-sample_fmt", "s16",
                str(out_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0 or not out_path.is_file():
                raise VoicePromptError(f"音频转码失败: {proc.stderr.strip()[:200]}")
            with wave.open(str(out_path), "rb") as w:
                duration = w.getnframes() / max(w.getframerate(), 1)
                rate = w.getframerate()
            if not (MIN_PROMPT_SECONDS <= duration <= MAX_PROMPT_SECONDS):
                out_path.unlink(missing_ok=True)
                raise VoicePromptError(
                    f"录音时长 {duration:.1f}s 超出 {MIN_PROMPT_SECONDS:.0f}~{MAX_PROMPT_SECONDS:.0f}s"
                )
            (self._dir / "prompt_text.txt").write_text(text, encoding="utf-8")
            logger.info("家长声音样本已保存: %.1fs@%dHz", duration, rate)
            return VoiceProfile(duration_seconds=duration, prompt_text=text, sample_rate=rate)
        finally:
            tmp_path.unlink(missing_ok=True)

    def delete(self) -> bool:
        """删除样本文件；返回是否确有样本被删。"""
        removed = False
        for name in ("prompt.wav", "prompt_text.txt"):
            p = self._dir / name
            if p.is_file():
                p.unlink(missing_ok=True)
                removed = True
        return removed
