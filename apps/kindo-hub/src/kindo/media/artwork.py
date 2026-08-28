"""Artwork Manager（v0.3 决策八，阶段 2c）。

实体级四类图（poster/backdrop/thumbnail/logo），统一 FFmpeg 转码宽 480 JPEG，
落盘 /data/cache/artworks/{kind}/{entity_id}.jpg；artwork_asset 行记录来源与
锁定。parent 来源与 locked 永不被 provider refresh 覆盖（约束 15）。
系列 poster 单文件存储（不再复制到每集）；episode thumbnail（still）单独生成。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Config
from ..models import ArtworkAsset

logger = logging.getLogger("kindo.artwork")

ARTWORK_WIDTH = {"poster": 480, "backdrop": 640, "thumbnail": 320, "logo": 480}
FFMPEG_TIMEOUT = 30.0
MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024


def artwork_path(config: Config, entity_id: str, kind: str) -> Path:
    return Path(config.data_dir) / "cache" / "artworks" / kind / f"{entity_id}.jpg"


def _transcode(config: Config, src: Path, entity_id: str, kind: str) -> bool:
    target = artwork_path(config, entity_id, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part.jpg")
    cmd = [
        config.ffmpeg_path, "-y", "-loglevel", "error", "-i", str(src),
        "-vf", f"scale='min({ARTWORK_WIDTH.get(kind, 480)},iw)':-2",
        "-frames:v", "1", "-q:v", "5", "-f", "image2", str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            logger.warning("artwork 转码失败 %s/%s: %s", kind, entity_id,
                           proc.stderr.decode(errors="replace")[:200])
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(target)
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("artwork 转码异常 %s/%s: %s", kind, entity_id, exc)
        tmp.unlink(missing_ok=True)
        return False


def generate_from_bytes(config: Config, data: bytes, entity_id: str, kind: str) -> bool:
    if not data:
        return False
    tmp_dir = Path(config.data_dir) / "cache" / "artwork_src"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{entity_id}.{kind}.src"
    try:
        tmp.write_bytes(data)
        return _transcode(config, tmp, entity_id, kind)
    except OSError as exc:
        logger.warning("artwork 源写入失败 %s/%s: %s", kind, entity_id, exc)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def generate_from_video_frame(config: Config, src: Path, entity_id: str, kind: str) -> bool:
    """本地视频文件 → 实体图（家长 AI Artwork 建议的应用路径；从片头抽帧）。"""
    try:
        if not src.is_file():
            return False
        return _transcode(config, src, entity_id, kind)
    except OSError as exc:
        logger.warning("artwork 抽帧失败 %s/%s: %s", kind, entity_id, exc)
        return False


def upsert_artwork(session: Session, config: Config, entity_id: str, kind: str,
                   source: str, image_bytes: bytes) -> bool:
    """生成文件并登记 artwork_asset 行；parent/locked 已存在时不覆盖。"""
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
           .one_or_none())
    if row is not None and (row.locked or row.source == "parent"):
        return False  # 家长设置永不被覆盖
    if not generate_from_bytes(config, image_bytes, entity_id, kind):
        return False
    rel = str(artwork_path(config, entity_id, kind).relative_to(config.data_dir))
    if row is None:
        from ..util import new_id

        session.add(ArtworkAsset(
            id=new_id(), entity_id=entity_id, kind=kind, source=source,
            file_path=rel))
    else:
        row.source = source
        row.file_path = rel
    return True


def has_artwork(session: Session, entity_id: str, kind: str = "poster") -> bool:
    return (session.query(ArtworkAsset)
            .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
            .first() is not None)
