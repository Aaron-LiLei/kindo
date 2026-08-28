"""Web Admin 最小认证（技术方案 §14.3）：单本地管理员、Argon2id、
opaque session（库存 hash+过期）、HttpOnly+SameSite=Strict Cookie、CSRF、
按源 IP+账号限速。首次初始化用 KINDO_ADMIN_BOOTSTRAP_TOKEN 或自动生成
一次性 token 写入 /data/bootstrap（设置密码后立即作废）。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Config
from ..errors import forbidden_admin, invalid_request, unauthorized_admin
from ..models import AdminSession, AdminUser
from ..security import constant_time_eq, hash_password, new_opaque_token, sha256_hex, verify_password
from ..util import new_id

logger = logging.getLogger("kindo.admin")

SESSION_TTL_HOURS = 24
LOGIN_RATE_LIMIT = 5  # 次（同 IP 同账号）
LOGIN_IP_RATE_LIMIT = 20  # 次（同 IP，防轮换账号绕过单账号限制）
LOGIN_RATE_WINDOW = 60  # 秒


class AdminAuthService:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()

    # ---------- bootstrap ----------

    def bootstrap_token_path(self) -> Path:
        return self._cfg.data_dir / "bootstrap" / "ADMIN_BOOTSTRAP_TOKEN"

    def ensure_bootstrap_material(self, has_admin: bool) -> str | None:
        """返回有效的 bootstrap token 来源说明；无管理员且无环境变量时生成一次性 token。"""
        if has_admin:
            return None
        if self._cfg.admin_bootstrap_token:
            return "env:KINDO_ADMIN_BOOTSTRAP_TOKEN"
        path = self.bootstrap_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            token = new_opaque_token(24)
            path.write_text(token, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            logger.info("已生成一次性 Admin bootstrap token，写入 %s（设置密码后作废）", path)
        return f"file:{path}"

    def _expected_bootstrap_token(self) -> str | None:
        if self._cfg.admin_bootstrap_token:
            return self._cfg.admin_bootstrap_token
        path = self.bootstrap_token_path()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return None

    def bootstrap(self, session: Session, username: str, password: str,
                  bootstrap_token: str) -> dict:
        with self._bootstrap_lock:  # 防并发双初始化（sync 端点跑线程池）
            if session.query(AdminUser).count() > 0:
                raise invalid_request("管理员已初始化，请直接登录")
            if len(username) < 3 or len(password) < 8:
                raise invalid_request("用户名至少 3 位，密码至少 8 位")
            expected = self._expected_bootstrap_token()
            if expected is None:
                raise forbidden_admin("缺少 bootstrap token（设置 KINDO_ADMIN_BOOTSTRAP_TOKEN 后重启）")
            if not bootstrap_token or not constant_time_eq(bootstrap_token, expected):
                raise forbidden_admin("bootstrap token 不正确")
            user = AdminUser(id=new_id(), username=username, password_hash=hash_password(password))
            session.add(user)
            session.commit()
        # 一次性 token 作废（§14.3：设置密码后立即作废）
        if not self._cfg.admin_bootstrap_token:
            try:
                self.bootstrap_token_path().unlink(missing_ok=True)
                self.bootstrap_token_path().parent.rmdir()
            except OSError:
                pass
        logger.info("管理员初始化完成 user=%s", username)
        return {"user_id": user.id, "username": user.username}

    # ---------- login / logout ----------

    def _rate_key(self, client_ip: str, username: str) -> str:
        return f"{client_ip}|{username}"

    def _check_rate(self, client_ip: str, username: str) -> None:
        now = time.monotonic()
        key = self._rate_key(client_ip, username)
        with self._lock:
            # 清理过期键，防长期运行内存缓慢增长
            if len(self._attempts) > 1000:
                self._attempts = {
                    k: [t for t in v if now - t < LOGIN_RATE_WINDOW]
                    for k, v in self._attempts.items()
                }
                self._attempts = {k: v for k, v in self._attempts.items() if v}
            ip_attempts = [t for t in self._attempts.get(client_ip, [])
                           if now - t < LOGIN_RATE_WINDOW]
            if len(ip_attempts) >= LOGIN_IP_RATE_LIMIT:
                raise forbidden_admin("登录尝试过于频繁，请稍后再试")
            attempts = [t for t in self._attempts.get(key, [])
                        if now - t < LOGIN_RATE_WINDOW]
            if len(attempts) >= LOGIN_RATE_LIMIT:
                raise forbidden_admin("登录尝试过于频繁，请稍后再试")
            attempts.append(now)
            ip_attempts.append(now)
            self._attempts[key] = attempts
            self._attempts[client_ip] = ip_attempts

    def _clear_rate(self, client_ip: str, username: str) -> None:
        """登录成功即清零失败计数（成功请求不应占用失败窗口）。"""
        with self._lock:
            self._attempts.pop(self._rate_key(client_ip, username), None)

    def login(self, session: Session, username: str, password: str, client_ip: str) -> dict:
        self._check_rate(client_ip, username)
        user = session.query(AdminUser).filter(AdminUser.username == username).one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            logger.warning("管理员登录失败 user=%s ip=%s", username, client_ip)
            raise unauthorized_admin("用户名或密码不正确")
        self._clear_rate(client_ip, username)
        session_id = new_opaque_token(32)
        csrf_token = new_opaque_token(24)
        row = AdminSession(
            id_hash=sha256_hex(session_id),
            user_id=user.id,
            csrf_token_hash=sha256_hex(csrf_token),
            expires_at=datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS),
        )
        session.add(row)
        session.commit()
        logger.info("管理员登录成功 user=%s ip=%s", username, client_ip)
        return {
            "session_id": session_id, "csrf_token": csrf_token,
            "expires_at": row.expires_at.isoformat(),
            "user": {"user_id": user.id, "username": user.username},
        }

    def logout(self, session: Session, session_id: str) -> None:
        row = session.get(AdminSession, sha256_hex(session_id))
        if row is not None:
            session.delete(row)
            session.commit()

    def change_password(self, session: Session, current_session_id: str,
                        current_password: str, new_password: str) -> str:
        """修改管理员密码；撤销该用户其余会话（当前会话保留），返回用户名。"""
        srow = self.authenticate(session, current_session_id)
        user = session.get(AdminUser, srow.user_id)
        if user is None:
            raise unauthorized_admin("会话无效，请重新登录")
        if not verify_password(current_password, user.password_hash):
            logger.warning("管理员改密失败（当前密码不正确）user=%s", user.username)
            raise invalid_request("当前密码不正确")
        if len(new_password) < 8:
            raise invalid_request("新密码至少 8 位")
        user.password_hash = hash_password(new_password)
        # 其余会话全部撤销（改密后其他浏览器需重新登录）
        for other in session.query(AdminSession).filter(
                AdminSession.user_id == user.id,
                AdminSession.id_hash != sha256_hex(current_session_id)).all():
            session.delete(other)
        session.commit()
        logger.info("管理员密码已修改 user=%s（其余会话已撤销）", user.username)
        return user.username

    def authenticate(self, session: Session, session_id: str) -> AdminSession:
        row = session.get(AdminSession, sha256_hex(session_id))
        if row is None or row.expires_at < datetime.now(UTC):
            raise unauthorized_admin()
        return row

    def verify_csrf(self, row: AdminSession, header_token: str | None) -> None:
        if not header_token or not constant_time_eq(sha256_hex(header_token), row.csrf_token_hash):
            raise forbidden_admin("CSRF 校验失败")
