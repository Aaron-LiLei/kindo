"""媒体扫描：sidecar 优先级 / parent_edited 保护 / 字幕入库 / 结构推断（技术方案 §7.4）。"""

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
def test_scan_full_library(env):
    build_sample_library(env.media_dir)
    job = _scan(env)
    assert job["state"] == "done", job
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert len(items) == 4
    by_title = {i["title"]: i for i in items}

    # sidecar 文件级字段
    ep1 = by_title["汪汪队立大功 第一季 第1集"]
    assert ep1["media_type"] == "episode"
    assert "天天" in ep1["tags"]["characters"]
    assert "救援" in ep1["tags"]["themes"]
    assert ep1["playable"] is True
    assert ep1["duration_ms"] >= 7000  # ffprobe 实测时长

    # 目录级默认值 + 文件级缺省字段（movies/kindo.yaml 提供 language/age_band）
    movie = by_title["海底小纵队大电影"]
    assert movie["media_type"] == "movie"
    assert movie["language"] == "zh-CN"
    assert movie["age_band"] == "3-6"

    lesson = by_title["英语启蒙 第1课"]
    assert lesson["media_type"] == "lesson"
    assert lesson["language"] == "en-US"


@requires_ffprobe
def test_parent_edit_survives_rescan(env):
    build_sample_library(env.media_dir)
    _scan(env)
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    target = next(i for i in items if i["title"].startswith("汪汪队") and "第1集" in i["title"])

    # 家长修正（含 sidecar 没有的新角色与主题）
    r = env.client.patch(
        f"/api/v1/admin/media/{target['media_id']}",
        json={"title": "汪汪队·飞行救援特辑", "characters": ["天天", "小克"]},
        headers=env.admin_headers(),
    )
    assert r.status_code == 200
    v1 = r.json()["metadata_version"]
    assert "title" in r.json()["parent_edited_fields"]
    assert "tags" in r.json()["parent_edited_fields"]

    # 修改 sidecar 后重扫：家长修正不被覆盖，未修正字段（第2集）随 sidecar 更新
    s2 = env.media_dir / "series/汪汪队/S01E02.kindo.yaml"
    data = yaml.safe_load(s2.read_text(encoding="utf-8"))
    data["title"] = "汪汪队立大功 第一季 第2集（新译名）"
    s2.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    _scan(env)
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    edited = next(i for i in items if i["media_id"] == target["media_id"])
    assert edited["title"] == "汪汪队·飞行救援特辑"  # 家长修正优先
    assert edited["metadata_version"] >= v1
    assert set(edited["tags"]["characters"]) == {"天天", "小克"}

    ep2 = next(i for i in items if "第2集" in i["title"])
    assert ep2["title"].endswith("（新译名）")  # 未修正字段随 sidecar 更新


@requires_ffprobe
def test_external_subtitle_ingested(env):
    build_sample_library(env.media_dir)
    _scan(env)
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    ep1 = next(i for i in items if "第1集" in i["title"])
    detail = env.client.get(f"/api/v1/media/{ep1['media_id']}", headers=headers).json()
    ext_tracks = [t for t in detail["subtitle_tracks"] if t["source_type"] == "external"]
    assert len(ext_tracks) == 1
    assert ext_tracks[0]["grounding_available"] is True
    # 语义检索：角色命中（MED-008）
    r = env.client.get("/api/v1/media", params={"query": "天天"}, headers=headers).json()
    assert any("第1集" in i["title"] for i in r["items"])


@requires_ffprobe
def test_missing_file_marks_not_deletes(env):
    build_sample_library(env.media_dir)
    _scan(env)
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    (env.media_dir / "movies/海底小纵队.mp4").unlink()
    _scan(env)
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    movie = next(i for i in items if i["title"] == "海底小纵队大电影")
    assert movie["missing"] is True  # 记录保留，仅标记
    # TV 列表不显示 missing
    tv_items = env.client.get("/api/v1/media", headers=headers).json()["items"]
    assert all(i["media_id"] != movie["media_id"] for i in tv_items)
