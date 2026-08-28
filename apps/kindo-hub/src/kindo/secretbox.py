"""Secret 落盘静态加密（2026-08-26 工程治理闭环）。

技术方案 §12.2 定义了 Secret 的写-only/掩码/日志过滤语义；本模块在其下补齐
"服务端持久化"一层：LLM API Key、NAS 凭据、TMDB Key 等可逆 Secret 在 SQLite
中以 Fernet 密文存储，明文只存在于进程内存。

主密钥（优先级）：
1. 环境变量 KINDO_SECRET_KEY（urlsafe base64 的 32 字节 Fernet key）
2. <data_dir>/secret.key 首次启动自动生成（POSIX 0600；与 kindo.db 同级）

密文格式：字符串前缀 "k1:" + Fernet token；dict 密文为 {"_enc": "k1:..."}。
兼容：无前缀的存量明文读取时原样返回（老库平滑升级）；启动巡检
encrypt_legacy_secrets() 把明文行统一转为密文。密钥丢失时解密失败按空值处理
并记 CRITICAL 日志（家长重新录入即可，不影响其余数据）。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("kindo.secretbox")

ENC_PREFIX = "k1:"
ENV_KEY = "KINDO_SECRET_KEY"
KEY_FILENAME = "secret.key"

_lock = threading.Lock()
_fernet: Fernet | None = None


def init(data_dir: Path) -> None:
    """加载/生成主密钥。必须在任何加解密调用前执行（create_app 内接线）。"""
    global _fernet
    with _lock:
        raw = os.environ.get(ENV_KEY, "").strip()
        if raw:
            try:
                _fernet = Fernet(raw.encode())
                return
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"{ENV_KEY} 不是合法的 Fernet key（urlsafe base64 32 字节）") from exc
        key_path = data_dir / KEY_FILENAME
        if key_path.is_file():
            _fernet = Fernet(key_path.read_bytes().strip())
            return
        key = Fernet.generate_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o600)
        _fernet = Fernet(key)
        logger.info("secretbox 主密钥已生成 path=%s（请与 kindo.db 一并备份）", key_path)


def _fer() -> Fernet:
    if _fernet is None:
        raise RuntimeError("secretbox 未初始化（缺少 init() 调用）")
    return _fernet


def encrypt_str(value: str) -> str:
    """明文 → "k1:<token>"；空串原样返回（空=未配置，无密文必要）。"""
    if not value or value.startswith(ENC_PREFIX):
        return value
    return ENC_PREFIX + _fer().encrypt(value.encode()).decode()


def decrypt_str(stored: str | None) -> str:
    """密文/存量明文 → 明文；密钥失配按空值处理（重新录入语义）。"""
    if not stored:
        return ""
    if not stored.startswith(ENC_PREFIX):
        return stored
    try:
        return _fer().decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.critical("Secret 解密失败（主密钥变更或丢失）——该 Secret 需重新录入")
        return ""


def encrypt_dict(secret: dict) -> dict:
    if not secret:
        return {}
    blob = json.dumps(secret, ensure_ascii=False, sort_keys=True)
    return {"_enc": encrypt_str(blob)}


def decrypt_dict(stored: dict | None) -> dict:
    if not stored:
        return {}
    enc = stored.get("_enc")
    if not enc:
        return dict(stored)  # 存量明文 dict（兼容期）
    plain = decrypt_str(enc)
    if not plain:
        return {}
    try:
        out = json.loads(plain)
        return out if isinstance(out, dict) else {}
    except ValueError:
        logger.error("Secret dict 密文解析失败")
        return {}


def encrypt_legacy_secrets(session) -> int:
    """启动巡检：把历史明文 Secret 行转为密文（幂等）。返回转换行数。

    app_setting 的 "scrape" 键与 media/scrape.py SETTING_KEY 对应（避免循环导入
    不直接引用常量）。
    """
    from .models import AppSetting, LlmProviderRow, MediaMount

    n = 0
    for row in session.query(LlmProviderRow).all():
        if row.api_key and not row.api_key.startswith(ENC_PREFIX):
            row.api_key = encrypt_str(row.api_key)
            n += 1
    for row in session.query(MediaMount).all():
        if row.secret_json and "_enc" not in (row.secret_json or {}):
            row.secret_json = encrypt_dict(row.secret_json)
            n += 1
    setting = session.get(AppSetting, "scrape")
    if setting is not None:
        cfg = dict(setting.value_json or {})
        key = cfg.get("api_key")
        if key and not str(key).startswith(ENC_PREFIX):
            cfg["api_key"] = encrypt_str(str(key))
            setting.value_json = cfg
            n += 1
    if n:
        session.commit()
    return n
