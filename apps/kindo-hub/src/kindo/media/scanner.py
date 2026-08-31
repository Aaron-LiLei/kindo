"""Media Scanner（技术方案 §7.4 / §11.1，PRD MED-001~008）。

异步 scan_job（进程内 worker，不引入消息队列）；Hub 重启把 running 标记 interrupted。
sidecar 优先级：目录级默认值 < 文件级 < 家长修正（parent_edited_json，重扫不覆盖）。
删除 sidecar / 文件消失均不删除已入库记录（missing 标记仅作展示）。
"""
from __future__ import annotations

import json
import logging
import pathlib as _pathlib_mod  # noqa: F401  保留别名
import threading
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from ..config import Config
from ..errors import conflict
from ..models import (
    Episode,
    Media,
    ScanJob,
    SubtitleSegment,
    SubtitleTrack,
)
from ..util import new_id
from .auto_group import apply_auto_group, compute_auto_groups
from .curation import remove_episode, remove_lesson, upsert_episode, upsert_lesson
from .posters import (
    extract_frame,
    find_poster_source_indexed,
    generate_from_source,
    poster_ready,
)
from .probe import ProbeResult, is_embedded_text_subtitle, is_image_subtitle, mime_for_container, probe_media
from .sidecar import load_sidecar, sidecar_from_texts, to_tags_json
from .storage import (
    AUDIO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    LocalMountedDirectoryProvider,
    StorageObject,
    StorageRegistry,
)
from .subtitles import parse_subtitle

logger = logging.getLogger("kindo.scanner")


def _now() -> datetime:
    return datetime.now(UTC)


def _title_from_filename(name: str) -> str:
    stem = Path(name).stem
    return stem.strip() or name


