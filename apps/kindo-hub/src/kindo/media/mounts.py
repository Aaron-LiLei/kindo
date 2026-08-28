"""媒体来源管理（2026-08-25 全页面化决策：本地目录/SMB/WebDAV 统一页面管理）。

- 本地：config_json={"path": 服务器绝对路径}（须存在且为目录）。
- 网络：config_json 连接字段（无密码）+ secret_json 写-only。
- 存储 id：默认 page-<id>；收养的配置根保持原根 id（媒体记录无缝）。
- 停用=断开连接（资源保留）；删除=清除该来源入库资源（文件保留，自动备份）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .. import secretbox
from ..config import Config
from ..errors import invalid_request, not_found
from ..models import MediaMount
from ..util import new_id
from .network import SmbStorageProvider, WebDavStorageProvider
from .storage import LocalMountedDirectoryProvider, StorageRegistry

logger = logging.getLogger("kindo.mounts")


class MountService:
    def __init__(self, storage: StorageRegistry, db_session_factory, cfg: Config | None = None):
        self._storage = storage
        self._db = db_session_factory
        self._cfg = cfg

    # ---------- 查询 ----------

    def list_mounts(self, session: Session) -> dict:
        mounts = [
            self._mount_payload(r)
            for r in session.query(MediaMount)
            .filter(MediaMount.deleted_at.is_(None))
            .order_by(MediaMount.created_at).all()
        ]
        return {
            "mounts": mounts,
            "note": "本地目录与网络源均由页面管理；停用=资源保留不可播，删除=清除入库资源（文件保留）",
        }

    # ---------- 配置收养（升级路径，幂等） ----------

    def adopt_config_roots(self, session: Session) -> int:
        """配置文件仍声明 media_mounts 时收养为页面挂载（2026-08-25 决策：配置段废弃）。

        storage_id 保持原根 id，既有媒体记录无缝衔接；目录缺失仅告警跳过。
        """
        if not self._cfg or not getattr(self._cfg, "media_mounts", None):
            return 0
        adopted = 0
        for m in self._cfg.media_mounts:
            exists = (session.query(MediaMount)
                      .filter(MediaMount.storage_id == m.id).first())
            if exists is not None:
                continue
            if not m.path.is_dir():
                logger.warning("配置根 %s 目录不存在，跳过收养: %s", m.id, m.path)
                continue
            row = MediaMount(
                id=new_id(), storage_id=m.id, root_id="", sub_path="",
                label=m.id, read_only=m.read_only, active=True,
                source="adopted", mount_type="local",
                config_json={"path": str(m.path)},
            )
            session.add(row)
            session.flush()
            self._storage.register(LocalMountedDirectoryProvider(
                m.id, m.path, m.read_only))
            adopted += 1
        if adopted:
            session.commit()
            logger.warning("已收养 %s 个配置声明的外层根为页面挂载（media_mounts 配置段已废弃，"
                           "可从 kindo.yaml 中删除）", adopted)
        return adopted

    # ---------- 添加 / 修改 / 删除 ----------

    def resolve_mount_id(self, row: MediaMount) -> str:
        """存储注册 id：默认 page-<id>；收养行保持原根 id（媒体记录键不变）。"""
        return row.storage_id or f"page-{row.id}"

    def _local_path(self, row: MediaMount) -> Path:
        path = (row.config_json or {}).get("path")
        if path:
            return Path(str(path))
        # 旧数据回退：根内子目录挂载（根已被收养、以原 id 注册）
        if row.root_id and row.sub_path:
            root = self._storage.get(row.root_id)
            return (Path(root.root) / row.sub_path).resolve()
        raise invalid_request("本地来源缺少路径（旧数据请删除后重新添加）")

    def create(self, session: Session, label: str, read_only: bool = True, *,
               mount_type: str = "local", config: dict | None = None,
               secret: dict | None = None) -> dict:
        config = config or {}
        secret = secret or {}
        mount_id = new_id()

        if mount_type == "local":
            raw = str(config.get("path", "")).strip()
            if not raw:
                raise invalid_request("本地来源需要 path（服务器/容器内绝对路径）")
            abs_path = Path(raw)
            if not abs_path.is_absolute():
                raise invalid_request("path 必须是绝对路径（Docker 部署时为容器内路径，如 /media）")
            if not abs_path.is_dir():
                raise invalid_request(f"目录不存在或不可读：{raw}")
            row = MediaMount(
                id=mount_id, storage_id=None, root_id="", sub_path="",
                label=label or abs_path.name or mount_id,
                read_only=read_only, active=True, source="page", mount_type="local",
                config_json={"path": str(abs_path)},
            )
            session.add(row)
            session.flush()
            self._storage.register(LocalMountedDirectoryProvider(
                self.resolve_mount_id(row), abs_path, read_only))
        elif mount_type in ("smb", "webdav"):
            # probe_mode：range（默认，Range 反代只取元数据字节）/ skip / full
            mode = str(config.get("probe_mode") or "range")
            if mode not in ("range", "skip", "full"):
                raise invalid_request("probe_mode 只支持 range|skip|full")
            row = MediaMount(
                id=mount_id, label=label or f"{mount_type}-{mount_id[:8]}",
                read_only=True, active=True, source="page", mount_type=mount_type,
                config_json={**{k: v for k, v in config.items() if k != "password"},
                             "probe_mode": mode},
                secret_json=secretbox.encrypt_dict(secret),  # 密文落盘；写-only 不回显/不进日志
            )
            session.add(row)
            session.flush()
            provider = self._build_network_provider(
                self.resolve_mount_id(row), mount_type, config, secret)
            try:
                provider.check_connectivity()  # 创建时连通校验，失败即 400
            except Exception as exc:
                raise invalid_request(f"网络源连接失败：{exc}") from exc
            self._storage.register(provider)
        else:
            raise invalid_request(f"未知挂载类型：{mount_type}")
        session.commit()
        return self._mount_payload(row)

    def _build_network_provider(self, mount_id: str, mount_type: str,
                                config: dict, secret: dict):
        if mount_type == "smb":
            for field in ("host", "share"):
                if not config.get(field):
                    raise invalid_request(f"SMB 源缺少必填字段：{field}")
            return SmbStorageProvider(
                mount_id, host=str(config["host"]), share=str(config["share"]),
                sub_path=str(config.get("path", "")),
                port=int(config.get("port", 445)),
                username=str(config.get("username", "")),
                password=str(secret.get("password", "")),
            )
        if mount_type == "webdav":
            url = str(config.get("url", ""))
            if not url.startswith(("http://", "https://")):
                raise invalid_request("WebDAV url 必须以 http(s):// 开头")
            return WebDavStorageProvider(
                mount_id, url=url, sub_path=str(config.get("path", "")),
                username=str(config.get("username", "")),
                password=str(secret.get("password", "")),
            )
        raise invalid_request(f"未知挂载类型：{mount_type}")

    def update(self, session: Session, mount_id: str, *, label: str | None,
               read_only: bool | None, active: bool | None,
               path: str | None = None, config_patch: dict | None = None,
               password: str | None = None) -> dict:
        """编辑来源：显示名/只读/启停 + 本地路径 / 网络连接字段（密码写-only）。
        连接字段变化即重建 provider；媒体记录按 (storage_mount_id, path_key) 不变，
        改路径后重扫增量对齐（旧文件标 missing、新文件入库）。"""
        row = session.get(MediaMount, mount_id)
        if row is None or row.deleted_at is not None:
            raise not_found("挂载不存在")
        mtype = row.mount_type or "local"
        connection_changed = False
        if label is not None:
            row.label = label
        if read_only is not None:
            row.read_only = read_only
            if mtype == "local":
                connection_changed = True  # 本地只读变更同样需重注册
        if path is not None and mtype == "local":
            raw = path.strip()
            abs_path = Path(raw)
            if not abs_path.is_absolute():
                raise invalid_request("path 必须是绝对路径")
            if not abs_path.is_dir():
                raise invalid_request(f"目录不存在或不可读：{raw}")
            row.config_json = {"path": str(abs_path)}
            connection_changed = True
        if config_patch or password is not None:
            if mtype not in ("smb", "webdav"):
                raise invalid_request("本地来源请使用 path 字段")
            if config_patch:
                merged = dict(row.config_json or {})
                merged.update(config_patch)
                if "probe_mode" in merged and merged["probe_mode"] not in (
                        "range", "skip", "full"):
                    raise invalid_request("probe_mode 只支持 range|skip|full")
                row.config_json = merged
                connection_changed = config_patch.keys() != {"probe_mode"} or connection_changed
            if password is not None:
                secret = secretbox.decrypt_dict(row.secret_json)
                if password:
                    secret["password"] = password
                else:
                    secret.pop("password", None)
                row.secret_json = secretbox.encrypt_dict(secret)
                connection_changed = True
        if connection_changed and row.active:
            self._re_register(row)
        if active is not None and active != row.active:
            row.active = active
            if not active:
                self._storage.unregister(self.resolve_mount_id(row))
            else:
                self._re_register(row)
        session.commit()
        return self._mount_payload(row)

    def _re_register(self, row: MediaMount) -> None:
        """按当前行内容重建 provider 并注册（编辑/启用/启动恢复共用路径）。"""
        mtype = row.mount_type or "local"
        if mtype in ("smb", "webdav"):
            provider = self._build_network_provider(
                self.resolve_mount_id(row), mtype,
                row.config_json or {}, secretbox.decrypt_dict(row.secret_json))
            self._storage.register(provider)
        else:
            self._storage.register(LocalMountedDirectoryProvider(
                self.resolve_mount_id(row), self._local_path(row), row.read_only))

    def restore_active_mounts(self, session: Session) -> int:
        """Hub 启动时从 DB 恢复来源（active 且未软删）。

        不做连通/存在校验（暂时离线不应阻止 Hub 启动），失败仅告警跳过。
        """
        restored = 0
        rows = session.query(MediaMount).filter(
            MediaMount.active.is_(True), MediaMount.deleted_at.is_(None)
        ).all()
        for row in rows:
            try:
                self._re_register(row)
                restored += 1
            except Exception:
                logger.warning("来源恢复失败 id=%s type=%s", row.id, row.mount_type, exc_info=True)
        return restored

    def delete(self, session: Session, mount_id: str) -> dict:
        """删除来源并清除其入库资源（2026-08-25 产品决策：删除=资源一并删除）。
        文件本身不动；执行前自动备份数据库；挂载行软删（重新添加即可重建）。"""
        from datetime import UTC, datetime

        row = session.get(MediaMount, mount_id)
        if row is None or row.deleted_at is not None:
            raise not_found("挂载不存在")
        counts: dict = {}
        backup_path = None
        if self._cfg is not None:
            from .purge import backup_database, purge_mount_media

            backup_path = backup_database(Path(self._cfg.data_dir))
            counts = purge_mount_media(session, Path(self._cfg.data_dir),
                                       self.resolve_mount_id(row))
        row.deleted_at = datetime.now(UTC)
        row.active = False
        self._storage.unregister(self.resolve_mount_id(row))
        session.commit()
        return {
            "mount_id": mount_id, "deleted": True,
            "purged": counts,
            "backup": str(backup_path) if backup_path else None,
            "note": "该来源入库的媒体/观看记录/海报缓存已删除（文件保留）；已自动备份数据库",
        }

    def _mount_payload(self, row: MediaMount) -> dict:
        payload = {
            "mount_id": row.id, "root_id": row.root_id, "sub_path": row.sub_path,
            "label": row.label, "read_only": row.read_only, "active": row.active,
            "source": row.source, "mount_type": row.mount_type or "local",
            "storage_mount_id": self.resolve_mount_id(row),  # 媒体记录/scanner 使用的 id
            "deleted": row.deleted_at is not None,
        }
        if (row.mount_type or "local") == "local":
            payload["path"] = (row.config_json or {}).get("path")
        else:
            # 凭据写-only：config 不含密码；仅返回 credentials_configured
            payload["config"] = row.config_json or {}
            payload["probe_mode"] = (row.config_json or {}).get("probe_mode") or "range"
            secret = secretbox.decrypt_dict(row.secret_json)
            payload["credentials_configured"] = bool(secret.get("password"))
        return payload
