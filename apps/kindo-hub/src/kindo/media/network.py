"""SMB / WebDAV StorageProvider（PRD v0.2.3 MED-003 P0，技术方案 v0.2.2 §11.1）。

- SMB：smbprotocol（SMB2/3）。凭据经 register_session 绑定到服务端连接池。
- WebDAV：httpx（PROPFIND 列表 / HEAD 属性 / GET+Range 读取，Basic 认证）。
- 凭据写-only：密码仅存 media_mount.secret_json；任何 API 只返回
  credentials_configured；日志脱敏已覆盖 password 字段。
- NFS 不做用户态实现（宿主机挂载后按本地源使用，架构 v0.2.3 §4）。
"""
from __future__ import annotations

import io
import logging
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlparse

import httpx
from defusedxml import ElementTree as SafeElementTree

from .storage import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, StorageObject

logger = logging.getLogger("kindo.storage.network")

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0


class SmbStorageProvider:
    """SMB2/3 网络源。mount_id 即注册 id；path_key 为共享内相对 POSIX 路径。"""

    def __init__(self, mount_id: str, host: str, share: str, sub_path: str = "",
                 port: int = 445, username: str = "", password: str = "",
                 read_only: bool = True):
        import smbclient  # smbprotocol 高层 API

        self._smbclient = smbclient
        self.mount_id = mount_id
        self.host = host
        self.port = port
        self.share = share
        self.sub_path = (sub_path or "").strip("/")
        self.username = username
        self.password = password
        self.read_only = read_only

    # ---------- 连接 ----------

    def _register(self) -> None:
        self._smbclient.register_session(
            self.host, username=self.username or None,
            password=self.password or None, port=self.port)

    def check_connectivity(self) -> None:
        """创建挂载时的连通校验：列出根目录（失败抛异常）。"""
        self._register()
        self._smbclient.listdir(self._unc(""))

    def _unc(self, path_key: str) -> str:
        rel = (path_key or "").strip("/").replace("/", "\\")
        base = "\\\\" + self.host + "\\" + self.share
        return base + "\\" + rel if rel else base

    # ---------- 通用接口（与 LocalMountedDirectoryProvider 对齐） ----------

    def _walk_objects(self):
        """listdir + 一次 stat 产出对象（stat 结果同时用于目录判定与 size/mtime）。"""
        queue = [self.sub_path] if self.sub_path else [""]
        while queue:
            current = queue.pop(0)
            prefix = f"{current}/" if current else ""
            for entry in self._smbclient.listdir(self._unc(current)):
                if entry in (".", "..") or entry.startswith("."):
                    continue
                full = f"{prefix}{entry}"
                st = self._smbclient.stat(self._unc(full))
                if st.st_file_attributes is not None and bool(
                        st.st_file_attributes & 0x10):  # FILE_ATTRIBUTE_DIRECTORY
                    queue.append(full)
                else:
                    yield StorageObject(path_key=full, name=entry,
                                        size=int(st.st_size), mtime_ms=int(st.st_mtime * 1000))

    def _to_object(self, path_key: str) -> StorageObject:
        st = self._smbclient.stat(self._unc(path_key))
        return StorageObject(
            path_key=path_key, name=path_key.rsplit("/", 1)[-1],
            size=int(st.st_size), mtime_ms=int(st.st_mtime * 1000),
        )

    def list_videos(self):
        self._register()  # 幂等：重启恢复后进程内可能尚未注册会话
        for obj in self._walk_objects():
            if "." + obj.name.rsplit(".", 1)[-1].lower() in VIDEO_EXTENSIONS:
                yield obj

    def dir_listing(self, path_key: str) -> list[tuple[str, bool, int, int]]:
        """单层目录列表 → (name, is_dir, size, mtime_ms)（增量剪枝 walk 用）。"""
        self._register()
        out: list[tuple[str, bool, int, int]] = []
        for entry in self._smbclient.listdir(self._unc(path_key)):
            if entry in (".", "..") or entry.startswith("."):
                continue
            st = self._smbclient.stat(self._unc(f"{path_key}/{entry}" if path_key else entry))
            is_dir = bool(st.st_file_attributes & 0x10) if st.st_file_attributes is not None else False
            out.append((entry, is_dir, st.st_file_size or 0, int(st.st_mtime * 1000)))
        return out

    def list_entries(self):
        """全量条目（扫描器构建目录索引用，见 storage.LocalMountedDirectoryProvider）。"""
        self._register()
        yield from self._walk_objects()

    def list_subtitles(self):
        self._register()
        for obj in self._walk_objects():
            if "." + obj.name.rsplit(".", 1)[-1].lower() in SUBTITLE_EXTENSIONS:
                yield obj

    def stat(self, path_key: str, timeout: float | None = None) -> StorageObject:
        self._register()
        return self._to_object(path_key)

    def open_range(self, path_key: str, start: int, length: int | None = None):
        self._register()
        f = self._smbclient.open_file(self._unc(path_key), mode="rb")
        f.seek(start)
        return f

    def read_text(self, path_key: str, limit_bytes: int = 5 * 1024 * 1024) -> str:
        self._register()
        with self._smbclient.open_file(self._unc(path_key), mode="rb") as f:
            data = f.read(limit_bytes)
        return data.decode("utf-8-sig", errors="replace")

    def sidecar_candidates(self, video: StorageObject):
        """网络源：返回 path_key（由 scanner 经 read_text 读取）。"""
        parent = video.path_key.rsplit("/", 1)[0] if "/" in video.path_key else ""
        stem = video.name.rsplit(".", 1)[0]
        file_key = f"{parent}/{stem}.kindo.yaml" if parent else f"{stem}.kindo.yaml"
        dir_key = f"{parent}/kindo.yaml" if parent else "kindo.yaml"
        return dir_key, file_key

    def health(self) -> dict:
        try:
            self.check_connectivity()
            ok = True
        except Exception as exc:
            logger.warning("SMB 健康检查失败 %s: %s", self.host, exc)
            ok = False
        return {"mount_id": self.mount_id, "type": "smb", "host": self.host,
                "share": self.share, "healthy": ok, "read_only": self.read_only}