class ScannerService:
    def __init__(self, db_session_factory, config: Config, storage: StorageRegistry):
        self._db = db_session_factory
        self._config = config
        self._storage = storage

    # ---------- 生命周期 ----------

    def mark_interrupted_on_startup(self) -> None:
        with self._db() as session:
            for job in session.query(ScanJob).filter(ScanJob.state == "running").all():
                job.state = "interrupted"
                job.error_summary = "Hub 重启，任务中断"
            session.commit()

    # job_id → force_full（start 与 run 在不同调用间传递）
    _force_full: dict[str, bool] = {}

    def start_job(self, mount_id: str, force_full: bool = False) -> str:
        self._storage.get(mount_id)  # 校验 mount 存在
        with self._db() as session:
            # 同挂载并发去重：已有 queued/running 任务直接 409（重复扫描=双倍流量+写竞争）
            active = (
                session.query(ScanJob)
                .filter(ScanJob.mount_id == mount_id, ScanJob.state.in_(("queued", "running")))
                .first()
            )
            if active is not None:
                raise conflict(f"该挂载已有进行中的扫描任务（{active.state}）")
            job = ScanJob(id=new_id(), mount_id=mount_id, state="queued")
            session.add(job)
            session.commit()
            job_id = job.id
        if force_full:
            self._force_full[job_id] = True
        threading.Thread(
            target=self._run_job, args=(job_id,), daemon=True, name=f"scan-{job_id[:8]}"
        ).start()
        return job_id

    def _update_job(self, job_id: str, **fields) -> None:
        with self._db() as session:
            job = session.get(ScanJob, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            session.commit()

    def get_job(self, job_id: str) -> ScanJob | None:
        with self._db() as session:
            return session.get(ScanJob, job_id)

    def latest_jobs(self, limit: int = 20) -> list[ScanJob]:
        with self._db() as session:
            return session.query(ScanJob).order_by(ScanJob.created_at.desc()).limit(limit).all()

    # ---------- 扫描主流程 ----------

    # 扫描历史保留策略：最近 500 条或 90 天（先到者为准；防止 scan_job 无限累积）
    SCAN_JOB_KEEP = 500
    SCAN_JOB_MAX_AGE_DAYS = 90

    def _prune_jobs(self) -> int:
        """清理过期/超量扫描任务记录，返回删除行数。每次任务收尾时调用。"""
        from datetime import timedelta

        with self._db() as session:
            cutoff = _now() - timedelta(days=self.SCAN_JOB_MAX_AGE_DAYS)
            deleted = (session.query(ScanJob)
                       .filter(ScanJob.created_at < cutoff)
                       .delete(synchronize_session=False))
            keep_ids = [r[0] for r in (
                session.query(ScanJob.id)
                .order_by(ScanJob.created_at.desc())
                .limit(self.SCAN_JOB_KEEP).all())]
            total = session.query(ScanJob).count()
            if total > self.SCAN_JOB_KEEP:
                deleted += (session.query(ScanJob)
                            .filter(ScanJob.id.notin_(keep_ids))
                            .delete(synchronize_session=False))
            if deleted:
                session.commit()
            return deleted

    def _run_job(self, job_id: str) -> None:
        with self._db() as session:
            job = session.get(ScanJob, job_id)
            if job is None:
                return
            mount_id = job.mount_id
        self._update_job(job_id, state="running", started_at=_now())
        try:
            stats = self._scan_mount(
                mount_id, job_id, force_full=self._force_full.get(job_id, False))
            self._force_full.pop(job_id, None)
            self._update_job(job_id, state="done", progress=1.0, finished_at=_now(), error_summary=None)
            logger.info("扫描完成 mount=%s stats=%s", mount_id, stats)
        except Exception as exc:
            logger.exception("扫描失败 mount=%s", mount_id)
            self._update_job(job_id, state="failed", finished_at=_now(), error_summary=str(exc)[:500])
        finally:
            try:
                self._prune_jobs()
            except Exception:
                logger.warning("扫描历史清理失败", exc_info=True)

    # 批量事务（优化 C，2026-08-25）：逐文件 commit 在万级库上事务开销显著；
    # 每批一个事务 + 进度按批更新（粒度 1/N*BATCH，UI 无感知差异）
    BATCH_SIZE = 100

    def _probe_mode(self, mount_id: str) -> str:
        """探测策略（B，2026-08-25）：挂载 config_json.probe_mode。
        range=本地反代只取元数据字节（网络源默认）/ skip=跳过 / full=整文件下载。"""
        from ..models import MediaMount

        with self._db() as session:
            row = (session.query(MediaMount)
                   .filter(MediaMount.storage_id == mount_id).first())
            if row is None and mount_id.startswith("page-"):
                row = session.get(MediaMount, mount_id[len("page-"):])
            mode = ((row.config_json or {}).get("probe_mode") if row else None) or "range"
        return mode if mode in ("range", "skip", "full") else "range"

    # 全量兜底周期（优化 D）：剪枝扫描最多隔 7 天强制一次全量
    FULL_SCAN_INTERVAL_SECONDS = 7 * 24 * 3600

    def _enumerate(self, provider, mount_id: str, job_id: str,
                   force_full: bool) -> tuple[list, list[str]]:
        """枚举条目（优化 D，2026-08-25）。

        本地源：os.walk 全量（成本可忽略）。
        网络源：目录 mtime 剪枝——父目录列表里子目录 mtime 与上次记录一致 →
        整棵子树跳过（未变化库重扫 ~2000 请求 → ~顶层一次）；任一目录变化
        （含新增/删除子项）其 mtime 更新 → 照常下钻。返回 (entries, 剪枝前缀)。
        兜底：距上次全量 >7 天 或 force_full 或无历史状态 → 全量。
        """
        entries: list = []
        pruned: list[str] = []
        is_network = hasattr(provider, "dir_listing") and not hasattr(provider, "abs_path")

        def _progress(n: int) -> None:
            if n % 2000 == 0:
                self._update_job(job_id, progress=round(min(0.05, n / 400_000), 3))

        if not is_network:
            for n, e in enumerate(provider.list_entries(), start=1):
                entries.append(e)
                _progress(n)
            return entries, pruned

        from ..models import ScanDirState

        with self._db() as session:
            rows = {r.dir_path: r.mtime_ms for r in (
                session.query(ScanDirState).filter(ScanDirState.mount_id == mount_id).all())}
        last_full = rows.get("", 0) / 1000.0
        do_prune = (not force_full and rows
                    and time.time() - last_full < self.FULL_SCAN_INTERVAL_SECONDS)

        dir_mtimes: dict[str, int] = {}      # 本次访问到的目录 → 新 mtime（含剪枝入口）
        def _walk(dir_key: str) -> None:
            listing = provider.dir_listing(dir_key)
            prefix = f"{dir_key}/" if dir_key else ""
            n_here = 0
            for name, is_dir, size, mtime in listing:
                n_here += 1
                full = f"{prefix}{name}"
                if is_dir:
                    if do_prune and rows.get(full) == mtime:
                        # mtime 未变：子树跳过，但记录其存在（保留状态行）
                        dir_mtimes[full] = mtime
                        pruned.append(full + "/")
                        continue
                    dir_mtimes[full] = mtime
                    _walk(full)
                else:
                    entries.append(StorageObject(
                        path_key=full, name=name, size=size, mtime_ms=mtime))
            if n_here:
                _progress(len(entries))

        _walk("")
        now_ms = int(time.time() * 1000)
        dir_mtimes[""] = now_ms if not do_prune else rows.get("", int(time.time() * 1000))
        # 全量扫描重置 last_full 标记
        if not do_prune:
            dir_mtimes[""] = now_ms
        with self._db() as session:
            # 删除已消失目录的状态行（保留剪枝入口与本次访问过的）
            keep = set(dir_mtimes) | {px.rstrip("/") for px in pruned}
            for r in session.query(ScanDirState).filter(
                    ScanDirState.mount_id == mount_id).all():
                if r.dir_path not in keep:
                    session.delete(r)
            for path, mt in dir_mtimes.items():
                row = session.get(ScanDirState, (mount_id, path))
                if row is None:
                    session.add(ScanDirState(mount_id=mount_id, dir_path=path, mtime_ms=mt))
                else:
                    row.mtime_ms = mt
            session.commit()
        return entries, pruned

    def _scan_mount(self, mount_id: str, job_id: str, force_full: bool = False) -> dict:
        provider = self._storage.get(mount_id)
        probe_mode = "local" if hasattr(provider, "abs_path") else self._probe_mode(mount_id)
        # 单次全量遍历：视频/字幕过滤 + 目录索引（sidecar/海报候选的存在性判定，
        # 网络源不再逐文件 stat/HEAD——请求放大会被网盘限速）
        # 枚举阶段反馈（此前 10 分钟 0% 无反馈）：按已发现条目数推进，封顶 5%
        entries, pruned_prefixes = self._enumerate(
            provider, mount_id, job_id, force_full=force_full)
        videos = [e for e in entries
                  if "." + e.name.rsplit(".", 1)[-1].lower() in VIDEO_EXTENSIONS]
        audios = [e for e in entries
                  if "." + e.name.rsplit(".", 1)[-1].lower() in AUDIO_EXTENSIONS]
        subs = [e for e in entries
                if "." + e.name.rsplit(".", 1)[-1].lower() in SUBTITLE_EXTENSIONS]
        dir_index: dict[str, dict[str, object]] = {}
        for e in entries:
            parent = e.path_key.rpartition("/")[0]
            dir_index.setdefault(parent, {})[e.name] = e
        total = max(1, len(videos))
        # 自动归组推导（纯 path_key，零网络）：子树 ≥2 条视频的二级目录 → 系列
        auto_groups = compute_auto_groups(e.path_key for e in videos)
        stats: dict[str, int] = {
            "videos": len(videos), "audios": len(audios), "subs": len(subs),
            "created": 0, "updated": 0, "unchanged": 0,
        }

        seen_keys: set[str] = set()
        created_ids: list[str] = []
        BATCH = self.BATCH_SIZE
        with self._db() as session:
            for idx, obj in enumerate(videos):
                seen_keys.add(obj.path_key)
                status = self._upsert_video(session, provider, mount_id, obj, dir_index,
                                            auto_groups, probe_mode=probe_mode)
                if status == "created":
                    created_ids.append(session.query(Media)
                                       .filter(Media.mount_id == mount_id,
                                               Media.path_key == obj.path_key)
                                       .one().id)
                stats[status] += 1
                if (idx + 1) % BATCH == 0 or idx + 1 == len(videos):
                    session.commit()
                    # 5%~95%：枚举 5%、收尾（字幕/消失检查）预留 5%，
                    # 进度条到 100% 必然伴随 state=done
                    self._update_job(
                        job_id,
                        progress=round(0.05 + 0.90 * (idx + 1) / total, 3))

        with self._db() as session:
            for obj in audios:
                seen_keys.add(obj.path_key)
                status = self._upsert_audio(session, provider, mount_id, obj, dir_index,
                                            probe_mode=probe_mode)
                if status == "created":
                    created_ids.append(session.query(Media)
                                       .filter(Media.mount_id == mount_id,
                                               Media.path_key == obj.path_key)
                                       .one().id)
                stats[status] += 1
            session.commit()

        self._update_job(job_id, progress=0.97)
        with self._db() as session:
            self._ingest_external_subtitles(session, provider, mount_id, subs)
            from .content_catalog import sync_media_entity, try_relocate_moved_media

            created_rows = (
                session.query(Media).filter(Media.id.in_(created_ids)).all()
                if created_ids else []
            )
            for m in session.query(Media).filter(Media.mount_id == mount_id, Media.missing.is_(False)).all():
                if m.path_key in seen_keys:
                    continue
                # 剪枝跳过的子树未重新枚举——其中的文件不应被误标 missing
                if any(m.path_key.startswith(px) for px in pruned_prefixes):
                    continue
                # 文件移动指纹迁移（决策二）：同指纹新行存在 → 历史随 entity 保留
                relocated = any(
                    try_relocate_moved_media(session, m, n) for n in created_rows
                    if n.id != m.id
                )
                if not relocated:
                    m.missing = True
                    sync_media_entity(session, m)  # missing 传导到 media_asset
                    stats["missing"] = stats.get("missing", 0) + 1
                else:
                    stats["moved"] = stats.get("moved", 0) + 1
            session.commit()
        return stats

    # ---------- 探测 / sidecar（本地与网络源统一入口） ----------

    def _probe_object(self, provider, obj, probe_mode: str = "local") -> ProbeResult | None:
        """按策略探测（B + Range 反代优化，2026-08-25）：
        local=直接路径（本地源）；range=本地 Range 反代，ffprobe 只取元数据字节
        （网络源默认，传输量从整文件降到通常 <2MB）；skip=跳过（时长未知可播）；
        full=整文件下载（原行为，受 remote_probe_max_bytes 上限约束）。"""
        if hasattr(provider, "abs_path"):
            return probe_media(provider.abs_path(obj.path_key), self._config.ffprobe_path)
        if probe_mode == "skip":
            return None
        if probe_mode == "range":
            from .probe_proxy import get_probe_proxy

            proxy = get_probe_proxy()
            token = proxy.url_for(provider, obj.path_key, obj.size)
            try:
                return probe_media(token, self._config.ffprobe_path, timeout=45.0)
            except Exception as exc:
                logger.warning("range 探测失败，回退跳过 %s: %s", obj.path_key, exc)
                return None
            finally:
                proxy.revoke(token.split("/p/")[1].split("/")[0])
        # full：临时文件整下载（技术方案 v0.2.2 §11.1）
        cap = int(getattr(self._config, "remote_probe_max_bytes", 2 * 1024 * 1024 * 1024))
        if obj.size > cap:
            logger.warning("远程文件超过探测上限，跳过 ffprobe：%s (%d bytes)", obj.path_key, obj.size)
            return None

        tmp_dir = Path(self._config.data_dir) / "cache" / "probe"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{obj.path_key.replace('/', '_')}.part"
        try:
            remaining = obj.size
            with provider.open_range(obj.path_key, 0) as rf, open(tmp, "wb") as wf:
                while remaining > 0:
                    chunk = rf.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    wf.write(chunk)
                    remaining -= len(chunk)
            return probe_media(tmp, self._config.ffprobe_path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_sidecar(self, provider, mount_id: str, obj, dir_index: dict):
        """加载 sidecar，返回 (Sidecar, overlay 根目录 | None, overlay 目录链)。

        优先级（§7.4 扩展，2026-08-20）：源目录级 < 源文件级 < **本地 overlay**
        （data/sidecars/<mount_id>/ 镜像树；目录级沿祖先链就近覆盖——网盘内容常为
        系列目录下多层嵌套，逐叶子目录放 yaml 不现实）< 家长修正。
        源 sidecar 的存在性经 dir_index 预判，网络源不再为缺失候选发请求。
        """
        parent = obj.path_key.rpartition("/")[0]
        stem = obj.name.rsplit(".", 1)[0]
        overlay_root = Path(self._config.data_dir) / "sidecars" / mount_id

        def _read(p: Path) -> str:
            with open(p, encoding="utf-8-sig", errors="replace") as f:
                return f.read(5 * 1024 * 1024)

        def _ov_dir(rel: str) -> Path:
            return overlay_root / rel if rel else overlay_root

        # 祖先链（"" → … → parent），存在的目录级 overlay 由远到近收集（近者覆盖远者）
        chain: list[str] = []
        cur = parent
        while True:
            chain.append(cur)
            if not cur:
                break
            cur = cur.rpartition("/")[0]
        ov_dirs = [c for c in reversed(chain) if (_ov_dir(c) / "kindo.yaml").is_file()]
        ov_file = _ov_dir(parent) / f"{stem}.kindo.yaml"
        if ov_dirs or ov_file.is_file():
            texts = [_read(_ov_dir(c) / "kindo.yaml") for c in ov_dirs]
            if ov_file.is_file():
                texts.append(_read(ov_file))
            return sidecar_from_texts(texts), overlay_root, ov_dirs

        if hasattr(provider, "abs_path"):
            dir_sc, file_sc = provider.sidecar_candidates(obj)  # 已做存在性检查的 Path
            return load_sidecar(dir_sc, file_sc), None, []
        # 网络源：候选文件在目录索引中不存在则不发 GET
        dir_key, file_key = provider.sidecar_candidates(obj)
        texts = [
            provider.read_text(k)
            for k in (dir_key, file_key)
            if k.rpartition("/")[2] in dir_index.get(k.rpartition("/")[0], {})
        ]
        return sidecar_from_texts(texts), None, []

    # ---------- 音频文件（v0.3：Story/Song 入库，modality=AUDIO） ----------

    def _upsert_audio(self, session: Session, provider, mount_id: str, obj,
                      dir_index: dict, probe_mode: str = "local") -> str:
        """音频 upsert：sidecar 可声明 entity_type（story/song）；默认 song。
        不参与自动归组与抽帧；probe 取时长；约定图海报仍可用。"""
        existing = (
            session.query(Media)
            .filter(Media.mount_id == mount_id, Media.path_key == obj.path_key)
            .one_or_none()
        )
        sc = self._load_sidecar(provider, mount_id, obj, dir_index)[0]
        unchanged = (
            existing is not None and not existing.missing
            and existing.size_bytes == obj.size and existing.mtime_ms == obj.mtime_ms
            and not (existing.probe_json or {}).get("error")
        )
        probe = None
        if not unchanged:
            try:
                probe = self._probe_object(provider, obj)
            except Exception as exc:
                logger.warning("ffprobe 失败(音频) %s: %s", obj.path_key, exc)

        etype = "story" if sc.entity_type == "story" else "song"
        if existing is None:
            media = Media(
                id=new_id(), mount_id=mount_id, path_key=obj.path_key,
                title=sc.title or _title_from_filename(obj.name),
                size_bytes=obj.size, mtime_ms=obj.mtime_ms, missing=False,
                media_type=etype, metadata_version=1,
            )
            session.add(media)
            session.flush()
        else:
            media = existing
            media.missing = False
            media.size_bytes = obj.size
            media.mtime_ms = obj.mtime_ms
        if probe is not None:
            media.duration_ms = probe.duration_ms
            media.mime_type = mime_for_container(probe.container)
            media.playable = True
            media.probe_json = {"container": probe.container, "audio": [
                {"id": f"a{st.index}", "codec": st.codec, "language": st.language}
                for st in probe.audio_streams]}
        from .content_catalog import sync_media_entity

        media.has_poster = self._refresh_poster(
            provider, obj, media, sc, probe, dir_index, None, [])
        entity = sync_media_entity(session, media)
        if entity is not None and etype == "story" and sc.story_text:
            # 朗读文本（§7.4）：仅 sidecar 声明，扫描以 sidecar 为准覆盖；
            # 非可信内容数据——只作 read_story 播报素材，不进 LLM 上下文
            entity.story_text = sc.story_text.strip()[:3000]
        if existing is None:
            return "created"
        return "unchanged"

    # ---------- 单个视频 ----------

    @staticmethod
    def _fingerprint(m: Media) -> tuple:
        """元数据指纹：逐字段对比，实际变化才 version+1（避免重扫无限涨版本）。"""
        return (
            m.title, m.language, m.age_band, m.media_type, m.playable, m.duration_ms,
            json.dumps(m.tags_json or {}, sort_keys=True, ensure_ascii=False),
            json.dumps(m.probe_json or {}, sort_keys=True, ensure_ascii=False),
        )

    def _upsert_video(self, session: Session, provider, mount_id: str, obj,
                      dir_index: dict, auto_groups: dict | None = None,
                      probe_mode: str = "local") -> str:
        """upsert 单个视频；返回 created / updated / unchanged。

        增量规则：文件未变（size+mtime 一致）且上次探测有效 → 跳过 ffprobe 与
        内嵌字幕轨登记（网络源 ffprobe 需整文件下载，§11.1）；sidecar、家长修正、
        海报探测、自动归组照常执行，保证改 kindo.yaml / 新放海报图 / 目录结构
        变化重扫即生效（§7.4 语义不变）。
        """
        existing = (
            session.query(Media)
            .filter(Media.mount_id == mount_id, Media.path_key == obj.path_key)
            .one_or_none()
        )
        unchanged = (
            existing is not None and not existing.missing
            and existing.size_bytes == obj.size and existing.mtime_ms == obj.mtime_ms
            and not (existing.probe_json or {}).get("error")
        )
        sc, overlay_root, ov_dirs = self._load_sidecar(provider, mount_id, obj, dir_index)

        probe: ProbeResult | None = None
        probe_skipped = False
        if not unchanged:
            try:
                probe = self._probe_object(provider, obj, probe_mode)
                probe_skipped = probe is None and probe_mode == "skip"
            except Exception as exc:
                logger.warning("ffprobe 失败 %s: %s", obj.path_key, exc)

        before = self._fingerprint(existing) if existing is not None else None

        if existing is None:
            media = Media(
                id=new_id(), mount_id=mount_id, path_key=obj.path_key,
                title=sc.title or _title_from_filename(obj.name),
                size_bytes=obj.size, mtime_ms=obj.mtime_ms, missing=False,
                media_type="movie", metadata_version=1,
            )
            session.add(media)
            session.flush()  # 先落库，保证 Episode/Lesson 外键可用
        else:
            media = existing
            media.missing = False
            media.size_bytes = obj.size
            media.mtime_ms = obj.mtime_ms

        if probe is not None:
            media.duration_ms = probe.duration_ms
            media.mime_type = mime_for_container(probe.container)
            media.playable = probe.playable
            media.probe_json = {
                "container": probe.container,
                "video_codec": probe.video_codec,
                "notes": probe.notes,
                "audio": [
                    {"id": f"a{st.index}", "codec": st.codec, "language": st.language, "title": st.title}
                    for st in probe.audio_streams
                ],
                "subtitles": [
                    {"id": f"s{st.index}", "codec": st.codec, "language": st.language,
                     "title": st.title, "text": is_embedded_text_subtitle(st.codec),
                     "image": is_image_subtitle(st.codec)}
                    for st in probe.subtitle_streams
                ],
            }
        elif not unchanged:
            media.playable = False
            network = hasattr(provider, "open_range") and not hasattr(provider, "abs_path")
            if probe_skipped:
                # skip 模式：可播但时长未知；不写 error 标记（下次重扫走增量快路径）
                media.playable = True
                media.probe_json = {"skipped": "probe_mode_skip"}
            elif network:
                media.playable = True  # range 回退跳过/超限：不阻断播放（时长未知）
                media.probe_json = {"error": "probe_skipped_over_cap"}
            else:
                media.probe_json = {"error": "ffprobe_failed"}

        if sc.title:
            media.title = sc.title  # 未被家长修正的字段随 sidecar 更新（§7.4）
        if sc.language:
            media.language = sc.language
        if sc.age_band:
            media.age_band = sc.age_band
        media.tags_json = to_tags_json(sc)

        if sc.course_name:
            media.media_type = "lesson"
        elif sc.series_name:
            media.media_type = "episode"

        # 家长修正覆盖（parent_edited_json 是事实来源，重扫不覆盖，见 §7.4）
        edited = media.parent_edited_json or {}
        for field in ("title", "language", "age_band", "media_type"):
            if field in edited:
                setattr(media, field, edited[field])
        if "tags" in edited:
            merged = dict(media.tags_json or {})
            for group in ("characters", "themes", "tags"):
                if group in edited["tags"]:
                    merged[group] = edited["tags"][group]
            media.tags_json = merged

        if media.media_type == "episode" and sc.series_name:
            upsert_episode(session, media, sc.series_name, sc.season_no, sc.episode_no)
            # sidecar 声明了系列但未给集号/季号（overlay 目录级常见）→ 用文件名
            # 推断补齐；§7.4 语义：未声明的字段可由扫描器推导，声明的值不动
            assig = (auto_groups or {}).get(obj.path_key)
            if assig is not None:
                ep = session.query(Episode).filter(Episode.media_id == media.id).one_or_none()
                if ep is not None:
                    if sc.episode_no is None and assig.episode_no != ep.episode_no:
                        ep.episode_no = assig.episode_no
                    if sc.season_no is None and assig.season_no is not None:
                        ep.season_no = assig.season_no
        if media.media_type == "lesson" and sc.course_name:
            upsert_lesson(session, media, sc.course_name, sc.chapter_no, sc.lesson_no)
        # 家长修正的归组（PATCH /media/{id} 提交；None=显式解除）优先于 sidecar
        if "series" in edited:
            if edited["series"]:
                upsert_episode(session, media, edited["series"]["name"],
                               edited["series"].get("season_no"),
                               edited["series"].get("episode_no"))
            else:
                remove_episode(session, media)
        if "course" in edited:
            if edited["course"]:
                upsert_lesson(session, media, edited["course"]["name"],
                              edited["course"].get("chapter_no"),
                              edited["course"].get("lesson_no"))
            else:
                remove_lesson(session, media)

        # 自动归组（优先级最低）：sidecar / 家长修正均未声明归组时按目录结构推断；
        # 一旦声明，自动标记让位（auto_series_key 置空），绑定不再被重算覆盖
        apply_auto_group(
            session, media, (auto_groups or {}).get(obj.path_key),
            declared=bool(sc.series_name or sc.course_name)
            or "series" in edited or "course" in edited,
            type_edited="media_type" in edited,
        )

        # 嵌入字幕轨登记（source_ref 为内部流 id；首轮与重扫一致执行，
        # 否则新入库媒体要等第二次扫描才有内嵌轨；V0.1 默认不抽取文本，
        # grounding_available=False —— 内嵌抽取范围由 PoC 字幕覆盖率决定，评审 O-3 / 决策 D-2）
        if probe is not None:
            for st in probe.subtitle_streams:
                src = f"embedded:{st.index}"
                track = (
                    session.query(SubtitleTrack)
                    .filter(
                        SubtitleTrack.media_id == media.id,
                        SubtitleTrack.source_type == "embedded",
                        SubtitleTrack.source_ref == src,
                    )
                    .one_or_none()
                )
                if track is None:
                    session.add(SubtitleTrack(
                        id=new_id(), media_id=media.id, language=st.language,
                        source_type="embedded", source_ref=src,
                        label=st.title or f"内嵌轨 {st.index}",
                        grounding_available=False,
                    ))

        # 缩略海报（§13.2 cache/posters）：overlay 图 > 源内声明/约定式图片 > 本地抽帧；
        # 生成失败不阻塞扫描主流程。unchanged 时仍探测（新放图重扫即生效）
        media.has_poster = self._refresh_poster(
            provider, obj, media, sc, probe, dir_index, overlay_root, ov_dirs)
        # v0.3 统一内容目录同步（阶段 1c）：media/series/episode/course/lesson 变更
        # 幂等镜像到 content_entity 树 + media_asset + entity_asset + topic 关联
        from .content_catalog import sync_media_entity

        sync_media_entity(session, media)
        if existing is None:
            return "created"
        if self._fingerprint(media) != before:
            media.metadata_version = (media.metadata_version or 1) + 1
            return "updated"
        return "unchanged"

    def _refresh_poster(self, provider, obj, media: Media, sc, probe: ProbeResult | None,
                        dir_index: dict, overlay_root: Path | None,
                        ov_dirs: list[str]) -> bool:
        try:
            if overlay_root is not None:
                ov = LocalMountedDirectoryProvider("sidecar-overlay", overlay_root)
                # overlay 海报候选：声明式（沿 overlay 目录链就近）> 视频目录同名图 >
                # 各 overlay 目录约定图（poster.jpg/folder.jpg）；全部本地 stat，零网络
                stem = obj.name.rsplit(".", 1)[0]
                parent = obj.path_key.rpartition("/")[0]
                cands = [f"{c}/{sc.poster_file}" if c else sc.poster_file
                         for c in reversed(ov_dirs) if sc.poster_file]
                cands += [f"{parent}/{stem}{ext}" if parent else f"{stem}{ext}"
                          for ext in (".jpg", ".jpeg", ".png")]
                cands += [f"{c}/{name}" if c else name
                          for c in reversed(ov_dirs)
                          for name in ("poster.jpg", "folder.jpg")]
                for key in cands:
                    try:
                        st = ov.stat(key)
                    except Exception:
                        continue
                    if 0 < st.size <= 32 * 1024 * 1024 and generate_from_source(
                            self._config, ov, media.id, key, st.mtime_ms):
                        return True
            hit = find_poster_source_indexed(dir_index, obj, sc)
            if hit is not None and generate_from_source(
                    self._config, provider, media.id, hit[0], hit[1]):
                return True
            duration_ms = media.duration_ms if probe is not None else 0
            if extract_frame(self._config, provider, obj, media.id, duration_ms):
                return True
            return poster_ready(self._config, media.id)
        except Exception as exc:
            logger.warning("海报生成异常 %s: %s", obj.path_key, exc)
            return poster_ready(self._config, media.id)

    # ---------- 外置字幕 ----------

    def _ingest_external_subtitles(self, session: Session, provider, mount_id: str, subs) -> None:
        for obj in subs:
            name = PurePosixPath(obj.path_key).name
            stem = Path(name).stem
            # 支持 EP01.srt / EP01.zh.srt；语言取自末段（若像语言代码）
            parts = stem.split(".")
            lang = None
            if len(parts) >= 2 and 2 <= len(parts[-1]) <= 5 and parts[-1].replace("-", "").replace("_", "").isalpha():
                lang, stem = parts[-1], ".".join(parts[:-1])
            parent = PurePosixPath(obj.path_key).parent
            parent_str = "" if str(parent) == "." else str(parent)
            candidates = [
                (f"{parent_str}/{stem}{ext}" if parent_str else f"{stem}{ext}")
                for ext in (".mp4", ".mkv", ".webm", ".mov")
            ]
            media = (
                session.query(Media)
                .filter(Media.mount_id == mount_id, Media.path_key.in_(candidates))
                .one_or_none()
            )
            if media is None:
                continue
            src = f"external:{obj.path_key}"
            stat_key = f"{obj.size}:{obj.mtime_ms}"
            track = (
                session.query(SubtitleTrack)
                .filter(
                    SubtitleTrack.media_id == media.id,
                    SubtitleTrack.source_type == "external",
                    SubtitleTrack.source_ref == src,
                )
                .one_or_none()
            )
            if track is not None and track.stat_key == stat_key:
                continue  # 未变化
            try:
                content = provider.read_text(obj.path_key)
                segments = parse_subtitle(content)
            except Exception as exc:
                logger.warning("外置字幕解析失败 %s: %s", obj.path_key, exc)
                continue
            if track is None:
                track = SubtitleTrack(
                    id=new_id(), media_id=media.id, language=lang,
                    source_type="external", source_ref=src,
                    label=(lang or "") + " 外置字幕" if lang else "外置字幕",
                    stat_key=stat_key, grounding_available=True,
                )
                session.add(track)
                session.flush()
            else:
                track.language = lang or track.language
                track.stat_key = stat_key
                track.grounding_available = True
                session.query(SubtitleSegment).filter(SubtitleSegment.track_id == track.id).delete()
            for seg in segments:
                session.add(SubtitleSegment(
                    id=new_id(), track_id=track.id, seq=seg.seq,
                    start_ms=seg.start_ms, end_ms=seg.end_ms, text=seg.text,
                ))
