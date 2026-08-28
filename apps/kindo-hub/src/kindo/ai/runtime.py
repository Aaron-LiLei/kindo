"""统一 AI Runtime（架构 A-18；技术方案 §19.1）——run_ai 单一执行入口。

不引入 Agent 框架：无自主规划器、多 Agent 对话、长期 Memory、Workflow DSL。
模型复用 Provider 注册表 active model（A-12：不新增 per-profile 配置）；家长侧
任务不计入 ai_voice 预算（G-3 口径限儿童 AI_VOICE 互动）。结构化输出解析失败
重试一次，再失败抛 AiRuntimeError（任务 failed，不落库半成品建议）。
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..providers.llm import OpenAIChatCompletionsAdapter
from ..util import new_id
from .profiles import AgentProfile

logger = logging.getLogger("kindo.ai.runtime")


class AiRuntimeError(Exception):
    """LLM 不可用或结构化输出无法解析（任务级失败，不落库半成品）。"""


def parse_model_json(text: str) -> dict:
    """容错解析：剥离代码块围栏/前后杂文，取最外层 JSON 对象。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise AiRuntimeError("模型输出中未找到 JSON 对象")
    try:
        parsed = json.loads(s[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AiRuntimeError(f"模型输出 JSON 解析失败: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AiRuntimeError("模型输出不是 JSON 对象")
    return parsed


def _require_summary_parts(item: dict, where: str) -> None:
    summary = item.get("summary")
    if not isinstance(summary, dict) or not all(
            isinstance(summary.get(k), str) and summary.get(k)
            for k in ("why", "what", "impact")):
        raise AiRuntimeError(f"{where} 缺少三问（why/what/impact）")


def validate_output(parsed: dict, schema: dict) -> None:
    """轻量结构校验（不引入 jsonschema 依赖）：required 逐层存在、枚举合法。"""
    for key in schema.get("required", []):
        if key not in parsed:
            raise AiRuntimeError(f"模型输出缺少字段: {key}")
    props = schema.get("properties", {})
    for key, spec in props.items():
        if key not in parsed:
            continue
        if spec.get("type") == "array" and not isinstance(parsed[key], list):
            raise AiRuntimeError(f"模型输出字段 {key} 不是数组")
    kind = schema.get("x-kind", "curator")
    if kind == "curator":
        sugg_spec = ((props.get("suggestions") or {}).get("items") or {}).get("properties", {})
        ct_enum = (sugg_spec.get("change_type") or {}).get("enum")
        for item in parsed.get("suggestions", []):
            if not isinstance(item, dict):
                raise AiRuntimeError("suggestions 项不是对象")
            for key in ((props.get("suggestions") or {}).get("items") or {}).get("required", []):
                if key not in item:
                    raise AiRuntimeError(f"建议缺少字段: {key}")
            if ct_enum and item.get("change_type") not in ct_enum:
                raise AiRuntimeError(f"change_type 非法: {item.get('change_type')}")
            _require_summary_parts(item, "建议 summary")
    elif kind == "advisor_summary":
        for item in parsed.get("policy_suggestions", []):
            if not isinstance(item, dict):
                raise AiRuntimeError("policy_suggestions 项不是对象")
            if not isinstance(item.get("rules_patch"), dict) or not item.get("rules_patch"):
                raise AiRuntimeError("规则建议缺少 rules_patch")
            _require_summary_parts(item, "规则建议 summary")
    elif kind == "advisor_coverage":
        modality_enum = (((props.get("gaps") or {}).get("items") or {})
                         .get("properties", {}).get("modality", {}).get("enum"))
        for item in parsed.get("gaps", []):
            if not isinstance(item, dict):
                raise AiRuntimeError("gaps 项不是对象")
            if not isinstance(item.get("topic"), str) or not item.get("topic"):
                raise AiRuntimeError("内容缺口缺少 topic")
            if modality_enum and item.get("modality") not in modality_enum:
                raise AiRuntimeError(f"modality 非法: {item.get('modality')}")
            _require_summary_parts(item, "内容缺口 summary")


class LLMRuntime:
    """run_ai 执行器：active model 解析 + LLM 调用 + strict 输出校验。"""

    def __init__(self, adapter: OpenAIChatCompletionsAdapter,
                 db_session_factory, provider_registry):
        self._adapter = adapter
        self._db = db_session_factory
        self._registry = provider_registry

    def _active_provider(self):
        """与 AppState.active_model 同语义：app_setting 指定 → 第一个启用项。"""
        from ..models import AppSetting

        with self._db() as session:
            row = session.get(AppSetting, "active_model")
            if row is not None:
                pid = (row.value_json or {}).get("provider_id")
                view = self._registry.get(pid or "")
                if view is not None and view.enabled:
                    return view
        for view in self._registry.all():
            if view.enabled:
                return view
        return None

    def ready(self) -> bool:
        return self._active_provider() is not None

    def run_ai(self, profile: AgentProfile, context_text: str,
               output_schema: dict | None = None) -> dict:
        """§19.1 最小实现：单轮、结构化输出；上下文已由 Context Builder 组装。
        output_schema 可按任务覆盖（同一 Profile 的摘要/缺口任务结构不同）。"""
        provider = self._active_provider()
        if provider is None:
            raise AiRuntimeError("未配置可用的 LLM Provider")
        schema = output_schema or profile.output_schema
        schema_text = json.dumps(schema, ensure_ascii=False)
        messages = [
            {"role": "system", "content": (
                f"{profile.system_instruction}\n输出 JSON 结构：\n{schema_text}")},
            {"role": "user", "content": f"以下是本批内容的资料（JSON）：\n{context_text}"},
        ]
        last_error: AiRuntimeError | None = None
        for _attempt in (1, 2):  # 解析失败重试一次
            text = asyncio.run(self._collect(provider, messages))
            try:
                parsed = parse_model_json(text)
                validate_output(parsed, schema)
                return parsed
            except AiRuntimeError as exc:
                last_error = exc
                logger.warning("run_ai 输出校验失败（profile=%s）: %s",
                               profile.profile_id, exc)
        raise last_error or AiRuntimeError("模型输出无法解析")

    async def _collect(self, provider, messages: list[dict]) -> str:
        chunks: list[str] = []
        async for ev in self._adapter.generate(
                provider, messages, None, new_id()):
            if ev.type == "error":
                raise AiRuntimeError(ev.error or "LLM 调用失败")
            if ev.type == "text_delta" and ev.text:
                chunks.append(ev.text)
        return "".join(chunks)
