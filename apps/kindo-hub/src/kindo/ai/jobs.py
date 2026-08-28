"""AI Job Runner（技术方案 §19.5）：进程内 worker，对齐 scan_job 模式。

同 job_type 单飞（重复触发 409）；Hub 重启把 running 标记 interrupted；
不引入 Celery/Kafka/Redis。任务由家长显式触发（无定时分析）。
CATALOG_AUDIT=library_curator（S1）；USAGE_SUMMARY / CONTENT_COVERAGE
=family_advisor（S2，AIA-003/004/005）。家长侧任务不计入 ai_voice 预算。
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from ..errors import conflict, invalid_request, provider_unavailable
from ..models import AiJob, ContentEntity
from ..util import new_id
from .context import build_advisor_context, build_curator_batch_context
from .profiles import (
    ADVISOR_COVERAGE_SCHEMA,
    ADVISOR_SUMMARY_SCHEMA,
    get_profile,
)

logger = logging.getLogger("kindo.ai.jobs")

JOB_TYPES = ("CATALOG_AUDIT", "USAGE_SUMMARY", "CONTENT_COVERAGE")
IMPLEMENTED = JOB_TYPES
JOB_PROFILE = {
    "CATALOG_AUDIT": "library_curator",
    "USAGE_SUMMARY": "family_advisor",
    "CONTENT_COVERAGE": "family_advisor",
}
AUDIT_BATCH_SIZE = 50  # 代码常量（A-12：不进配置文件）
# LLM 预取并发（2026-08-27 速度优化，用户定版=2）：仅并发网络调用
# （run_ai 纯 IO，每线程独立 session/event loop）；建议落库仍按批序在
# 主线程串行——去重/计数/进度语义与串行版完全一致。实测串行 ~3 分钟/
# 批（817 批 ≈40h），并发 2 预期 ~1/2；取 2 而非 3 是对 Provider 限速
# 的保守档。
AUDIT_LLM_CONCURRENCY = 2
AUDIT_ENTITY_TYPES = ("movie", "series", "season", "episode",
                      "story", "song", "course", "lesson")
MAX_FINDINGS_KEPT = 20
MAX_HEADLINES_KEPT = 8


def _now() -> datetime:
    return datetime.now(UTC)


class AiJobRunner:
    def __init__(self, db_session_factory, config, runtime, proposals, storage=None,
                 *, history=None, policy=None, playback=None):
        self._db = db_session_factory
        self._config = config
        self._runtime = runtime
        self._proposals = proposals
        self._storage = storage
        self._history = history
        self._policy = policy
        self._playback = playback

    # ---------- 生命周期（scan_job 同模式） ----------

    def mark_interrupted_on_startup(self) -> None:
        with self._db() as session:
            for job in session.query(AiJob).filter(AiJob.state == "running").all():
                job.state = "interrupted"
                job.error_summary = "Hub 重启，任务中断"
            session.commit()

    def start(self, job_type: str) -> str:
        if job_type not in JOB_TYPES:
            raise invalid_request(f"未知任务类型: {job_type}")
        if job_type not in IMPLEMENTED:
            raise invalid_request(f"任务类型 {job_type} 尚未提供")
        if not self._runtime.ready():
            raise provider_unavailable("未配置可用的 LLM Provider，无法运行 AI 分析")
        with self._db() as session:
            active = (session.query(AiJob)
                      .filter(AiJob.job_type == job_type,
                              AiJob.state.in_(("queued", "running")))
                      .first())
            if active is not None:
                raise conflict(f"已有进行中的 AI 分析任务（{active.state}）")
            job = AiJob(id=new_id(), job_type=job_type, state="queued")
            session.add(job)
            session.commit()
            job_id = job.id
        threading.Thread(
            target=self._run_job, args=(job_id,), daemon=True,
            name=f"ai-job-{job_id[:8]}",
        ).start()
        return job_id

    def _update(self, job_id: str, **fields) -> None:
        with self._db() as session:
            job = session.get(AiJob, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            session.commit()

    def get_job(self, job_id: str) -> AiJob | None:
        with self._db() as session:
            return session.get(AiJob, job_id)

    def list_jobs(self, *, job_type: str | None = None, status: str | None = None,
                  limit: int = 20) -> list[AiJob]:
        with self._db() as session:
            q = session.query(AiJob)
            if job_type:
                q = q.filter(AiJob.job_type == job_type)
            if status:
                q = q.filter(AiJob.state == status)
            return q.order_by(AiJob.created_at.desc()).limit(limit).all()

    # ---------- 执行 ----------

    def _run_job(self, job_id: str) -> None:
        self._update(job_id, state="running", started_at=_now())
        try:
            with self._db() as session:
                job = session.get(AiJob, job_id)
                job_type = job.job_type if job else "CATALOG_AUDIT"
            if job_type == "USAGE_SUMMARY":
                result = self._run_usage_summary(job_id)
            elif job_type == "CONTENT_COVERAGE":
                result = self._run_content_coverage(job_id)
            else:
                result = self._run_catalog_audit(job_id)
            self._update(job_id, state="done", progress=1.0, finished_at=_now(),
                         result_summary=result, error_summary=None)
            logger.info("AI 任务完成 job=%s counts=%s", job_id,
                        (result or {}).get("counts"))
        except Exception as exc:
            logger.exception("AI 任务失败 job=%s", job_id)
            # str(exc) 可能为空（如裸 CancelledError）——补类型名保证家长可见失败原因
            self._update(job_id, state="failed", finished_at=_now(),
                         error_summary=f"{type(exc).__name__}: {exc}"[:500])

    def _run_usage_summary(self, job_id: str) -> dict:
        """家庭使用摘要（AIA-003/004）：无副作用摘要 + 可选 POLICY 建议（HIGH）。"""
        profile = get_profile("family_advisor")
        assert profile is not None
        counts = {"policy_created": 0, "skipped_duplicate": 0, "skipped_invalid": 0}
        self._update(job_id, result_summary={
            "stage_note": "正在读取最近 7 天的聚合使用统计（不读取逐条观看记录）…"})
        with self._db() as session:
            ctx = build_advisor_context(session, profile, history=self._history,
                                        policy=self._policy, playback=self._playback)
            output = self._runtime.run_ai(profile, ctx, ADVISOR_SUMMARY_SCHEMA)
            for s in output.get("policy_suggestions", []):
                status = self._proposals.create_from_advisor(
                    session, job_id=job_id, kind="POLICY",
                    payload_parts={"rules_patch": s.get("rules_patch")},
                    summary_parts=s.get("summary") or {})
                key = status if status.startswith("skipped_") else "policy_created"
                counts[key] = counts.get(key, 0) + 1
            session.commit()
        headlines = [h for h in output.get("headlines", []) if isinstance(h, str)][:MAX_HEADLINES_KEPT]
        if not headlines:
            headlines = ["最近没有需要特别关注的使用变化"]
        return {"headlines": headlines,
                "summary_text": [t for t in output.get("summary_text", [])
                                 if isinstance(t, str)],
                "counts": counts}

    def _run_content_coverage(self, job_id: str) -> dict:
        """内容覆盖分析（AIA-005）：InterestSignal × Catalog 缺口 → 方向性建议。"""
        profile = get_profile("family_advisor")
        assert profile is not None
        counts = {"gap_created": 0, "skipped_duplicate": 0, "skipped_invalid": 0}
        self._update(job_id, result_summary={
            "stage_note": "正在对比孩子的兴趣与家庭内容覆盖…"})
        with self._db() as session:
            ctx = build_advisor_context(session, profile, history=self._history,
                                        policy=self._policy, playback=self._playback)
            output = self._runtime.run_ai(profile, ctx, ADVISOR_COVERAGE_SCHEMA)
            for g in output.get("gaps", []):
                status = self._proposals.create_from_advisor(
                    session, job_id=job_id, kind="CONTENT_GAP",
                    payload_parts=g, summary_parts=g.get("summary") or {})
                key = status if status.startswith("skipped_") else "gap_created"
                counts[key] = counts.get(key, 0) + 1
            session.commit()
        headlines = [h for h in output.get("headlines", []) if isinstance(h, str)][:MAX_HEADLINES_KEPT]
        if not headlines:
            headlines = ["家庭内容与近期兴趣的覆盖暂无明显缺口"]
        return {"headlines": headlines, "counts": counts}

    def _run_catalog_audit(self, job_id: str) -> dict:
        profile = get_profile("library_curator")
        assert profile is not None
        with self._db() as session:
            ids = [r[0] for r in (
                session.query(ContentEntity.id)
                .filter(ContentEntity.entity_type.in_(AUDIT_ENTITY_TYPES))
                .order_by(ContentEntity.created_at, ContentEntity.id).all())]
        batches = [ids[i:i + AUDIT_BATCH_SIZE]
                   for i in range(0, len(ids), AUDIT_BATCH_SIZE)]
        counts = {"audited": len(ids), "created": 0, "created_high": 0,
                  "skipped_duplicate": 0, "skipped_locked": 0, "skipped_invalid": 0}
        findings: list[dict] = []
        # 过程可见（产品反馈 2026-08-27：不能只给一个百分比）：逐批刷新
        # stage_note 与累计计数，前端轮询 GET /ai/jobs/{id} 即可呈现"AI 正在
        # 做什么/做到哪"；完成后由最终 result_summary 呈现"本次整理依据"。
        self._update(job_id, result_summary=self._live(counts, "正在读取内容目录…", 0))
        processed = 0

        def _llm_batch(batch_ids: list[str]) -> dict:
            with self._db() as session:
                ctx = build_curator_batch_context(session, profile, batch_ids,
                                                  storage=self._storage)
                return self._runtime.run_ai(profile, ctx)

        # 预取窗口：最多 N 批 LLM 在飞，按提交顺序消费（异常即抛→任务
        # failed，与串行版同语义；窗口外的批不提交，失败退出不空跑）
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor

        futures: deque = deque()
        batch_iter = iter(batches)
        with ThreadPoolExecutor(max_workers=AUDIT_LLM_CONCURRENCY) as pool:
            for i in range(len(batches)):
                while len(futures) < AUDIT_LLM_CONCURRENCY:
                    try:
                        nxt = next(batch_iter)
                    except StopIteration:
                        break
                    futures.append((nxt, pool.submit(_llm_batch, nxt)))
                batch, fut = futures.popleft()
                output = fut.result()
                with self._db() as session:
                    for s in output.get("suggestions", []):
                        entity = session.get(ContentEntity, s.get("entity_id") or "")
                        if entity is None:
                            counts["skipped_invalid"] += 1
                            continue
                        status = self._proposals.create_from_curator(
                            session, job_id=job_id, entity=entity,
                            change_type=s.get("change_type") or "",
                            changes=s.get("changes") or {},
                            summary_parts=s.get("summary") or {})
                        if status == "created":
                            mapped_level = (s.get("change_type") in
                                            ("set_content_class", "set_age_range"))
                            counts["created_high" if mapped_level else "created"] += 1
                        elif status.startswith("skipped_"):
                            counts[status] = counts.get(status, 0) + 1
                    session.commit()
                    findings.extend(f for f in output.get("findings", [])
                                    if isinstance(f, dict))
                processed += len(batch)
                self._update(job_id, progress=(i + 1) / max(len(batches), 1),
                             result_summary=self._live(
                                 counts,
                                 f"正在分析第 {i + 1}/{len(batches)} 批内容",
                                 processed))
        findings = findings[:MAX_FINDINGS_KEPT]
        with self._db() as session:
            titles = {e.id: e.title for e in (
                session.query(ContentEntity)
                .filter(ContentEntity.id.in_(
                    [f.get("entity_id") for f in findings if f.get("entity_id")] or [""]))
                .all())}
        headlines = [f"《{titles.get(f.get('entity_id'), f.get('entity_id', ''))}》"
                     f"{f.get('issue', '')}" for f in findings]
        if not headlines and counts["created"] == 0 and counts["created_high"] == 0:
            headlines = ["本次整理没有发现需要补充的内容"]
        return {"headlines": headlines, "counts": counts}

    @staticmethod
    def _live(counts: dict, note: str, processed: int) -> dict:
        """运行中的过程快照：stage_note=当前阶段（家长可读）、processed/total=
        已检查/待检查内容数、counts=累计计数（含 created/created_high/skipped_*）。"""
        return {"stage_note": note, "processed": processed,
                "total": counts.get("audited", 0), "counts": dict(counts)}
