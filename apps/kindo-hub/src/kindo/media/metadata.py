"""Metadata Provider（TMDB）+ Normalizer（v0.3 决策三/四，阶段 2b）。

Provider 只产出标准化的原始字段；Normalizer 按 Canonical 六级合并优先级
（Parent locked > Parent explicit > Sidecar explicit > Confirmed Provider >
Auto Provider > Parser inferred）写入 content_entity 结构化列并记录
meta_provenance_json。写入只可覆盖同级或更低来源（技术方案 §7.5）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..models import ContentEntity

logger = logging.getLogger("kindo.metadata")

# 六级优先级（数值越大越高）
PROVENANCE_RANK = {
    "PARENT_LOCKED": 6,
    "PARENT_EXPLICIT": 5,
    "SIDECAR_EXPLICIT": 4,
    "CONFIRMED_PROVIDER": 3,
    "AUTO_PROVIDER": 2,
    "PARSER_INFERRED": 1,
}
# provenance.source → 级别
SOURCE_LEVEL = {
    "parent": "PARENT_EXPLICIT",       # locked 经 provenance.locked 表达
    "sidecar": "SIDECAR_EXPLICIT",
    "provider_confirmed": "CONFIRMED_PROVIDER",
    "provider": "AUTO_PROVIDER",
    "auto": "PARSER_INFERRED",
}


@dataclass(frozen=True)
class ProviderDetails:
    name: str = ""
    overview: str = ""
    release_date: str = ""   # first_air_date / release_date（YYYY-MM-DD）
    poster_path: str = ""
    backdrop_path: str = ""


def fetch_tmdb_details(client: httpx.Client, api_key: str, kind: str,
                       ref_id: str, language: str) -> ProviderDetails:
    r = client.get(
        f"/3/{'tv' if kind == 'tv' else 'movie'}/{ref_id}",
        params={"api_key": api_key, "language": language},
    )
    if r.status_code != 200:
        raise RuntimeError(f"TMDB details {r.status_code}")
    d = r.json()
    return ProviderDetails(
        name=d.get("name") or d.get("title") or "",
        overview=d.get("overview") or "",
        release_date=d.get("first_air_date") or d.get("release_date") or "",
        poster_path=d.get("poster_path") or "",
        backdrop_path=d.get("backdrop_path") or "",
    )


# ---------- Normalizer ----------

def _level_of(entity: ContentEntity, field: str) -> int:
    prov = (entity.meta_provenance_json or {}).get(field) or {}
    if prov.get("locked"):
        return PROVENANCE_RANK["PARENT_LOCKED"]
    return PROVENANCE_RANK.get(SOURCE_LEVEL.get(prov.get("source", ""), "auto"), 1)


def apply_with_provenance(entity: ContentEntity, field: str, value,
                          source: str) -> bool:
    """六级优先级写入（技术方案 §7.5）：越级写入被拒并返回 False。

    locked 字段等价 PARENT_LOCKED，任何非家长写入不可覆盖。
    """
    incoming = PROVENANCE_RANK[SOURCE_LEVEL[source]]
    current = _level_of(entity, field)
    if incoming < current:
        return False
    if incoming == current and current == PROVENANCE_RANK["PARENT_LOCKED"]:
        return False  # 家长锁定之间也互不覆盖（家长写入走 set_field_parent）
    setattr(entity, field, value)
    prov = dict(entity.meta_provenance_json or {})
    old = prov.get(field) or {}
    prov[field] = {
        "source": source,
        "updated_at": _now_iso(),
        "locked": bool(old.get("locked")) and source == "parent",
    }
    entity.meta_provenance_json = prov
    return True


def set_field_parent(session, entity: ContentEntity, field: str, value,
                     locked: bool) -> bool:
    """家长写入（Admin PATCH）：PARENT_EXPLICIT（或 locked=PARENT_LOCKED）。"""
    prov = dict(entity.meta_provenance_json or {})
    setattr(entity, field, value)
    prov[field] = {"source": "parent", "updated_at": _now_iso(), "locked": locked}
    entity.meta_provenance_json = prov
    return True


def normalize_provider_details(entity: ContentEntity, details: ProviderDetails,
                               *, confirmed: bool) -> dict:
    """把 Provider 详情合并进 entity（overview/release_date/language 缺省补齐）。

    confirmed=True 时来源级别为 CONFIRMED_PROVIDER，否则 AUTO_PROVIDER。
    返回 {field: applied|skipped} 供批任务统计。标题不合并（展示名以本地为准，
    决策四"TMDB 标题仅参照"）。"""
    source = "provider_confirmed" if confirmed else "provider"
    out: dict[str, str] = {}
    if details.overview:
        out["overview"] = "applied" if apply_with_provenance(
            entity, "overview", details.overview, source) else "skipped"
    if details.release_date:
        out["release_date"] = "applied" if apply_with_provenance(
            entity, "release_date", details.release_date, source) else "skipped"
    if not entity.language:
        entity.language = "zh-CN"  # 检索语言兜底（无 provenance，缺省级）
    return out


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
