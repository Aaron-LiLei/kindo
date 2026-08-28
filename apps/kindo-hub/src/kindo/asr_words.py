"""ASR 热词自动构建（ASR-005）：从本地内容元数据生成热词表。

词表供 kindo-asr 的 KINDO_ASR_HOTWORDS_FILE 消费（一行一词；默认输出
<data_dir>/hotwords.txt，compose 部署把 hub 数据卷只读挂给 asr 容器，
两侧指向同一文件）。家长仍可在文件中手工增补（重建按来源前缀保留人工行）。
来源：系列名 / 课程名 / 电影·故事·儿歌实体标题 / 角色 / 主题标签；
过滤过短、纯数字与纯符号词条；上限 300（热词过多会稀释束搜索收益）。
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from .config import Config
from .models import ContentEntity, Course, Media, Series

MAX_WORDS = 300
_WORD_MIN, _WORD_MAX = 2, 20
_BAD_RE = re.compile(r"^[\W_0-9]+$")  # 纯符号/数字（含全角）


def _clean(word: str) -> str | None:
    w = word.strip()
    if not (_WORD_MIN <= len(w) <= _WORD_MAX):
        return None
    if _BAD_RE.match(w):
        return None
    return w


def build_hotwords(session: Session) -> list[str]:
    """从库内元数据收集热词（顺序即优先级：系列 > 课程 > 作品 > 角色/主题）。"""
    words: list[str] = []

    def add(src: list[str]) -> None:
        for raw in src:
            w = _clean(raw)
            if w and w not in words:
                words.append(w)

    add([t for (t,) in session.query(Series.title).all()])
    add([t for (t,) in session.query(Course.title).all()])
    add([t for (t,) in session.query(ContentEntity.title)
         .filter(ContentEntity.entity_type.in_(("movie", "story", "song"))).all()])
    for m in session.query(Media.tags_json).all():  # type: ignore[attr-defined]
        tags = m[0] or {}
        add(tags.get("characters", []) + tags.get("themes", []))
    return words[:MAX_WORDS]


def hotwords_path(cfg: Config) -> Path:
    override = getattr(cfg, "asr_hotwords_out", "") or ""
    return Path(override) if override else cfg.data_dir / "hotwords.txt"


MANUAL_MARKER = "## manual（手工补写，重建保留）"


def write_hotwords(cfg: Config, session: Session) -> dict:
    """重建词表文件：内容来源行全量重生成；"## manual" 标记之后的手工行保留。"""
    path = hotwords_path(cfg)
    manual: list[str] = []
    if path.exists():
        in_manual = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == MANUAL_MARKER:
                in_manual = True
                continue
            if in_manual and line.strip():
                manual.append(line.strip())
    words = build_hotwords(session)
    content_lines = list(words)
    if manual:
        content_lines.append(MANUAL_MARKER)
        content_lines.extend(manual)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
    return {"path": str(path), "count": len(words), "manual_count": len(manual)}


def hotwords_status(cfg: Config) -> dict:
    path = hotwords_path(cfg)
    if not path.exists():
        return {"path": str(path), "exists": False}
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("##")]
    stat = path.stat()
    return {"path": str(path), "exists": True, "count": len(lines),
            "sample": lines[:10],
            "updated_at": stat.st_mtime}
