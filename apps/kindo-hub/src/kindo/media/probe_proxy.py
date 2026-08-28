"""本地 Range 反代（扫描探测优化，2026-08-25）。

ffprobe 不必读整个文件：MP4 的 moov 与 MKV 的 cues 通常只需头部+尾部少量
字节。本模块在 127.0.0.1 起一个极简 HTTP 服务，把 ffprobe 的 Range 请求
原样翻译为存储 provider 的 open_range——网络源首扫探测的传输量从"整文件"
（几十 MB/集）降到"元数据字节"（通常 < 2MB）。

仅监听回环地址；token 一次性（探测完即撤销），不暴露给任何外部网络。
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

logger = logging.getLogger("kindo.probe_proxy")

_CHUNK = 256 * 1024


class ProbeProxy:
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._server.daemon_threads = True
        self._port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever,
                         daemon=True, name="probe-proxy").start()

    # ---------- 扫描器侧 ----------

    def url_for(self, provider, path_key: str, size: int) -> str:
        """登记一次性 token，返回给 ffprobe 用的本地 URL。"""
        token = uuid4().hex
        with self._lock:
            self._entries[token] = {"provider": provider, "path": path_key, "size": size}
        return f"http://127.0.0.1:{self._port}/p/{token}/{unquote(path_key).rsplit('/', 1)[-1]}"

    def revoke(self, token: str) -> None:
        with self._lock:
            self._entries.pop(token, None)

    # ---------- HTTP 侧 ----------

    def _lookup(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            return self._entries.get(token)

    def _make_handler(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # 静默（ffprobe 正常 404 探测也高频）
                pass

            def _token(self) -> str:
                return urlparse(self.path).path.split("/")[2]

            def _serve(self, send_head_only: bool) -> None:
                entry = proxy._lookup(self._token())
                if entry is None:
                    self.send_error(404, "unknown or expired token")
                    return
                size = entry["size"]
                start, length = self._range(size)
                if start is None:
                    self.send_error(416, "invalid range")
                    return
                length = length if length is not None else size - start
                try:
                    reader = entry["provider"].open_range(entry["path"], start, length)
                except Exception:
                    self.send_error(502, "remote read failed")
                    return
                try:
                    self.send_response(206 if "Range" in self.headers else 200)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(length))
                    self.send_header(
                        "Content-Range", f"bytes {start}-{start + length - 1}/{size}")
                    self.end_headers()
                    if send_head_only:
                        return
                    remaining = length
                    while remaining > 0:
                        chunk = reader.read(min(_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # ffprobe 读够即断开，正常
                finally:
                    try:
                        reader.close()
                    except Exception:
                        pass

            def _range(self, size: int) -> tuple[int, int | None] | tuple[None, None]:
                """解析 Range 头 → (start, length)；无 Range 头 = 从 0 全量流。"""
                raw = self.headers.get("Range")
                if not raw:
                    return 0, None
                spec = raw.strip().split("=", 1)[-1]
                first, _, last = spec.partition("-")
                try:
                    if first == "":  # suffix：最后 N 字节
                        n = int(last)
                        return max(0, size - n), n
                    start = int(first)
                    end = int(last) if last else size - 1
                except ValueError:
                    return None, None
                if start >= size or end < start:
                    return None, None
                return start, min(end, size - 1) - start + 1

            def do_HEAD(self):
                entry = proxy._lookup(self._token())
                if entry is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(entry["size"]))
                self.end_headers()

            def do_GET(self):
                self._serve(send_head_only=False)

        return Handler


_proxy: ProbeProxy | None = None
_proxy_lock = threading.Lock()


def get_probe_proxy() -> ProbeProxy:
    """进程级单例（惰性启动；仅回环监听）。"""
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = ProbeProxy()
        return _proxy
