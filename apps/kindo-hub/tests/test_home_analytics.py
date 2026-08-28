"""Home / 首页聚合 / 统计 / TV 媒体列表（§3.1，ANA-001~003）。"""
import time

from conftest import build_sample_library, requires_ffprobe


@requires_ffprobe
def test_home_aggregation(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    home = env.client.get("/api/v1/home", headers=headers).json()
    assert "explore_themes" in home
    themes = home["explore_themes"]
    assert any("海洋" == t or "救援" == t or "英语" == t for t in themes)
    assert home["continue_watching"] == []  # 尚无观看
    assert home["continue_learning"] == []


@requires_ffprobe
def test_home_continue_split_by_modality(env):
    """交互 §4.2：断点按媒介拆分——视频进 continue_watching，音频进 continue_listening。"""
    import subprocess
    import time as _time

    from conftest import FFPROBE

    build_sample_library(env.media_dir)
    ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg")
    songs = env.media_dir / "songs"
    songs.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=6",
                    "-c:a", "libmp3lame", str(songs / "小星星.mp3")],
                   check=True, capture_output=True, timeout=30)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        _time.sleep(0.5)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    from kindo.models import Media, Profile, WatchHistory

    with env.db.session() as s:
        video = s.query(Media).filter(Media.media_type == "episode").first()
        audio = s.query(Media).filter(Media.media_type == "song").one()
        # 与 home 端点同源解析 default profile（有 Profile 行用其 id，否则 "default"）
        prow = s.query(Profile).first()
        profile_id = prow.id if prow else "default"
        for m, pos in ((video, 30_000), (audio, 2_000)):
            s.merge(WatchHistory(
                profile_id=profile_id, media_id=m.id,
                last_position_ms=pos, watched_seconds=pos // 1000,
                completed=False))
        s.commit()

    home = env.client.get("/api/v1/home", headers=headers).json()
    assert "continue_listening" in home
    watching_types = {i["media_type"] for i in home["continue_watching"]}
    listening_types = {i["media_type"] for i in home["continue_listening"]}
    assert watching_types and watching_types <= {"episode", "movie", "lesson"}
    assert listening_types and listening_types <= {"song", "story"}
    assert not (watching_types & listening_types), "视频与音频断点不得串行"
    assert home["continue_listening"][0]["last_position_ms"] == 2_000


@requires_ffprobe
def test_analytics_day(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    # 真实播放一小段并结束
    items = env.client.get("/api/v1/media", headers=headers).json()["items"]
    target = next(i for i in items if "第1集" in i["title"])
    body = env.client.post("/api/v1/playbacks", json={
        "media_id": target["media_id"], "action": "play", "source": "ui",
    }, headers=headers).json()
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "an1",
                      "playback_id": body["playback_id"], "position_ms": 0})
        from conftest import wait_ack

        wait_ack(ws, "an1")
        time.sleep(1.0)
        ws.send_json({"type": "playback.ended", "event_id": "an2",
                      "playback_id": body["playback_id"],
                      "position_ms": body["stream_descriptor"]["duration_ms"]})
        wait_ack(ws, "an2")  # 在关闭连接前确认服务端已处理 ended

    # 等待 ended 事件完成落库（interval 关闭）
    from kindo.models import Playback as _PB

    for _ in range(40):
        with env.db.session() as s:
            pb = s.get(_PB, body["playback_id"])
            done = pb is not None and pb.state == "ended"
        if done:
            break
        time.sleep(0.25)

    data = env.client.get("/api/v1/admin/analytics?period=day").json()
    assert data["period"] == "day"
    assert data["total_watched_seconds"] >= 1
    assert data["top_media"][0]["title"].startswith("汪汪队")
    assert "不产生任何心理" in data["note"]


@requires_ffprobe
def test_tv_media_pagination(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    r1 = env.client.get("/api/v1/media", params={"limit": 2}, headers=headers).json()
    assert len(r1["items"]) == 2
    assert r1["next_cursor"]
    r2 = env.client.get("/api/v1/media", params={"limit": 2, "cursor": r1["next_cursor"]},
                        headers=headers).json()
    ids1 = {i["media_id"] for i in r1["items"]}
    ids2 = {i["media_id"] for i in r2["items"]}
    assert not (ids1 & ids2), "分页不应重叠"

    # 类型过滤
    lessons = env.client.get("/api/v1/media", params={"type": "lesson"}, headers=headers).json()
    assert all(i["media_type"] == "lesson" for i in lessons["items"])
    assert len(lessons["items"]) == 1
