"""结构化 JSON 日志与敏感字段过滤（技术方案 §16.1）。

永不记录：Authorization、Cookie、X-Kindo-Playback-Grant、api_key、secret、token、原始音频。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

_REDACT_KEYS = {
    "authorization", "cookie", "x-kindo-playback-grant", "api_key", "apikey",
    "secret", "token", "password", "set-cookie", "proxy-authorization",
}


def _redact(value: object) -> object:
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for k, v in value.items():
            if str(k).lower() in _REDACT_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "module": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "kindo_extra", None)
        if extra:
            red = _redact(extra)
            if isinstance(red, dict):
                payload.update(red)
        if record.exc_info and record.exc_info[0]:
            payload["error"] = repr(record.exc_info[1])[:500]
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper()))
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "kindo.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **extra: object) -> None:
    logger.log(level, event, extra={"kindo_extra": extra})
