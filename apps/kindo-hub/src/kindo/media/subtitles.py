"""字幕解析与标准化（技术方案 §10.1）。

外置 SRT / WebVTT → SubtitleSegment(start_ms, end_ms, text)；
输出端返回标准化 WebVTT（§9.4）。清理格式标签/重复空白/控制字符，不做 OCR。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_ASS_TAG_RE = re.compile(r"\{\\[^}]*\}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ParsedSubtitle:
    seq: int
    start_ms: int
    end_ms: int
    text: str


def _to_ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def clean_text(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    text = _ASS_TAG_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    out: list[str] = []
    for line in lines:
        if not out or out[-1] != line:  # 相邻重复行去重
            out.append(line)
    return "\n".join(out)


def parse_subtitle(content: str) -> list[ParsedSubtitle]:
    """解析 SRT 或 WebVTT 文本（按 cue 计数编号）。"""
    segments: list[ParsedSubtitle] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    seq = 0
    while i < len(lines):
        m = _TIME_RE.search(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        start_ms = _to_ms(g[0], g[1], g[2], g[3])
        end_ms = _to_ms(g[4], g[5], g[6], g[7])
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1
        text = clean_text("\n".join(text_lines))
        if text and end_ms > start_ms:
            seq += 1
            segments.append(ParsedSubtitle(seq=seq, start_ms=start_ms, end_ms=end_ms, text=text))
        i += 1
    segments.sort(key=lambda x: (x.start_ms, x.end_ms))
    for idx, seg in enumerate(segments, start=1):
        seg.seq = idx
    return segments


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


def to_webvtt(segments: list[ParsedSubtitle]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_fmt_ms(seg.start_ms)} --> {_fmt_ms(seg.end_ms)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)
