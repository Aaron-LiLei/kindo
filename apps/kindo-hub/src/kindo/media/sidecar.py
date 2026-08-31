"""Sidecar 元数据规范（技术方案 §7.4）。

YAML；目录级 kindo.yaml 作为该目录默认值，文件级 <视频同名>.kindo.yaml 优先于目录级；
10 字段：title / language / age_band / characters / themes / tags / series / course / poster。
安全：sidecar 文本按“非可信内容数据”处理，仅作元数据（§10.3）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("kindo.sidecar")

VALID_FIELDS = {
    "title", "language", "age_band", "characters", "themes", "tags",
    "series", "course", "poster",
    # v0.3（技术方案 §7.4）
    "entity_type", "content_class", "modality", "age_min", "age_max",
    "topics", "difficulty", "sequence_no", "repeatable",
    # v0.3.7（2026-08-31）：故事朗读文本（read_story 直接播报，不经 LLM 复述）
    "story_text",
}


@dataclass
class Sidecar:
    title: str | None = None
    language: str | None = None
    age_band: str | None = None
    characters: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    series_name: str | None = None
    season_no: int | None = None
    episode_no: int | None = None
    course_name: str | None = None
    chapter_no: int | None = None
    lesson_no: int | None = None
    # v0.3：内容类型（story/song 等）与正交维度声明（音频内容常用）
    entity_type: str | None = None
    content_class: str | None = None
    # 海报图文件名（相对该 sidecar 所在目录，如 "poster.jpg"）；扫描时缩放落入
    # /data/cache/posters/{media_id}.jpg（§13.2）。图片同样按非可信内容数据处理，仅作展示。
    poster_file: str | None = None
    # 故事朗读文本（story 实体专用，§7.4）：非可信内容数据——仅作为 read_story
    # 的朗读素材在服务端直接分句播报，不进入 LLM 上下文
    story_text: str | None = None


def _parse_one(data: dict, source: Path | None, sc: Sidecar) -> None:
    for key in ("title", "language", "age_band"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            setattr(sc, key, v.strip())
    for key in ("characters", "themes", "tags"):
        v = data.get(key)
        if isinstance(v, list):
            setattr(sc, key, [str(x).strip() for x in v if str(x).strip()])
        elif isinstance(v, str) and v.strip():
            setattr(sc, key, [v.strip()])
    series = data.get("series")
    if isinstance(series, dict):
        if isinstance(series.get("name"), str):
            sc.series_name = series["name"].strip()
        for k in ("season_no", "episode_no"):
            v = series.get(k)
            if isinstance(v, int):
                setattr(sc, k, v)
    course = data.get("course")
    if isinstance(course, dict):
        if isinstance(course.get("name"), str):
            sc.course_name = course["name"].strip()
        for k in ("chapter_no", "lesson_no"):
            v = course.get(k)
            if isinstance(v, int):
                setattr(sc, k, v)
    for key in ("entity_type", "content_class"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            setattr(sc, key, v.strip())
    poster = data.get("poster")
    if isinstance(poster, str) and poster.strip():
        sc.poster_file = poster.strip()
    story = data.get("story_text")
    if isinstance(story, str) and story.strip():
        sc.story_text = story.strip()


def sidecar_from_texts(texts: list[str]) -> Sidecar:
    """从文本内容列表构造 Sidecar（网络源经 provider.read_text 读取后使用）。"""

    sc = Sidecar()
    for text in texts:
        try:
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise ValueError("sidecar 根结构必须是映射")
            unknown = set(data) - VALID_FIELDS
            if unknown:
                logger.warning("sidecar 含未知字段（忽略）: %s", unknown)
            _parse_one(data, None, sc)
        except Exception as exc:
            logger.warning("sidecar 解析失败: %s", exc)
    return sc


def load_sidecar(dir_sidecar: Path | None, file_sidecar: Path | None) -> Sidecar:
    sc = Sidecar()
    for path in (dir_sidecar, file_sidecar):
        if path is None:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError("sidecar 根结构必须是映射")
            unknown = set(data) - VALID_FIELDS
            if unknown:
                logger.warning("sidecar %s 含未知字段（忽略）: %s", path, unknown)
            _parse_one(data, path, sc)
        except Exception as exc:  # 损坏的 sidecar 不阻塞扫描
            logger.warning("sidecar 解析失败 %s: %s", path, exc)
    return sc


def to_tags_json(sc: Sidecar) -> dict:
    return {
        "characters": list(sc.characters),
        "themes": list(sc.themes),
        "tags": list(sc.tags),
    }
