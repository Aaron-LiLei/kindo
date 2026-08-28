"""扫描历史保留策略测试（最近 500 条或 90 天，任务收尾自动清理）。"""
from datetime import UTC, datetime, timedelta

from conftest import requires_ffprobe


@requires_ffprobe
def test_scan_job_retention_prunes_old_and_excess(env):
    from kindo.models import ScanJob
    from kindo.util import new_id

    now = datetime.now(UTC)

    def job(days_ago: float) -> ScanJob:
        return ScanJob(id=new_id(), mount_id="family", state="done", progress=1.0,
                       created_at=now - timedelta(days=days_ago))

    with env.db.session() as s:
        s.add_all([job(100) for _ in range(5)])   # 90 天前 → 按时间删
        s.add_all([job(1) for _ in range(10)])    # 近期 → 保留
        s.commit()

    deleted = env.state.scanner._prune_jobs()
    assert deleted == 5
    with env.db.session() as s:
        assert s.query(ScanJob).count() == 10

    # 超量：伪造 520 条近期任务 → 只保留最新 500
    with env.db.session() as s:
        s.add_all(ScanJob(id=new_id(), mount_id="family", state="done",
                          created_at=now - timedelta(minutes=i))
                  for i in range(520))
        s.commit()
    deleted = env.state.scanner._prune_jobs()
    assert deleted == 30
    with env.db.session() as s:
        assert s.query(ScanJob).count() == 500
