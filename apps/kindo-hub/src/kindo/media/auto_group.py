"""媒体自动归组与类型推断（2026-08-20 库内容治理；2026-08-21 内容边界重写；
2026-08-24 v0.3 阶段 2a：纯解析层抽至 media/parser.py，本模块只保留归组树逻辑）。

对无 sidecar 声明、无家长修正归组的媒体，按目录结构推断合集归属与 media_type：
- 内容边界推导：从挂载根向下走，目录出现 ≥2 个含视频的内容子目录时视为分类
  节点并拆分；只有季/阶/编号分段等"容器目录"或直接视频时该目录成系列。
  单内容链下沉且子目录名是版本词（英文版/国语版…）或通用词（动画片/视频…）
  时，系列挂在链头（汪汪队/国语版/… → 系列"汪汪队"）。
- 集号优先取文件名/相对链编号目录的编号（第01集 / S01E05 / EP01 / 001_ / "02. "），
  无编号时按路径自然排序顺序分配
- 季号取系列目录以下「第N季 / S01 / Season N / 第N阶」目录名或文件名 SxxExx
优先级最低：sidecar / 家长修正一旦声明归组，自动归组即让位（media.auto_series_key 置空）。
解析规则本体见 media/parser.py；本模块不做任何存储访问，纯 path_key 推导。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# 纯解析层（阶段 2a 抽取；re-export 保持既有 import 兼容）
from .parser import (  # noqa: F401
    _CONTAINER_PATTERNS,
    _DIR_INDEX_PREFIX,
    _EP_DANJI,
    _EP_EP,
    _EP_LEADING,
    _EP_RANGE,
    _EP_SEASON_EP,
    _GENERIC_NAMES,
    _SEASON_PATTERNS,
    _STRUCT_MARK,
    _VERSION_WORDS,
    MAX_EPISODE_NO,
    _cn_season,
    _norm_text,
    clean_series_title,
    is_container_dir,
    parse_episode_no,
    parse_leading_no,
    parse_season_no,
)


@dataclass(frozen=True)
class AutoAssignment:
    """一条媒体基于目录结构的归组结果。"""

    series_key: str  # 系列目录 path_key，生命周期判定用
    series_title: str
    season_no: int | None
    episode_no: int


def _natural_key(path: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", path)]


def _display_title(dir_path: str) -> str | None:
    """目录的展示名：通用词/空 → 上位目录名；纯版本词 → 上位目录名 + 版本词。"""
    parts = dir_path.split("/") if dir_path else []
    for i in range(len(parts) - 1, -1, -1):
        base = clean_series_title(parts[i])
        if not base or base in _GENERIC_NAMES:
            continue  # 沿祖先链找有意义的名字
        if base in _VERSION_WORDS:
            for j in range(i - 1, -1, -1):
                parent = clean_series_title(parts[j])
                if parent and parent not in _GENERIC_NAMES and parent not in _VERSION_WORDS:
                    return f"{parent} {base}"
            return base
        return base
    return None


def compute_auto_groups(path_keys: Iterable[str]) -> dict[str, AutoAssignment]:
    """由 path_key 集合按「内容边界」推导全部自动归组结果。

    规则：目录出现 ≥2 个含视频的内容子目录 → 分类节点（各自成系列）；只有
    容器目录或直接视频 → 该目录成系列（子树 ≥2 条视频，否则散文件归电影）。
    单内容链下沉：子目录名是版本词/通用词时系列挂链头（继承上位名），否则
    子目录自己成系列。集号：文件名显式编号 > 相对链编号目录 > 自然排序顺序
    分配（显式编号允许同号重复——"精讲+原片"一类常见结构）。
    """
    keys = [k for k in path_keys if "/" in k]
    if not keys:
        return {}

    own: dict[str, list[str]] = {}
    child_names: dict[str, set[str]] = {}
    for k in keys:
        parts = k.split("/")
        for i in range(len(parts) - 1):
            child_names.setdefault("/".join(parts[:i]), set()).add(parts[i])
        own.setdefault("/".join(parts[:-1]), []).append(k)

    subtree: dict[str, int] = {}

    def _subtree(d: str) -> int:
        if d not in subtree:
            n = len(own.get(d, []))
            for c in child_names.get(d, ()):
                n += _subtree(f"{d}/{c}" if d else c)
            subtree[d] = n
        return subtree[d]

    def _join(d: str, c: str) -> str:
        return f"{d}/{c}" if d else c

    def _collect(d: str) -> list[str]:
        out = list(own.get(d, []))
        for c in child_names.get(d, ()):
            out.extend(_collect(_join(d, c)))
        return out

    out: dict[str, AutoAssignment] = {}

    def _emit(series_dir: str, title: str | None, videos: list[str]) -> None:
        if len(videos) < 2 or not title:
            return
        ordered = sorted(videos, key=_natural_key)
        rel_start = len(series_dir.split("/"))
        explicit: dict[str, int | None] = {}
        seasons: dict[str, int | None] = {}
        for k in ordered:
            parts = k.split("/")
            filename = parts[-1].rsplit(".", 1)[0]
            ep, fn_season = parse_episode_no(filename)
            if ep is None:
                for part in reversed(parts[rel_start:-1]):
                    ep = parse_leading_no(part)
                    if ep is not None:
                        break
            explicit[k] = ep
            season = fn_season
            if season is None:
                for part in reversed(parts[rel_start:-1]):  # 最深的季目录优先
                    season = parse_season_no(part)
                    if season is not None:
                        break
            seasons[k] = season
        used = {ep for ep in explicit.values() if ep is not None}
        next_free = 1
        for k in ordered:
            ep = explicit[k]
            if ep is None:
                while next_free in used:
                    next_free += 1
                ep = next_free
                used.add(ep)
            out[k] = AutoAssignment(
                series_key=series_dir, series_title=title,
                season_no=seasons[k], episode_no=ep,
            )

    def _resolve(d: str, head: str | None) -> None:
        ancestors = frozenset(
            _norm_text(clean_series_title(p))
            for p in ((head.split("/") if head else []) + (d.split("/") if d else []))
            if p
        )
        content: list[str] = []
        container_vids: list[str] = []
        for c in sorted(child_names.get(d, ())):
            if _subtree(_join(d, c)) <= 0:
                continue
            if is_container_dir(c, ancestors):
                container_vids.extend(_collect(_join(d, c)))
            else:
                content.append(c)
        loose = own.get(d, [])
        if not content:
            # 内容边界：系列挂链头（单链下沉）或本目录；子树含直接视频+容器后代
            series_dir = head if head is not None else d
            _emit(series_dir, _display_title(series_dir), _collect(d))
            return
        # 有内容子目录时，散视频与容器子目录视频并为父级系列（<2 条归电影）
        combined = loose + container_vids
        if len(combined) >= 2:
            _emit(d, _display_title(d), combined)
        if len(content) == 1 and not combined:
            c = content[0]
            cleaned = clean_series_title(c)
            if not cleaned or cleaned in _GENERIC_NAMES or cleaned in _VERSION_WORDS:
                _resolve(_join(d, c), head if head is not None else d)
            else:
                _resolve(_join(d, c), None)
        else:
            for c in content:
                _resolve(_join(d, c), None)

    _resolve("", None)
    return out


def apply_auto_group(session, media, assig: AutoAssignment | None, *,
                     declared: bool, type_edited: bool) -> str:
    """把推断结果落到一行 Media（Episode 绑定 + media_type + auto_series_key）。

    declared：已有 sidecar/家长修正归组（此时只清自动标记，不动绑定）；
    type_edited：家长已修正 media_type（自动推断不改写）。
    返回 grouped / rebound / released / cleared / kept 之一，供统计。
    """
    from .curation import remove_episode, upsert_episode

    if declared:
        if media.auto_series_key is not None:
            media.auto_series_key = None
            return "cleared"
        return "kept"
    if assig is not None:
        prev_key = media.auto_series_key
        if prev_key == assig.series_key:
            # 顺序分配的集号可能随目录内新增文件变化，无条件刷新编号与标题
            # （算法升级后同键系列的展示名也可能变化，按标题收敛到同一行）；
            # 绑定意外缺失（如外部删行）则重建
            upsert_episode(session, media, assig.series_title,
                           assig.season_no, assig.episode_no)
        else:
            upsert_episode(session, media, assig.series_title,
                           assig.season_no, assig.episode_no)
            media.auto_series_key = assig.series_key
        if not type_edited:
            media.media_type = "episode"
        if prev_key is None:
            return "grouped"
        return "kept" if prev_key == assig.series_key else "rebound"
    if media.auto_series_key is not None:
        # 目录结构变化（文件移动/合集缩到单文件）→ 解除自动归组
        remove_episode(session, media)
        media.auto_series_key = None
        if not type_edited and media.media_type == "episode":
            media.media_type = "movie"
        return "released"
    return "kept"


def rebuild_auto_groups(session, mount_id: str | None = None) -> dict:
    """对已入库媒体本地重算自动归组（不触碰任何存储源，零网络请求）。

    已有 Episode/Lesson 绑定但 auto_series_key 为空 = sidecar/家长修正建立，
    不受影响；其余按当前 path_key 重新推导。用于存量库一次性回填，或在
    目录结构整理后手动重算，免去整树重扫（网络源重扫=重新枚举全树）。
    """
    from collections import Counter

    from ..models import Episode, Lesson, Media

    q = session.query(Media)
    if mount_id:
        q = q.filter(Media.mount_id == mount_id)
    rows = q.all()
    assigs = compute_auto_groups(m.path_key for m in rows)
    lesson_media = {
        lm for (lm,) in session.query(Lesson.media_id).all()
    }
    ep_by_media = {ep.media_id: ep for ep in session.query(Episode).all()}

    stats: Counter = Counter()
    for m in rows:
        edited = m.parent_edited_json or {}
        ep = ep_by_media.get(m.id)
        declared = (
            "series" in edited or "course" in edited
            or m.id in lesson_media
            or (ep is not None and m.auto_series_key is None)
        )
        assig = assigs.get(m.path_key)
        result = apply_auto_group(session, m, assig,
                                  declared=declared, type_edited="media_type" in edited)
        stats[result] += 1
        # 声明归组但集号/季号仍是缺省占位（sidecar 只写了系列名的常见形态）→
        # 用文件名推断补齐；家长显式给过编号的尊重原值。精确对齐由下次扫描完成
        if declared and assig is not None and ep is not None:
            declared_series = edited.get("series")
            pinned = declared_series if isinstance(declared_series, dict) else {}
            if not pinned.get("episode_no") and ep.episode_no == 1 and assig.episode_no != 1:
                ep.episode_no = assig.episode_no
                stats["ep_no_filled"] += 1
            if (not pinned.get("season_no") and ep.season_no == 1
                    and assig.season_no is not None and assig.season_no != 1):
                ep.season_no = assig.season_no
                stats["season_no_filled"] += 1
    stats["processed"] = len(rows)
    session.commit()
    return dict(stats)
