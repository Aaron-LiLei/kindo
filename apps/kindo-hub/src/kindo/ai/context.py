"""Context Builder（架构 §3.2；技术方案 §19.6 数据最小化）。

按 Profile 的 context_policy 白名单组装最小上下文；数据获取只能经
ai/tools.py 的只读工具（Tool Permission），本模块不直接查库。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .profiles import AgentProfile
from .tools import call_tool


def build_curator_batch_context(session: Session, profile: AgentProfile,
                                entity_ids: list[str], storage=None) -> str:
    """CATALOG_AUDIT 单批上下文：实体白名单字段的紧凑 JSON（无路径/凭据/历史）。"""
    rows = call_tool(profile, "read_library_audit_data", session,
                     entity_ids=entity_ids, storage=storage)
    return json.dumps({"entities": rows}, ensure_ascii=False)


def build_advisor_context(session: Session, profile: AgentProfile, *,
                          history=None, policy=None, playback=None) -> str:
    """USAGE_SUMMARY / CONTENT_COVERAGE 上下文：聚合统计的紧凑 JSON
    （无逐条观看日志、无路径、无凭据；§19.6）。"""
    stats = call_tool(profile, "read_family_stats", session,
                      history=history, policy=policy, playback=playback)
    return json.dumps({"family_stats": stats}, ensure_ascii=False)
