"""Kindo Hub — 配置加载。

层级（技术方案 §12.1）：环境变量/Secret 引用 > /config/kindo.yaml > 内置默认值。
配置文件中的 ${ENV_NAME} 占位在加载时解析；环境变量不存在则报配置错误。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

_DEFAULT_CONFIG_PATHS = ["KINDO_CONFIG", "/config/kindo.yaml", "kindo.yaml"]


class ConfigError(Exception):
    pass


def _resolve_env(value: Any, path: str = "") -> Any:
    if isinstance(value, str):
        m = _ENV_REF.match(value)
        if m:
            env = m.group(1)
            if env not in os.environ:
                raise ConfigError(f"配置 {path} 引用的环境变量 {env} 不存在")
            return os.environ[env]
        return value
    if isinstance(value, dict):
        return {k: _resolve_env(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v, path) for v in value]
    return value


class LLMProviderConfig:
    def __init__(self, raw: dict[str, Any]):
        self.id: str = raw["id"]
        self.display_name: str = raw.get("display_name", self.id)
        self.protocol: str = raw.get("protocol", "openai_chat_completions")
        self.base_url: str = raw["base_url"]
        self.model: str = raw["model"]
        self.api_key: str = raw.get("api_key", "")
        if self.protocol != "openai_chat_completions":
            raise ConfigError(f"V0.1 仅支持 openai_chat_completions（provider {self.id}）")


class MediaMountConfig:
    def __init__(self, raw: dict[str, Any]):
        self.id: str = raw["id"]
        self.path: Path = Path(raw["path"]).resolve()
        self.read_only: bool = raw.get("read_only", True)
        if not self.path.is_dir():
            raise ConfigError(f"媒体挂载 {self.id} 的目录不存在或不可读: {self.path}")


class Config:
    def __init__(self, raw: dict[str, Any], config_path: Path | None):
        self._raw = raw
        self.config_path = config_path

        server = raw.get("server", {})
        self.bind: str = server.get("bind", "0.0.0.0")
        self.port: int = int(server.get("port", 8090))
        self.instance_display_name: str = server.get("display_name", "Kindo Hub")
        self.mdns_enabled: bool = server.get("mdns_enabled", True)

        self.data_dir: Path = Path(os.environ.get("KINDO_DATA_DIR", raw.get("data_dir", "/data")))
        self.timezone: str = raw.get("timezone", "Asia/Shanghai")

        self.media_mounts: list[MediaMountConfig] = [
            MediaMountConfig(m) for m in raw.get("media_mounts", [])
        ]

        conv = raw.get("conversation", {})
        self.follow_up_seconds: int = int(conv.get("follow_up_seconds", 6))
        self.session_idle_seconds: int = int(conv.get("session_idle_seconds", 600))

        playback = raw.get("playback", {})
        self.progress_interval_seconds: int = int(playback.get("progress_interval_seconds", 5))
        self.lost_protection_seconds: int = int(playback.get("lost_protection_seconds", 120))
        self.realtime_grace_seconds: int = int(playback.get("realtime_grace_seconds", 10))

        rules = raw.get("rules", {})
        self.breakpoint_min_position_ms: int = int(rules.get("breakpoint_min_position_ms", 30_000))
        self.breakpoint_min_remaining_ms: int = int(rules.get("breakpoint_min_remaining_ms", 60_000))
        self.completion_ratio: float = float(rules.get("completion_ratio", 0.90))
        self.completion_tail_ms: int = int(rules.get("completion_tail_ms", 120_000))
        self.course_completion_ratio: float = float(rules.get("course_completion_ratio", 0.95))
        self.episode_count_ratio: float = float(rules.get("episode_count_ratio", 0.50))

        asr = raw.get("asr", {})
        self.asr_endpoint: str = asr.get("endpoint", "")
        self.asr_timeout_seconds: float = float(asr.get("timeout_seconds", 10.0))
        # 热词表输出路径（ASR-005 自动构建；缺省 <data_dir>/hotwords.txt，
        # compose 部署将该文件只读共享给 kindo-asr）
        self.asr_hotwords_out: str = str(asr.get("hotwords_out", "") or "")

        self.llm_connect_timeout: int = int(raw.get("llm_connect_timeout_seconds", 5))
        self.llm_first_event_timeout: int = int(raw.get("llm_first_event_timeout_seconds", 15))
        self.llm_total_timeout: int = int(raw.get("llm_total_timeout_seconds", 30))
        self.tool_default_timeout: int = int(raw.get("tool_default_timeout_seconds", 5))

        tools = raw.get("tools", {})
        self.ffprobe_path: str = tools.get("ffprobe_path", "ffprobe")
        self.ffmpeg_path: str = tools.get("ffmpeg_path", "ffmpeg")
        self.embedded_subtitle_extraction: bool = tools.get("embedded_subtitle_extraction", False)
        self.remote_probe_max_bytes: int = int(tools.get("remote_probe_max_bytes", 2 * 1024 * 1024 * 1024))

        self.llm_providers: list[LLMProviderConfig] = [
            LLMProviderConfig(p) for p in raw.get("llm_providers", [])
        ]

        self.admin_bootstrap_token: str | None = raw.get("admin_bootstrap_token") or os.environ.get(
            "KINDO_ADMIN_BOOTSTRAP_TOKEN"
        )
        self.embedded_subtitle_extraction_env = self.embedded_subtitle_extraction

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kindo.db"

    def provider(self, provider_id: str) -> LLMProviderConfig | None:
        for p in self.llm_providers:
            if p.id == provider_id:
                return p
        return None


def _find_config_file() -> tuple[dict[str, Any], Path | None]:
    for cand in _DEFAULT_CONFIG_PATHS:
        if cand in os.environ:
            path = Path(os.environ[cand])
            if path.is_file():
                return _load_yaml(path), path
            raise ConfigError(f"KINDO_CONFIG 指向的文件不存在: {path}")
        p = Path(cand)
        if p.is_file():
            return _load_yaml(p), p
    return {}, None


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件根结构必须是映射: {path}")
    return raw


def load_config() -> Config:
    raw, path = _find_config_file()
    raw = _resolve_env(raw)
    # 环境变量覆盖（优先级最高）
    if "KINDO_BIND" in os.environ:
        raw.setdefault("server", {})["bind"] = os.environ["KINDO_BIND"]
    if "KINDO_PORT" in os.environ:
        raw.setdefault("server", {})["port"] = int(os.environ["KINDO_PORT"])
    if "KINDO_DATA_DIR" in os.environ:
        raw["data_dir"] = os.environ["KINDO_DATA_DIR"]
    cfg = Config(raw, path)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "cache").mkdir(parents=True, exist_ok=True)
    return cfg
