"""Provider Registry（2026-08-25 产品决策：全页面化）。

唯一事实来源 = llm_provider 表；配置文件声明的 Provider 仅在启动时**收养**进表
（一次性，与挂载收养同模式），之后与页面添加的完全等价——可编辑、可停用
（不被选为当前模型）、可删除（删除不复活：收养记录防重收养）。
api_key 写-only：仅返回 configured/masked_hint，永不回显明文。
"""
from __future__ import annotations

import threading

from .. import secretbox
from ..config import Config
from ..models import LlmProviderRow


class ProviderView:
    """统一视图：config 与 runtime 共同的只读形态。"""

    def __init__(self, provider_id: str, display_name: str, protocol: str,
                 base_url: str, model: str, api_key: str, source: str,
                 enabled: bool = True):
        self.id = provider_id
        self.display_name = display_name
        self.protocol = protocol
        self.base_url = base_url
        self.model = model
        self.api_key = api_key  # 服务端内部使用；任何 API 不得返回
        self.source = source  # config | page
        self.enabled = enabled  # 停用=不参与会话解析（密钥保留）

    def public(self) -> dict:
        return {
            "provider_id": self.id,
            "display_name": self.display_name,
            "protocol": self.protocol,
            "model": self.model,
            "base_url": self.base_url,
            "source": self.source,
            "enabled": self.enabled,
            "api_key_configured": bool(self.api_key),
            "api_key_hint": masked_hint(self.api_key),
            # 注意：绝不包含 api_key 本体（ADM-007 / §12.2 写-only）
        }


def masked_hint(key: str) -> str | None:
    if not key:
        return None
    tail = key[-4:] if len(key) > 8 else "****"
    head = key[:3] if len(key) > 8 else ""
    return f"{head}****{tail}"


class ProviderRegistry:
    def __init__(self, cfg: Config, db_session_factory):
        self._cfg = cfg
        self._db = db_session_factory
        self._lock = threading.Lock()
        self._views: dict[str, ProviderView] = {}
        self.reload()

    ADOPTED_KEY = "config_providers_adopted"

    def _adopt_locked(self, session) -> int:
        """收养（幂等，无锁版）：config 声明 → llm_provider 行。
        已收养 id（含被用户删除的）不重复收养——删除即永久；
        配置文件后续新增的 Provider 在下次 reload 时自动收养。"""
        from ..models import AppSetting

        row = session.get(AppSetting, self.ADOPTED_KEY)
        adopted = set((row.value_json if row else None) or [])
        n = 0
        for p in self._cfg.llm_providers:
            if p.id in adopted:
                continue
            exists = session.get(LlmProviderRow, p.id) is not None
            if not exists:
                from datetime import UTC, datetime

                now = datetime.now(UTC)
                session.add(LlmProviderRow(
                    id=p.id, display_name=p.display_name, protocol=p.protocol,
                    base_url=p.base_url, model=p.model,
                    api_key=secretbox.encrypt_str(p.api_key or ""),
                    created_at=now, updated_at=now,
                ))
            adopted.add(p.id)
            n += 1
        if row is None:
            row = AppSetting(key=self.ADOPTED_KEY, value_json=sorted(adopted))
            session.add(row)
        else:
            row.value_json = sorted(adopted)
        if n:
            session.commit()
        return n

    def adopt_config_providers(self, session) -> int:
        """启动收养入口（日志用；reload 内部亦会自动收养）。"""
        return self._adopt_locked(session)

    def reload(self) -> None:
        with self._lock:
            merged: dict[str, ProviderView] = {}
            with self._db() as session:  # 唯一事实来源：数据库
                self._adopt_locked(session)  # 配置新增自动收养（删除过的除外）
                for row in session.query(LlmProviderRow).all():
                    merged[row.id] = ProviderView(
                        row.id, row.display_name, row.protocol, row.base_url,
                        row.model, secretbox.decrypt_str(row.api_key), source="page",
                        enabled=bool(row.enabled),
                    )
            self._views = merged

    def all(self) -> list[ProviderView]:
        with self._lock:
            return list(self._views.values())

    def get(self, provider_id: str) -> ProviderView | None:
        with self._lock:
            return self._views.get(provider_id)

    @property
    def configured_count(self) -> int:
        """可用 Provider 数（停用不计——TV ai_available / /health llm 同源）。"""
        with self._lock:
            return sum(1 for v in self._views.values() if v.enabled)
