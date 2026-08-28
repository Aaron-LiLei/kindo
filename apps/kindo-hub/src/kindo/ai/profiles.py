"""Agent Profile 注册表（架构 A-19；技术方案 §19.1）。

仅注册家长侧可执行 Profile：S1 交付 library_curator，family_advisor 随
Advisor 阶段（S2）加入。Profile 是代码内常量（A-12 配置最小化：不进配置
文件），包含 profile_id / system_instruction / tool_allowlist / context_policy /
output_schema 五要素；tool_allowlist 经 ai/tools.py 注册表解析，表外工具一律
拒绝（Tool Permission）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    system_instruction: str
    tool_allowlist: tuple[str, ...]
    context_policy: dict
    output_schema: dict


CURATOR_CHANGE_TYPES = (
    "add_topic", "add_character", "set_overview", "set_language",
    "add_artwork", "set_content_class", "set_age_range",
)

CURATOR_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["findings", "suggestions"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_id", "issue"],
                "properties": {
                    "entity_id": {"type": "string"},
                    "issue": {"type": "string"},
                },
            },
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_id", "change_type", "changes", "summary"],
                "properties": {
                    "entity_id": {"type": "string"},
                    "change_type": {"type": "string", "enum": list(CURATOR_CHANGE_TYPES)},
                    "changes": {"type": "object"},
                    "summary": {
                        "type": "object",
                        "required": ["why", "what", "impact"],
                        "properties": {
                            "why": {"type": "string"},
                            "what": {"type": "string"},
                            "impact": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}

CURATOR_SYSTEM_INSTRUCTION = """你是家庭媒体库的整理助手，帮助家长发现内容资料缺失与异常。
你只基于用户消息中给出的资料做判断，不臆测资料之外的任何事实。
输出规则：
- 只输出一个 JSON 对象，不输出任何其他文字或代码块标记。
- 顶层包含 findings 与 suggestions 两个数组。
- findings 记录无需（或无法由你）修改的观察：疑似重复或错误归组、分类可疑但证据不足、
  海报缺失但该内容没有本地视频文件等，issue 用家长能懂的中文短句描述。
- suggestions 只针对资料中显示"缺失"的字段提建议；已有值或 locked=true 的字段一律
  不要建议修改。change_type 只能取：
  add_topic（changes={"names":[...]},最多 5 个）、add_character（同上）、
  set_overview（changes={"overview":"..."}）、set_language（changes={"language":"zh-CN"}）、
  add_artwork（changes={"kind":"poster|backdrop|thumbnail|logo"}，仅当 has_local_primary_asset=true）、
  set_content_class（changes={"content_class":"ENTERTAINMENT|LEARNING|STORY|MUSIC|OTHER"}，
    仅当资料明显显示分类错误时才提）、
  set_age_range（changes={"age_min":3,"age_max":6}，仅当明显缺失或明显不当时才提）。
- summary 必须回答三问：why（为什么提出）、what（将补充或修改什么）、
  impact（对孩子使用可能有什么影响），用家长能懂的中文短句，不出现内部字段名。
- 不做心理、能力、人格或医学判断；不输出任何诊断类表述。"""

LIBRARY_CURATOR = AgentProfile(
    profile_id="library_curator",
    system_instruction=CURATOR_SYSTEM_INSTRUCTION,
    tool_allowlist=("read_library_audit_data",),
    # 数据最小化（§19.6）：Curator 只见目录/元数据/匹配/Artwork 状态，
    # 不见儿童观看历史、路径与凭据（白名单在 ai/tools.py 落实）
    context_policy={
        "data_scope": ["catalog", "canonical_metadata", "provenance_locked_flags",
                       "match_status", "artwork_status", "local_primary_asset_flag"],
        "exclude": ["path_key", "viewing_history", "credentials", "secrets"],
        "batch_size": 50,
    },
    output_schema=CURATOR_OUTPUT_SCHEMA,
)


ADVISOR_SYSTEM_INSTRUCTION = """你是家庭使用的分析助手，帮助家长了解孩子最近的使用情况、评估屏幕时间规则是否合适、发现家庭内容缺口。
你只基于用户消息中给出的聚合统计做判断，不臆测数据之外的事实。
红线：
- 只描述可观察行为（接触了哪些主题/媒介、时长结构、主动提问的主题、成长接力的接受与拒绝）；
- 绝不生成心理、能力、人格、注意力或医学判断，绝不用"成瘾/聪明/落后/多动"等评价性表述；
- 摘要用家长能懂的中文短句；不出现内部字段名、键名或 JSON 术语。
- 使用摘要任务输出 {"headlines": [...], "summary_text": [...], "policy_suggestions": [...]}：
  headlines 是概览卡片用的 3~5 条短句（只放值得家长关注的信息）；
  policy_suggestions 仅在有数据支持时给出（如某预算长期用满而另一媒介几乎未用），
  rules_patch 只输出要修改的字段（不输出完整规则），summary 回答三问
  （why 为什么建议 / what 将改变什么 / impact 对家庭使用的影响）。
- 内容缺口任务输出 {"headlines": [...], "gaps": [...]}：gaps 每项含 topic/modality/summary，
  只指出补充方向（哪种主题×形态×语言），不虚构统计中不存在的数据，不建议下载或获取任何资源。"""

_SUMMARY_OBJ = {
    "type": "object",
    "required": ["why", "what", "impact"],
    "properties": {
        "why": {"type": "string"},
        "what": {"type": "string"},
        "impact": {"type": "string"},
    },
}

ADVISOR_SUMMARY_SCHEMA = {
    "type": "object",
    "x-kind": "advisor_summary",
    "required": ["headlines", "summary_text", "policy_suggestions"],
    "properties": {
        "headlines": {"type": "array", "items": {"type": "string"}},
        "summary_text": {"type": "array", "items": {"type": "string"}},
        "policy_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rules_patch", "summary"],
                "properties": {
                    "rules_patch": {"type": "object"},
                    "summary": _SUMMARY_OBJ,
                },
            },
        },
    },
}

ADVISOR_COVERAGE_SCHEMA = {
    "type": "object",
    "x-kind": "advisor_coverage",
    "required": ["headlines", "gaps"],
    "properties": {
        "headlines": {"type": "array", "items": {"type": "string"}},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["topic", "modality", "summary"],
                "properties": {
                    "topic": {"type": "string"},
                    "modality": {"type": "string", "enum": ["VIDEO", "AUDIO"]},
                    "language": {"type": "string"},
                    "age_band": {"type": "string"},
                    "summary": _SUMMARY_OBJ,
                },
            },
        },
    },
}

FAMILY_ADVISOR = AgentProfile(
    profile_id="family_advisor",
    system_instruction=ADVISOR_SYSTEM_INSTRUCTION,
    tool_allowlist=("read_family_stats",),
    # 数据最小化（§19.6 / 架构 §7.2）：Advisor 默认只发聚合统计（分钟数/次数/比率），
    # 不发逐条观看日志、路径与凭据
    context_policy={
        "data_scope": ["aggregated_analytics", "interest_summary",
                       "transition_stats", "policy_snapshot", "catalog_coverage"],
        "exclude": ["per_record_viewing_logs", "path_key", "credentials", "secrets"],
        "window_days": 7,
    },
    output_schema=ADVISOR_SUMMARY_SCHEMA,
)

PROFILES: dict[str, AgentProfile] = {
    LIBRARY_CURATOR.profile_id: LIBRARY_CURATOR,
    FAMILY_ADVISOR.profile_id: FAMILY_ADVISOR,
}


def get_profile(profile_id: str) -> AgentProfile | None:
    return PROFILES.get(profile_id)
