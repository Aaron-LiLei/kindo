"""在线元数据管线批任务（v0.3 决策三，阶段 2d；重构自 2026-08-21 轻量海报刮削）。

流程：Identity Matcher（检索+评分+决策）→ Provider 详情（TMDB）→
Normalizer（六级优先级合并）→ Artwork（系列/电影 poster 单次转码）。

- confirmed / no_match 永不重试（约束 15）；force 仅重置 auto；
- 待确认（低置信）缓存 top-3 候选，家长在 Admin 确认；
- 速率 ≤4 rps、单飞行、进度内存态可查；TMDB 只是 Provider，不是事实库。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .. import secretbox
from ..config import Config
from ..models import AppSetting, ContentEntity, ExternalIdentity, Media
from .artwork import upsert_artwork
from .matcher import make_client, run_match
from .metadata import fetch_tmdb_details, normalize_provider_details

logger = logging.getLogger("kindo.scrape")

SETTING_KEY = "scrape"
DEFAULT_BASE_URL = "https://api.themoviedb.org"
DEFAULT_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
REQUEST_INTERVAL = 0.25  # ≤4 rps
HTTP_TIMEOUT = 15.0


def _make_client(base_url: str) -> httpx.Client:  # 测试注入锚点（MockTransport）
    return make_client(base_url)


@dataclass
class ScrapeStatus:
    state: str = "idle"  # idle / running / done / failed
    total: int = 0
    done: int = 0
    matched: int = 0      # 自动应用（exact 唯一）
    pending: int = 0      # 待家长确认
    no_hit: int = 0
    failed: int = 0
    current: str = ""
    started_at: str = ""
    finished_at: str = ""
    log_tail: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "state": self.state, "total": self.total, "done": self.done,
            "matched": self.matched, "pending": self.pending,
            "no_hit": self.no_hit, "failed": self.failed,
            "current": self.current, "started_at": self.started_at,
            "finished_at": self.finished_at, "log_tail": self.log_tail[-20:],
        }


class ScrapeService:
    """元数据管线批任务：配置（app_setting）+ 单飞行。"""

    def __init__(self, config: Config, session_factory) -> None:
        self.config = config
        self.db = session_factory
        self.status = ScrapeStatus()
        self._lock = threading.Lock()

    # ---------- 配置 ----------

    def get_config(self, session) -> dict:
        row = session.get(AppSetting, SETTING_KEY)
        cfg = dict(row.value_json) if row else {}
        return {
            "provider": "tmdb",
            "base_url": cfg.get("base_url", DEFAULT_BASE_URL),
            "image_base_url": cfg.get("image_base_url", DEFAULT_IMAGE_BASE_URL),
            "language": cfg.get("language", "zh-CN"),
            "api_key_configured": bool(secretbox.decrypt_str(cfg.get("api_key") or "")),
        }

    def save_config(self, session, *, base_url: str | None, image_base_url: str | None,
                    language: str | None, api_key: str | None) -> dict:
        row = session.get(AppSetting, SETTING_KEY)
        cfg = dict(row.value_json) if row else {}
        if base_url:
            cfg["base_url"] = base_url.strip().rstrip("/")
        if image_base_url:
            cfg["image_base_url"] = image_base_url.strip().rstrip("/")
        if language:
            cfg["language"] = language.strip()
        if api_key:
            cfg["api_key"] = secretbox.encrypt_str(api_key.strip())  # 密文落盘
        if row is None:
            row = AppSetting(key=SETTING_KEY, value_json=cfg)
            session.add(row)
        else:
            row.value_json = cfg
        session.commit()
        logger.info("元数据管线配置已更新 key_configured=%s", bool(cfg.get("api_key")))
        return self.get_config(session)

    # ---------- 批任务 ----------

    def start(self, *, force: bool = False) -> tuple[bool, dict]:
        with self._lock:
            if self.status.state == "running":
                return False, self.status.snapshot()
            self.status = ScrapeStatus(
                state="running",
                started_at=datetime.now(UTC).isoformat(),
            )
        thread = threading.Thread(
            target=self._run, args=(force,), daemon=True, name="kindo-scrape",
        )
        thread.start()
        return True, self.status.snapshot()

    def _targets(self, session, force: bool) -> list[dict]:
        """系列（有集）+ 散电影；confirmed/no_match 跳过（force 重置 auto）。"""
        targets: list[dict] = []
        q = session.query(ContentEntity).filter(
            ContentEntity.entity_type.in_(("series", "movie")))
        for ent in q.all():
            if ent.match_status in ("confirmed", "no_match"):
                continue
            has_identity = (
                session.query(ExternalIdentity)
                .filter(ExternalIdentity.entity_id == ent.id).first() is not None)
            if has_identity and not force:
                continue
            # path 线索（年份）取首个关联 asset
            from ..models import EntityAsset

            link = (session.query(EntityAsset)
                    .filter(EntityAsset.entity_id == ent.id).first())
            path_key = None
            if link is not None:
                m = session.get(Media, link.asset_id)
                if m is not None:
                    path_key = m.path_key
            targets.append({
                "entity": ent, "path_key": path_key,
                "display": ent.title,
            })
        return targets

    def _run(self, force: bool) -> None:
        try:
            with self.db.begin() as session:
                cfg = self.get_config(session)
                if not cfg["api_key_configured"]:
                    self.status.state = "failed"
                    self.status.log_tail.append("未配置 TMDB API Key")
                    return
                targets = self._targets(session, force)
            self.status.total = len(targets)
            self.status.log_tail.append(
                f"目标 {len(targets)} 个（系列 + 散电影；confirmed/no_match 除外）")
            client = _make_client(cfg["base_url"])
            last_request = [0.0]
            api_key = self._api_key()
            for t in targets:
                if self.status.state != "running":
                    break
                self.status.current = t["display"]
                entity: ContentEntity = t["entity"]
                try:
                    with self.db.begin() as session:
                        ent = session.get(ContentEntity, entity.id)
                        result = run_match(
                            session, ent, client, api_key, cfg["language"],
                            path_key=t["path_key"], last_request=last_request)
                        if result == "auto_applied":
                            self.status.matched += 1
                            self._apply_details_and_artwork(
                                session, ent, client, cfg, api_key, last_request)
                            self.status.log_tail.append(
                                f"命中 {t['display']}（自动应用）")
                        elif result == "pending":
                            self.status.pending += 1
                            self.status.log_tail.append(f"待确认 {t['display']}")
                        elif result == "skipped":
                            self.status.no_hit += 1
                        self.status.done += 1
                except Exception as exc:
                    self.status.failed += 1
                    self.status.log_tail.append(f"失败 {t['display']}: {exc}")
                    self.status.done += 1
                    logger.exception("元数据管线目标失败 %s", t["display"])
            client.close()
            self.status.state = "done"
            self.status.finished_at = datetime.now(UTC).isoformat()
            self.status.current = ""
            logger.info(
                "元数据管线完成 matched=%s pending=%s failed=%s",
                self.status.matched, self.status.pending, self.status.failed)
        except Exception:
            logger.exception("元数据管线任务异常")
            self.status.state = "failed"
            self.status.finished_at = datetime.now(UTC).isoformat()

    def _apply_details_and_artwork(self, session, entity: ContentEntity,
                                   client: httpx.Client, cfg: dict, api_key: str,
                                   last_request: list[float]) -> None:
        ident = (session.query(ExternalIdentity)
                 .filter(ExternalIdentity.entity_id == entity.id).first())
        if ident is None:
            return
        kind = "tv" if entity.entity_type == "series" else "movie"
        wait = REQUEST_INTERVAL - (time.monotonic() - last_request[0])
        if wait > 0:
            time.sleep(wait)
        last_request[0] = time.monotonic()
        details = fetch_tmdb_details(client, api_key, kind, ident.ref_id,
                                     cfg["language"])
        normalize_provider_details(entity, details, confirmed=False)
        # poster：下载 → 单次转码（不复制到每集，决策八）
        if details.poster_path:
            wait = REQUEST_INTERVAL - (time.monotonic() - last_request[0])
            if wait > 0:
                time.sleep(wait)
            last_request[0] = time.monotonic()
            img = client.get(f"{cfg['image_base_url']}/w500{details.poster_path}")
            if img.status_code == 200 and img.content:
                upsert_artwork(session, self.config, entity.id, "poster",
                               "provider", img.content)

    def _api_key(self) -> str:
        with self.db.begin() as session:
            row = session.get(AppSetting, SETTING_KEY)
            key = (row.value_json or {}).get("api_key", "") if row else ""
        return secretbox.decrypt_str(key)
