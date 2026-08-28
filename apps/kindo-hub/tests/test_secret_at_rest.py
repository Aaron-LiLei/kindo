"""Secret 落盘静态加密回归（2026-08-26 工程治理）。

覆盖：三处可逆 Secret（llm_provider.api_key / media_mount.secret_json /
app_setting scrape.api_key）密文落盘、API 掩码不回显、运行时解密可用、
存量明文平滑升级（读取兼容 + 启动巡检转密文）、密钥失配降级。
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from kindo import secretbox
from kindo.models import AppSetting, LlmProviderRow, MediaMount


def test_provider_api_key_encrypted_at_rest(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "display_name": "K", "base_url": "http://llm.local", "model": "m",
        "api_key": "sk-secret-key-123456",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key_configured"] is True
    assert "sk-secret-key-123456" not in json.dumps(body)  # 响应只有掩码 hint
    with env.db.session() as s:
        row = s.query(LlmProviderRow).one()
        assert row.api_key.startswith("k1:")
        assert "sk-secret-key-123456" not in row.api_key  # 库内无明文
    view = env.state.provider_registry.get(body["provider_id"])
    assert view is not None and view.api_key == "sk-secret-key-123456"  # 运行时解密

    # PATCH 换 key 同样密文落盘
    r = env.client.patch(f"/api/v1/admin/providers/{body['provider_id']}",
                         headers=env.admin_headers(), json={
                             "display_name": "K", "base_url": "http://llm.local",
                             "model": "m", "api_key": "sk-new-key-987654321"})
    assert r.status_code == 200
    with env.db.session() as s:
        row = s.query(LlmProviderRow).one()
        assert row.api_key.startswith("k1:")
        assert "sk-new-key-987654321" not in row.api_key
    assert env.state.provider_registry.get(body["provider_id"]).api_key == "sk-new-key-987654321"


def test_mount_secret_encrypted_and_payload_masked(env):
    with env.db.session() as s:
        s.add(MediaMount(
            id="m-sec1", storage_id=None, root_id="", sub_path="", label="网盘",
            read_only=True, active=False, source="page", mount_type="webdav",
            config_json={"url": "http://nas.local/dav", "username": "u"},
            secret_json=secretbox.encrypt_dict({"password": "nas-pass-123"}),
        ))
        s.commit()
        raw = s.get(MediaMount, "m-sec1").secret_json
        assert "_enc" in raw and raw["_enc"].startswith("k1:")
        assert "nas-pass-123" not in json.dumps(raw)
    payload = None
    with env.db.session() as s:
        payload = next(m for m in env.state.mounts.list_mounts(s)["mounts"]
                       if m["mount_id"] == "m-sec1")
    assert payload["credentials_configured"] is True
    assert "nas-pass-123" not in json.dumps(payload)  # 列表不回显


def test_scrape_api_key_encrypted(env):
    svc = env.state._extra["scrape"]
    with env.db.session() as s:
        svc.save_config(s, base_url=None, image_base_url=None,
                        language=None, api_key="tmdb-key-abcdef")
        raw = s.get(AppSetting, "scrape").value_json["api_key"]
        assert raw.startswith("k1:") and "tmdb-key-abcdef" not in raw
        cfg = svc.get_config(s)
        assert cfg["api_key_configured"] is True
        assert "api_key" not in cfg or not cfg["api_key"].startswith("tmdb")  # 不回显
    assert svc._api_key() == "tmdb-key-abcdef"  # 用时解密


def test_legacy_plaintext_rows_upgrade_and_read(env):
    """存量明文库：读取兼容（原样返回）+ 启动巡检统一转密文。"""
    now = datetime.now(UTC)
    with env.db.session() as s:
        s.add(LlmProviderRow(id="legacy", display_name="L", protocol="openai_chat_completions",
                             base_url="http://l", model="m", api_key="sk-legacy-plain",
                             created_at=now, updated_at=now))
        s.add(MediaMount(id="m-legacy", label="旧源", mount_type="smb",
                         config_json={"host": "h", "share": "s"},
                         secret_json={"password": "legacy-pass"}))
        s.add(AppSetting(key="scrape", value_json={"api_key": "legacy-tmdb"}))
        s.commit()

    # 兼容读取：明文原样返回（registry/挂载/刮削在巡检前也能工作）
    assert secretbox.decrypt_str("sk-legacy-plain") == "sk-legacy-plain"
    assert secretbox.decrypt_dict({"password": "legacy-pass"}) == {"password": "legacy-pass"}

    with env.db.session() as s:
        n = secretbox.encrypt_legacy_secrets(s)
    assert n == 3
    with env.db.session() as s:
        assert s.get(LlmProviderRow, "legacy").api_key.startswith("k1:")
        assert "_enc" in s.get(MediaMount, "m-legacy").secret_json
        assert s.get(AppSetting, "scrape").value_json["api_key"].startswith("k1:")
        # 幂等
        assert secretbox.encrypt_legacy_secrets(s) == 0
    env.state.provider_registry.reload()
    assert env.state.provider_registry.get("legacy").api_key == "sk-legacy-plain"


def test_key_material_and_failure_degrade(env, tmp_path, monkeypatch):
    # 密钥文件已生成且 roundtrip 正常
    assert (env.data_dir / "secret.key").is_file()
    assert secretbox.decrypt_str(secretbox.encrypt_str("x-1")) == "x-1"

    # 密钥失配：解密失败按空值处理（重新录入语义），不抛异常
    other = Fernet.generate_key()
    monkeypatch.setenv("KINDO_SECRET_KEY", other.decode())
    secretbox.init(tmp_path)
    token = secretbox.encrypt_str("v-2")
    monkeypatch.setenv("KINDO_SECRET_KEY", Fernet.generate_key().decode())
    secretbox.init(tmp_path)
    assert secretbox.decrypt_str(token) == ""

    # 环境变量优先于密钥文件；还原为 env 的密钥避免影响后续用例
    monkeypatch.delenv("KINDO_SECRET_KEY")
    secretbox.init(env.data_dir)
    assert secretbox.decrypt_str(secretbox.encrypt_str("y-3")) == "y-3"
    assert "KINDO_SECRET_KEY" not in os.environ
