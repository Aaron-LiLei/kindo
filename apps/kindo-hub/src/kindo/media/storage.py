"""Storage Provider 抽象与本地目录实现（技术方案 §11.1）。

path_key 是 Hub 内部标识（mount 内相对 POSIX 路径），绝不返回给 TV/LLM。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..config import ConfigError

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav"}


@dataclass
class StorageObject:
    path_key: str
    name: str
    size: int
    mtime_ms: int


class LocalMountedDirectoryProvider:
    """V0.1 唯一实现：config 已声明的本地挂载目录（默认只读）。"""

    def __init__(self, mount_id: str, root: Path, read_only: bool = True):
        self.mount_id = mount_id
        self.root = root
        self.read_only = read_only

    def _resolve(self, path_key: str) -> Path:
        p = (self.root / path_key).resolve()
        root = self.root.resolve()
        if root != p and root not in p.parents:
            raise ConfigError(f"path_key 越界: {path_key}")
        return p

    def list_videos(self) -> Iterator[StorageObject]:
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fn in filenames:
                if Path(fn).suffix.lower() in VIDEO_EXTENSIONS:
                    full = Path(dirpath) / fn
                    yield self._to_object(full)

    def list_subtitles(self) -> Iterator[StorageObject]:
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fn in filenames:
                if Path(fn).suffix.lower() in SUBTITLE_EXTENSIONS:
                    full = Path(dirpath) / fn
                    yield self._to_object(full)

    def list_entries(self) -> Iterator[StorageObject]:
        """全量条目（不过滤扩展名）。扫描器据此构建目录索引：
        sidecar/海报候选的存在性判断走索引，不再逐文件 stat（网络源=真实请求）。"""
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fn in filenames:
                if fn.startswith("."):
                    continue
                yield self._to_object(Path(dirpath) / fn)

    def sidecar_candidates(self, video: StorageObject) -> tuple[Path | None, Path | None]:
        """返回 (目录级 kindo.yaml, 文件级 <同名>.kindo.yaml)。"""
        video_path = self._resolve(video.path_key)
        dir_sidecar = video_path.parent / "kindo.yaml"
        file_sidecar = video_path.with_suffix("").with_suffix("")  # 去掉一层后缀
        file_sidecar = video_path.parent / (video_path.stem + ".kindo.yaml")
        return (
            dir_sidecar if dir_sidecar.is_file() else None,
            file_sidecar if file_sidecar.is_file() else None,
        )

    def sidecar_files(self) -> Iterator[tuple[str, Path]]:
        """枚举全部 sidecar：(mount_id 相对目录, path) —— 目录级。"""
        for dirpath, _dirnames, filenames in os.walk(self.root):
            if "kindo.yaml" in filenames:
                full = Path(dirpath) / "kindo.yaml"
                rel = full.parent.relative_to(self.root).as_posix()
                yield rel, full

    def stat(self, path_key: str) -> StorageObject:
        p = self._resolve(path_key)
        return self._to_object(p)

    def abs_path(self, path_key: str) -> Path:
        return self._resolve(path_key)

    def open_range(self, path_key: str, start: int, length: int | None = None) -> BinaryIO:
        p = self._resolve(path_key)
        f = open(p, "rb")
        f.seek(start)
        return f

    def read_text(self, path_key: str, limit_bytes: int = 5 * 1024 * 1024) -> str:
        p = self._resolve(path_key)
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            return f.read(limit_bytes)

    def health(self) -> dict:
        ok = self.root.is_dir()
        return {
            "mount_id": self.mount_id,
            "healthy": ok,
            "read_only": self.read_only,
            "root_exists": ok,
        }

    def _to_object(self, p: Path) -> StorageObject:
        st = p.stat()
        rel = p.relative_to(self.root).as_posix()
        return StorageObject(
            path_key=rel, name=p.name, size=st.st_size, mtime_ms=int(st.st_mtime * 1000)
        )


class StorageRegistry:
    def __init__(self, mounts: list[LocalMountedDirectoryProvider]):
        self._mounts = {m.mount_id: m for m in mounts}

    def get(self, mount_id: str) -> LocalMountedDirectoryProvider:
        if mount_id not in self._mounts:
            raise KeyError(mount_id)
        return self._mounts[mount_id]

    @property
    def mount_ids(self) -> list[str]:
        return list(self._mounts)

    def all(self) -> list[LocalMountedDirectoryProvider]:
        return list(self._mounts.values())

    def register(self, provider: LocalMountedDirectoryProvider) -> None:
        """运行时注册页面添加的挂载（ADM-010，立即生效）。"""
        self._mounts[provider.mount_id] = provider

    def unregister(self, mount_id: str) -> None:
        """注销挂载（停用/软删）；已入库媒体记录不受影响。"""
        self._mounts.pop(mount_id, None)

    def health(self) -> list[dict]:
        return [m.health() for m in self._mounts.values()]