class _HttpRangeReader:
    """WebDAV GET+Range 的只读流（供 _file_iter 分块读取）。"""

    def __init__(self, response: httpx.Response):
        self._response = response
        self._iter = response.iter_bytes(64 * 1024)
        self._buf = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            for c in self._iter:
                chunks.append(c)
            return b"".join(chunks)
        while len(self._buf) < size:
            try:
                self._buf += next(self._iter)
            except StopIteration:
                break
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def close(self) -> None:
        self._response.close()


class WebDavStorageProvider:
    """WebDAV 网络源。url 为服务根（如 http://nas:5005/dav），path_key 为其内相对路径。"""

    def __init__(self, mount_id: str, url: str, sub_path: str = "",
                 username: str = "", password: str = "", read_only: bool = True):
        self.mount_id = mount_id
        self.base = url.rstrip("/")
        # URL 基路径（如 http://host:5244/dav → "dav"）：部分服务器（OpenList 等）
        # 的 href 带该前缀，解析前统一剥掉；根挂载服务器（基路径为空）不受影响
        self._base_path = urlparse(self.base).path.strip("/")
        self.sub_path = (sub_path or "").strip("/")
        self.username = username
        self.password = password
        self.read_only = read_only
        self._client = httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            auth=(username, password) if username or password else None,
        )

    # ---------- 内部 ----------

    def _url_of(self, path_key: str) -> str:
        rel = "/".join(p for p in (self.sub_path, path_key or "") if p)
        return f"{self.base}/{quote(rel)}" if rel else self.base

    def check_connectivity(self) -> None:
        r = self._client.request("PROPFIND", self._url_of(""), headers={"Depth": "0"})
        if r.status_code in (207, 200):
            return
        # 可诊断错误（家长能照着改对）：地址不是 DAV 服务 / 凭据错 / 路径不存在
        hints = {
            405: "该地址不是 WebDAV 服务（OpenList/AList 的 WebDAV 端点通常是 "
                 "http://主机:端口/dav，不是网页管理页）",
            401: "账号或密码不正确",
            403: "账号无权访问该目录",
            404: "地址或子路径不存在",
        }
        hint = hints.get(r.status_code, "检查地址是否可达、是否为 WebDAV 服务")
        extra = f"；服务端返回：{r.text[:120]}" if r.status_code not in hints else ""
        raise ConnectionError(
            f"WebDAV 连接失败（HTTP {r.status_code} @ {self._url_of('')}）：{hint}{extra}")

    def _propfind(self, path_key: str = "") -> list[tuple[str, bool, int, int]]:
        """Depth:1 PROPFIND → 直接子项 (name, is_dir, size, mtime_ms)。"""
        r = self._client.request(
            "PROPFIND", self._url_of(path_key), headers={"Depth": "1"}, content=b"")
        if r.status_code != 207:
            raise ConnectionError(f"PROPFIND {r.status_code}")
        ns = {"d": "DAV:"}
        root = SafeElementTree.fromstring(r.text)
        requested = "/".join(x for x in (self.sub_path, path_key) if x).strip("/")
        children: list[tuple[str, bool, int, int]] = []
        for resp in root.findall("d:response", ns):
            href = unquote(resp.findtext("d:href", "", ns) or "").strip("/")
            if self._base_path and (href == self._base_path
                                    or href.startswith(self._base_path + "/")):
                href = href[len(self._base_path):].strip("/")
            if not href or href == requested:
                continue
            name = href[len(requested):].strip("/") if requested else href
            if not name or "/" in name:
                continue  # Depth:1 只取直接子项
            prop = resp.find("d:propstat/d:prop", ns)
            if prop is None:
                continue
            is_dir = prop.find("d:resourcetype/d:collection", ns) is not None
            size = int(prop.findtext("d:getcontentlength", "0", ns) or 0)
            mtime_ms = 0
            mtime_raw = prop.findtext("d:getlastmodified", "", ns)
            if mtime_raw:
                try:
                    mtime_ms = int(parsedate_to_datetime(mtime_raw).timestamp() * 1000)
                except Exception:
                    mtime_ms = 0
            children.append((name, is_dir, size, mtime_ms))
        return children

    def _walk_objects(self):
        """PROPFIND 已返回 size/mtime，直接产出对象，避免逐文件再发 HEAD。"""
        queue: list[str] = [""]
        while queue:
            current = queue.pop(0)
            prefix = f"{current}/" if current else ""
            for name, is_dir, size, mtime in self._propfind(current):
                full = f"{prefix}{name}"
                if is_dir:
                    queue.append(full)
                else:
                    yield StorageObject(path_key=full, name=name, size=size, mtime_ms=mtime)

    def _to_object(self, path_key: str, timeout: float | None = None) -> StorageObject:
        r = self._client.head(self._url_of(path_key), timeout=timeout)
        r.raise_for_status()
        size = int(r.headers.get("content-length", 0) or 0)
        mtime = 0
        lm = r.headers.get("last-modified")
        if lm:
            try:
                mtime = int(parsedate_to_datetime(lm).timestamp() * 1000)
            except Exception:
                mtime = 0
        return StorageObject(path_key=path_key,
                             name=path_key.rsplit("/", 1)[-1],
                             size=size, mtime_ms=mtime)

    # ---------- 通用接口 ----------

    def list_videos(self):
        for obj in self._walk_objects():
            if "." + obj.name.rsplit(".", 1)[-1].lower() in VIDEO_EXTENSIONS:
                yield obj

    def dir_listing(self, path_key: str) -> list[tuple[str, bool, int, int]]:
        """单层目录列表 → (name, is_dir, size, mtime_ms)（增量剪枝 walk 用）。"""
        return self._propfind(path_key)

    def list_entries(self):
        """全量条目（扫描器构建目录索引用，见 storage.LocalMountedDirectoryProvider）。"""
        yield from self._walk_objects()

    def list_subtitles(self):
        for obj in self._walk_objects():
            if "." + obj.name.rsplit(".", 1)[-1].lower() in SUBTITLE_EXTENSIONS:
                yield obj

    def stat(self, path_key: str, timeout: float | None = None) -> StorageObject:
        return self._to_object(path_key, timeout=timeout)

    def open_range(self, path_key: str, start: int, length: int | None = None):
        headers = {}
        end = "" if length is None else str(start + length - 1)
        sent_range = start > 0 or length is not None
        if sent_range:
            headers["Range"] = f"bytes={start}-{end}"
        req = self._client.build_request("GET", self._url_of(path_key), headers=headers)
        resp = self._client.send(req, stream=True)
        if sent_range and resp.status_code != 206:
            # 服务器忽略 Range 返回 200 全量：直接读会从文件头错位供字节
            resp.close()
            raise ConnectionError(
                f"WebDAV 服务器不支持 Range 请求（got {resp.status_code}，want 206）")
        resp.raise_for_status()
        return _HttpRangeReader(resp)

    def read_text(self, path_key: str, limit_bytes: int = 5 * 1024 * 1024) -> str:
        with self._client.stream("GET", self._url_of(path_key)) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            for chunk in r.iter_bytes(65536):
                buf.write(chunk)
                if buf.tell() >= limit_bytes:
                    break
        return buf.getvalue().decode("utf-8-sig", errors="replace")

    def sidecar_candidates(self, video: StorageObject):
        parent = video.path_key.rsplit("/", 1)[0] if "/" in video.path_key else ""
        stem = video.name.rsplit(".", 1)[0]
        file_key = f"{parent}/{stem}.kindo.yaml" if parent else f"{stem}.kindo.yaml"
        dir_key = f"{parent}/kindo.yaml" if parent else "kindo.yaml"
        return dir_key, file_key

    def health(self) -> dict:
        try:
            self.check_connectivity()
            ok = True
        except Exception as exc:
            logger.warning("WebDAV 健康检查失败 %s: %s", self.base, exc)
            ok = False
        return {"mount_id": self.mount_id, "type": "webdav", "url": self.base,
                "healthy": ok, "read_only": self.read_only}
