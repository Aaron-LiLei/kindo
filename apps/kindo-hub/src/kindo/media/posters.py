"""扫描期缩略海报生成（技术方案 §13.2 /data/cache/posters 落实，2026-08-20 媒体库展示重构）。

来源优先级（均按“非可信内容数据”处理，仅作后台展示，§10.3）：
1. sidecar 显式声明 poster: <图片文件名>（相对视频所在目录）
2. 约定式同名图 <视频名>.jpg/.jpeg/.png（兼容常见刮削器产物）
3. 约定式目录图 poster.jpg / folder.jpg
4. FFmpeg 抽帧兜底（取 25% 时长处，仅本地挂载；网络源不为此下载视频）

统一经 FFmpeg CLI 缩放为宽 480px JPEG——扫描期一次性离线操作，非实时转码（约束 9）。
幂等：sidecar 源更新（mtime 变新）才重新生成；抽帧结果只在缺失时生成。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path, PurePosixPath

from ..config import Config

logger = logging.getLogger("kindo.posters")

POSTER_WIDTH = 480
FRAME_RATIO = 0.25  # 抽帧位置：25% 时长处（避开片头黑场，短视频亦适用）
_DEFAULT_POSTER_VARIANTS = 6
FFMPEG_TIMEOUT = 30.0
# sidecar 图片可能意外巨大，超过则拒绝读取（视频抽帧不受此限）
MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024


def poster_path(config: Config, media_id: str) -> Path:
    return Path(config.data_dir) / "cache" / "posters" / f"{media_id}.jpg"


def default_poster(config: Config, seed: str | None = None) -> Path:
    """默认海报（无真实海报条目的兜底展示）。

    2026-08-27 v4（产品反馈"空白占位让小朋友疑惑为什么没图"）：糖果色
    微笑太阳六变体，按 seed（media/entity id）稳定轮换——一排无海报内容
    呈现为有意的彩色拼图而不是清一色空白墙。无文字、无品牌色原则保留。
    变体图由一次性工具生成、随包分发（运行时零绘制依赖）；assets 缺失时
    回退 ffmpeg 纯渐变（浅暖灰）保底。"""
    import hashlib
    import shutil

    idx = 0
    if seed:
        idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)             % _DEFAULT_POSTER_VARIANTS
    cache = Path(config.data_dir) / "cache" / "posters"
    target = cache / f"_default_v4_{idx}.jpg"
    if target.is_file() and target.stat().st_size > 0:
        return target
    cache.mkdir(parents=True, exist_ok=True)
    # 旧版缓存清理（_default=配色叠标题 / v2=深灰 / v3=浅暖灰纯色）
    for legacy in ("_default.jpg", "_default_v2.jpg", "_default_v3.jpg"):
        (cache / legacy).unlink(missing_ok=True)
    asset = Path(__file__).parent / "assets" / "default_posters" / f"dp_{idx}.jpg"
    if asset.is_file() and asset.stat().st_size > 0:
        shutil.copyfile(asset, target)
        return target
    # 兜底：assets 缺失（异常打包）时生成浅暖灰渐变
    tmp = target.with_suffix(".part.jpg")
    cmd = [
        config.ffmpeg_path, "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "gradients=s=480x640:c0=0xF1E8D9:c1=0xE3D6C0:x0=0:y0=0:x1=0:y1=640"
              ":nb_colors=2:speed=0.0001:d=0.04",
        "-frames:v", "1", "-q:v", "5", "-f", "image2", str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(target)
            return target
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("默认海报生成失败: %s", exc)
    finally:
        tmp.unlink(missing_ok=True)
    raise FileNotFoundError("默认海报生成失败")


def poster_ready(config: Config, media_id: str) -> bool:
    p = poster_path(config, media_id)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def poster_candidates(obj, sc) -> list[str]:
    """按优先级返回海报候选 path_key（声明式 > 同名图 > 目录图）。"""
    video_dir = PurePosixPath(obj.path_key).parent
    stem = Path(obj.name).stem
    candidates: list[str] = []
    if sc.poster_file:
        candidates.append(str(video_dir / sc.poster_file))
    for ext in (".jpg", ".jpeg", ".png"):
        candidates.append(str(video_dir / (stem + ext)))
    for name in ("poster.jpg", "folder.jpg"):
        candidates.append(str(video_dir / name))
    return candidates


def _check_size(key: str, size: int, mtime_ms: int) -> tuple[str, int] | None:
    if 0 < size <= MAX_SOURCE_IMAGE_BYTES:
        return key, mtime_ms
    if size > MAX_SOURCE_IMAGE_BYTES:
        logger.warning("海报源超过大小上限，跳过：%s (%d bytes)", key, size)
    return None


def find_poster_source(provider, obj, sc) -> tuple[str, int] | None:
    """返回 (海报源 path_key, 源 mtime_ms)；无可用源返回 None。

    仅用于本地 provider（逐候选 stat；本地开销可忽略）。
    """
    for key in poster_candidates(obj, sc):
        try:
            st = provider.stat(key)
        except Exception:
            continue  # 候选缺失是常态
        hit = _check_size(key, st.size, st.mtime_ms)
        if hit is not None:
            return hit
    return None


def find_poster_source_indexed(dir_index: dict, obj, sc) -> tuple[str, int] | None:
    """目录索引版海报查找：候选存在性查扫描期建立的全量索引。

    网络源（WebDAV/SMB）不再逐候选发 stat/HEAD——万级媒体 × 5 候选的
    请求放大不可接受；索引条目已带 size/mtime，直接判定。
    """
    for key in poster_candidates(obj, sc):
        parent, _, name = key.rpartition("/")
        entry = dir_index.get(parent, {}).get(name)
        if entry is None:
            continue
        hit = _check_size(key, entry.size, entry.mtime_ms)
        if hit is not None:
            return hit
    return None


def _ffmpeg_to_poster(config: Config, input_arg: str, media_id: str, *, seek_seconds: float | None = None) -> bool:
    """一次 FFmpeg 调用产出统一规格海报；成功返回 True。"""
    target = poster_path(config, media_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part.jpg")
    cmd = [config.ffmpeg_path, "-y", "-loglevel", "error"]
    if seek_seconds is not None:
        cmd += ["-ss", f"{seek_seconds:.2f}"]
    cmd += [
        "-i", input_arg,
        "-vf", f"scale='min({POSTER_WIDTH},iw)':-2",
        "-frames:v", "1", "-q:v", "5", "-f", "image2", str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            logger.warning("海报生成失败 media=%s: %s", media_id, proc.stderr.decode(errors="replace")[:200])
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(target)
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("海报生成异常 media=%s: %s", media_id, exc)
        tmp.unlink(missing_ok=True)
        return False


def _mtime_ms(path: Path) -> int:
    return int(path.stat().st_mtime * 1000)


def generate_from_source(config: Config, provider, media_id: str, source_key: str,
                         source_mtime_ms: int) -> bool:
    """sidecar/约定式图片 → 海报。源比现有海报新才重新生成（幂等）。"""
    target = poster_path(config, media_id)
    if target.is_file() and target.stat().st_size > 0 and _mtime_ms(target) >= source_mtime_ms:
        return True  # 已是最新
    if hasattr(provider, "abs_path"):
        return _ffmpeg_to_poster(config, str(provider.abs_path(source_key)), media_id)
    # 网络源：下载到临时文件再转码（图片体量小，上限已在发现阶段校验）
    tmp_dir = Path(config.data_dir) / "cache" / "poster_src"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{media_id}.src"
    try:
        with provider.open_range(source_key, 0) as rf, open(tmp, "wb") as wf:
            while True:
                chunk = rf.read(1024 * 1024)
                if not chunk:
                    break
                wf.write(chunk)
        return _ffmpeg_to_poster(config, str(tmp), media_id)
    except Exception as exc:
        logger.warning("海报源读取失败 media=%s: %s", media_id, exc)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def generate_from_image_bytes(config: Config, data: bytes, media_id: str) -> bool:
    """在线刮削下载的图片字节 → 统一规格海报（宽 480 JPEG，走同一 FFmpeg 管线）。"""
    if not data:
        return False
    tmp_dir = Path(config.data_dir) / "cache" / "poster_src"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{media_id}.scrape"
    try:
        tmp.write_bytes(data)
        return _ffmpeg_to_poster(config, str(tmp), media_id)
    except OSError as exc:
        logger.warning("刮削海报写入失败 media=%s: %s", media_id, exc)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def copy_poster(config: Config, from_media_id: str, to_media_id: str) -> bool:
    """同源海报复制（系列级刮削一次转码、逐集落盘用）。"""
    src = poster_path(config, from_media_id)
    if not src.is_file() or src.stat().st_size == 0:
        return False
    target = poster_path(config, to_media_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copyfile(src, target)
    return True


def extract_frame(config: Config, provider, obj, media_id: str, duration_ms: int) -> bool:
    """无图片源时从本地视频抽帧（网络源不为此下载视频，直接跳过）。仅在缺失时执行。"""
    if poster_ready(config, media_id):
        return True
    if not hasattr(provider, "abs_path") or duration_ms <= 4000:
        return False
    pos = duration_ms * FRAME_RATIO / 1000
    return _ffmpeg_to_poster(
        config, str(provider.abs_path(obj.path_key)), media_id, seek_seconds=pos,
    )
