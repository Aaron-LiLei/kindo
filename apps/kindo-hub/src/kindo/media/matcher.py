"""Identity Matcher（v0.3 决策三，阶段 2b）。

TMDB 检索 → 候选评分（exact / likely / fuzzy）→ Match Decision：
- exact 唯一命中 → 自动应用（match_status=auto，写 ExternalIdentity）；
- 其余 → 缓存 top-3 候选待家长确认（不得直接错误绑定）；
- 家长确认 → confirmed；确认无匹配 → no_match；二者永不被 refresh 覆盖（约束 15）。

不直接写 Canonical 值字段——元数据合并由 metadata.Normalizer 执行。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..models import ContentEntity, ExternalIdentity, MatchDecision
from ..util import new_id
from .parser import norm_title, parse_path_clues

logger = logging.getLogger("kindo.matcher")

REQUEST_INTERVAL = 0.25  # ≤4 rps（与刮削任务共用限速语义）
HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class Candidate:
    ref_id: str
    title: str
    original_title: str = ""
    first_air_date: str = ""
    poster_path: str = ""
    popularity: float = 0.0

    def to_json(self) -> dict:
        return {
            "ref_id": self.ref_id, "title": self.title,
            "original_title": self.original_title,
            "first_air_date": self.first_air_date,
            "poster_path": self.poster_path, "popularity": self.popularity,
        }


def make_client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT, follow_redirects=True)


def search_tmdb(client: httpx.Client, api_key: str, kind: str, query: str,
                language: str) -> list[Candidate]:
    """TMDB /3/search/{tv|movie} → 候选列表（原文与译文标题都保留）。"""
    r = client.get(
        f"/3/search/{'tv' if kind == 'tv' else 'movie'}",
        params={"api_key": api_key, "query": query, "language": language},
    )
    if r.status_code != 200:
        raise RuntimeError(f"TMDB {r.status_code}")
    out: list[Candidate] = []
    for item in (r.json().get("results") or [])[:10]:
        out.append(Candidate(
            ref_id=str(item.get("id")),
            title=item.get("name") or item.get("title") or "",
            original_title=item.get("original_name") or item.get("original_title") or "",
            first_air_date=item.get("first_air_date") or item.get("release_date") or "",
            poster_path=item.get("poster_path") or "",
            popularity=float(item.get("popularity") or 0),
        ))
    return out


def score_candidates(title_guess: str, candidates: list[Candidate],
                     year: int | None = None) -> list[tuple[Candidate, str]]:
    """评分排序：exact（归一化标题相等）> likely（互相包含）> fuzzy（popularity 序）。

    返回 (candidate, confidence) 列表，按 (confidence, popularity) 降序。
    """
    q = norm_title(title_guess)
    scored: list[tuple[Candidate, str]] = []
    for c in candidates:
        t = norm_title(c.title)
        ot = norm_title(c.original_title)
        if q and t and q == t:
            conf = "exact"
        elif q and ot and q == ot:
            conf = "exact"
        elif q and t and (q in t or t in q):
            conf = "likely"
        elif q and ot and (q in ot or ot in q):
            conf = "likely"
        else:
            conf = "fuzzy"
        scored.append((c, conf))
    order = {"exact": 0, "likely": 1, "fuzzy": 2}
    scored.sort(key=lambda x: (order[x[1]], -x[0].popularity))
    # 年份一致性提升置信度（同名不同作品区分）
    if year is not None:
        promoted = []
        for c, conf in scored:
            if conf == "likely" and c.first_air_date[:4] == str(year):
                promoted.append((c, "exact"))
            else:
                promoted.append((c, conf))
        scored = promoted
        scored.sort(key=lambda x: (order[x[1]], -x[0].popularity))
    return scored


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def run_match(session, entity: ContentEntity, client: httpx.Client,
              api_key: str, language: str, *, path_key: str | None = None,
              last_request: list[float] | None = None) -> str:
    """对单个 series/movie 实体执行匹配决策。返回 auto_applied / pending / skipped。

    confirmed / no_match 直接跳过（家长锚定，约束 15）；已有 identity 且
    match_status=auto 的也跳过（refresh 语义由批任务的 force 控制）。
    """
    import time

    if entity.entity_type not in ("series", "movie"):
        return "skipped"
    if entity.match_status in ("confirmed", "no_match"):
        return "skipped"
    if (session.query(ExternalIdentity)
            .filter(ExternalIdentity.entity_id == entity.id).first() is not None
            and entity.match_status == "auto"):
        return "skipped"

    kind = "tv" if entity.entity_type == "series" else "movie"
    query = entity.title
    year = None
    if path_key:
        clues = parse_path_clues(path_key)
        year = clues.year
        if clues.title_guess and norm_title(clues.title_guess) != norm_title(entity.title):
            query = entity.title  # 目录结构与家长命名不一致时以 Catalog 标题检索

    # 丢词重试（"小鼠波波 Maisy Mouse" → "小鼠波波"）
    attempts = [query]
    parts = query.split()
    while len(parts) > 1 and len(attempts) < 3:
        parts = parts[:-1]
        attempts.append(" ".join(parts))
    candidates: list[Candidate] = []
    for q in attempts:
        if not q:
            break
        if last_request is not None:
            wait = REQUEST_INTERVAL - (time.monotonic() - last_request[0])
            if wait > 0:
                time.sleep(wait)
            last_request[0] = time.monotonic()
        try:
            found = search_tmdb(client, api_key, kind, q, language)
        except Exception as exc:
            logger.warning("TMDB 检索失败 %s: %s", entity.title, exc)
            return "skipped"
        if found:
            candidates = found
            break
    if not candidates:
        session.add(MatchDecision(
            id=new_id(), entity_id=entity.id, provider="tmdb",
            candidate_json=None, confidence="none", decision="pending_saved",
            decided_by="auto", created_at=datetime.now(UTC)))
        entity.candidates_json = None
        return "pending"

    scored = score_candidates(query, candidates, year)
    best, conf = scored[0]
    exact_all = [c for c, k in scored if k == "exact"]
    if conf == "exact" and len({c.ref_id for c in exact_all}) == 1:
        # 唯一精确命中 → 自动应用
        _apply_identity(session, entity, best, conf, decided_by="auto")
        return "auto_applied"
    # 低置信：缓存 top-3 待确认（带置信度，供 Admin 候选标签渲染）
    entity.candidates_json = [
        {**c.to_json(), "confidence": conf} for c, conf in scored[:3]]
    entity.match_status = "none"
    session.add(MatchDecision(
        id=new_id(), entity_id=entity.id, provider="tmdb",
        candidate_json=best.to_json(), confidence=conf,
        decision="pending_saved", decided_by="auto",
        created_at=datetime.now(UTC)))
    return "pending"


def _apply_identity(session, entity: ContentEntity, cand: Candidate, conf: str,
                    *, decided_by: str) -> None:
    session.add(ExternalIdentity(
        id=new_id(), entity_id=entity.id, provider="tmdb", ref_id=cand.ref_id,
        matched_title=cand.title, created_at=datetime.now(UTC)))
    entity.match_status = "auto" if decided_by == "auto" else "confirmed"
    entity.candidates_json = None
    session.add(MatchDecision(
        id=new_id(), entity_id=entity.id, provider="tmdb",
        candidate_json=cand.to_json(), confidence=conf,
        decision="auto_apply" if decided_by == "auto" else "parent_confirm",
        decided_by=decided_by, created_at=datetime.now(UTC)))


def confirm_match(session, entity: ContentEntity, ref_id: str,
                  title: str = "", first_air_date: str = "", poster_path: str = "",
                  popularity: float = 0.0) -> None:
    """家长确认（Admin）：写 identity + confirmed，永不被 refresh 覆盖。"""
    session.query(ExternalIdentity).filter(
        ExternalIdentity.entity_id == entity.id).delete()
    cand = Candidate(ref_id=str(ref_id), title=title or entity.title,
                     first_air_date=first_air_date, poster_path=poster_path,
                     popularity=popularity)
    _apply_identity(session, entity, cand, "exact", decided_by="parent")


def mark_no_match(session, entity: ContentEntity) -> None:
    """家长确认无匹配：no_match 状态，批任务不再自动尝试（force 除外）。"""
    entity.match_status = "no_match"
    entity.candidates_json = None
    session.add(MatchDecision(
        id=new_id(), entity_id=entity.id, provider="tmdb",
        candidate_json=None, confidence="none",
        decision="parent_no_match", decided_by="parent",
        created_at=datetime.now(UTC)))
