"""ffprobe 探测与 §1.2 兼容矩阵判定。"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("kindo.probe")

# 技术方案 §1.2 V0.1 direct play 目标矩阵（PoC 实测后冻结）
CONTAINER_PLAYABLE = {"mp4", "matroska,webm", "webm", "mov,mp4,m4a,3gp,3g2,mj2", "mov"}
VIDEO_CODECS_FULL = {"h264", "hevc"}
VIDEO_CODECS_DEVICE = {"av1"}          # 视设备解码器
AUDIO_CODECS_FULL = {"aac", "opus", "mp3", "flac"}
AUDIO_CODECS_DEVICE = {"ac3", "eac3"}  # 视设备解码器
EMBEDDED_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}
IMAGE_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}


@dataclass
class ProbeStream:
    index: int
    kind: str  # video|audio|subtitle
    codec: str
    language: str | None = None
    title: str | None = None


@dataclass
class ProbeResult:
    duration_ms: int
    container: str
    video_codec: str | None
    audio_streams: list[ProbeStream] = field(default_factory=list)
    subtitle_streams: list[ProbeStream] = field(default_factory=list)
    playable: bool = True
    notes: list[str] = field(default_factory=list)


def probe_media(path: Path | str, ffprobe_path: str = "ffprobe", timeout: float = 30.0) -> ProbeResult:
    cmd = [
        ffprobe_path, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)

    fmt = data.get("format", {})
    container = fmt.get("format_name", "")
    duration_ms = int(float(fmt.get("duration", 0) or 0) * 1000)

    audio: list[ProbeStream] = []
    subs: list[ProbeStream] = []
    video_codec: str | None = None
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        stream = ProbeStream(
            index=int(s.get("index", 0)),
            kind=kind or "other",
            codec=s.get("codec_name", "") or "",
            language=(s.get("tags", {}) or {}).get("language"),
            title=(s.get("tags", {}) or {}).get("title"),
        )
        if kind == "video" and video_codec is None:
            video_codec = stream.codec
        elif kind == "audio":
            audio.append(stream)
        elif kind == "subtitle":
            subs.append(stream)

    playable, notes = _judge(container, video_codec, audio)
    return ProbeResult(
        duration_ms=duration_ms, container=container, video_codec=video_codec,
        audio_streams=audio, subtitle_streams=subs, playable=playable, notes=notes,
    )


def _judge(container: str, video_codec: str | None, audio: list[ProbeStream]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    playable = True
    if container not in CONTAINER_PLAYABLE:
        playable = False
        notes.append(f"容器 {container} 不在 V0.1 兼容矩阵，不承诺可播放")
    if video_codec and video_codec not in VIDEO_CODECS_FULL | VIDEO_CODECS_DEVICE:
        playable = False
        notes.append(f"视频编码 {video_codec} 不在 V0.1 兼容矩阵")
    elif video_codec in VIDEO_CODECS_DEVICE:
        notes.append(f"视频编码 {video_codec} 视设备解码器支持情况")
    bad_audio = [a.codec for a in audio if a.codec and a.codec not in AUDIO_CODECS_FULL | AUDIO_CODECS_DEVICE]
    if bad_audio:
        playable = False
        notes.append(f"音频编码 {bad_audio} 不在 V0.1 兼容矩阵")
    for a in audio:
        if a.codec in AUDIO_CODECS_DEVICE:
            notes.append(f"音频编码 {a.codec} 视设备解码器支持情况")
    return playable, notes


def is_embedded_text_subtitle(codec: str) -> bool:
    return codec in EMBEDDED_TEXT_SUBTITLE_CODECS


def is_image_subtitle(codec: str) -> bool:
    return codec in IMAGE_SUBTITLE_CODECS


def mime_for_container(container: str) -> str:
    if container.startswith("matroska"):
        return "video/x-matroska"
    if container in ("mp3", "mpeg"):
        return "audio/mpeg"
    if container in ("flac",):
        return "audio/flac"
    if container in ("wav",):
        return "audio/wav"
    if container in ("mov,mp4,m4a,3gp,3g2,mj2", "mov") or "ipod" in container or "m4a" in container:
        return "audio/mp4" if "m4a" in container or "ipod" in container else "video/quicktime"
    if container.startswith("mov"):
        return "video/quicktime"
    if "webm" in container:
        return "video/webm"
    return "video/mp4"
