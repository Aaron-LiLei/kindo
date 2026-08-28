"""健康检查（技术方案 §16.2）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from sqlalchemy import text

from ..util import now_iso
from .deps import get_state

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live():
    return {"status": "live", "time": now_iso()}


@router.get("/health/ready")
async def ready(request: Request):
    state = get_state(request)

    # 同步 DB 探测与挂载探测（网络 IO）放线程池，避免阻塞事件循环
    def _sync_checks():
        db_ok = False
        try:
            with state.db.session() as s:
                s.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        mounts_ok = (all(m.get("healthy") for m in state.storage.health())
                     if state.config.media_mounts else True)
        return db_ok, mounts_ok, state.storage.health()

    db_ok, mounts_ok, mount_detail = await asyncio.to_thread(_sync_checks)
    asr = await state.asr.health()
    ready = db_ok and state.migrations_current and mounts_ok
    return {
        "status": "ready" if ready else "not_ready",
        "time": now_iso(),
        "checks": {
            "database": {"ready": db_ok, "writable": db_ok},
            "migrations": {"current": state.migrations_current},
            "media_mounts": {"ready": mounts_ok, "detail": mount_detail},
            # ASR/LLM 单独标 degraded，不拖垮 Media Ready（§16.2）
            "asr": {"status": "ready" if asr.get("ready") else "degraded",
                    "detail": {k: v for k, v in asr.items() if k != "error"}},
            "llm": {"status": "ready" if state.provider_registry.configured_count else "degraded",
                    "configured_providers": state.provider_registry.configured_count},
        },
    }
