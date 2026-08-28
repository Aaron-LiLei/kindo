"""扫描管线加固：增量跳过 / 状态机统一 / 并发去重 / 新端点（2026-08-20）。"""

import yaml

from conftest import build_sample_library, requires_ffprobe


def _scan(env):
    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    import time

    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"扫描超时: {job}")


@requires_ffprobe
def test_incremental_scan_skips_probe(env, monkeypatch):
    """文件未变时跳过 ffprobe（网络源整文件下载的成本大头），sidecar 修改仍生效。"""
    build_sample_library(env.media_dir)
    _scan(env)

    calls = {"n": 0}
    from kindo.media import scanner as scanner_mod

    orig = scanner_mod.probe_media

    def counting(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(scanner_mod, "probe_media", counting)

    _scan(env)  # 重扫：文件全部未变
    assert calls["n"] == 0, "未变化的文件不应再触发 ffprobe"

    # sidecar 修改（视频不动）重扫即生效：增量只跳 probe，不跳 sidecar 应用
    s2 = env.media_dir / "series/汪汪队/S01E02.kindo.yaml"
    data = yaml.safe_load(s2.read_text(encoding="utf-8"))
    data["title"] = "汪汪队立大功 第一季 第2集（新译名）"
    s2.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    _scan(env)
    assert calls["n"] == 0  # sidecar 变化不触发 probe
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert any("（新译名）" in i["title"] for i in items)

    # 文件变化（mtime/size 变）→ 重新 probe
    from conftest import make_sample_video

    make_sample_video(env.media_dir / "series/汪汪队/S01E02.mkv", seconds=9)
    _scan(env)
    assert calls["n"] == 1


@requires_ffprobe
def test_rescan_without_changes_keeps_version(env):
    """无实际元数据变化的重扫不递增 metadata_version（此前每次重扫 +1 无限涨）。"""
    build_sample_library(env.media_dir)
    _scan(env)
    items = {i["media_id"]: i for i in env.client.get("/api/v1/admin/media?limit=100").json()["items"]}
    _scan(env)
    again = {i["media_id"]: i for i in env.client.get("/api/v1/admin/media?limit=100").json()["items"]}
    for mid, before in items.items():
        assert again[mid]["metadata_version"] == before["metadata_version"], mid


def test_concurrent_scan_rejected(env):
    """同挂载已有 queued/running 任务时再次触发 → 409。"""
    from kindo.models import ScanJob

    env.bootstrap_admin()
    with env.db.session() as session:
        session.add(ScanJob(id="job-active-1", mount_id="family", state="running"))
        session.commit()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 409
    assert "进行中" in r.json()["error"]["message"]


def test_scan_jobs_list_and_mounts_health(env):
    env.bootstrap_admin()
    build_sample_library_raw(env)

    r = env.client.get("/api/v1/admin/scan-jobs")
    assert r.status_code == 200
    assert "jobs" in r.json()

    r = env.client.get("/api/v1/admin/media-mounts/health")
    assert r.status_code == 200
    mounts = {m["mount_id"]: m for m in r.json()["mounts"]}
    assert mounts["family"]["healthy"] is True


def build_sample_library_raw(env):
    """无 ffprobe 依赖的最小媒体目录（空目录即可，仅保证挂载存在）。"""
    (env.media_dir / "placeholder").mkdir(exist_ok=True)


def test_state_machine_strings(env):
    """scan-job state 取值域为 queued/running/done/failed/interrupted（前端据此渲染）。"""
    from kindo.models import ScanJob

    env.bootstrap_admin()
    with env.db.session() as session:
        for state in ("done", "failed", "interrupted"):
            session.add(ScanJob(id=f"job-{state}", mount_id="family", state=state))
        session.commit()
    jobs = {j["id"]: j for j in env.client.get("/api/v1/admin/scan-jobs?limit=100").json()["jobs"]}
    for state in ("done", "failed", "interrupted"):
        assert jobs[f"job-{state}"]["state"] == state
