"""Playback / Grant / 媒体流 / 事件计时集成测试（技术方案 §9，PoC P02/P03）。"""
import time

import pytest

from conftest import build_sample_library, requires_ffprobe


@pytest.fixture()
def library_env(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            break
        time.sleep(0.5)
    return env


def _first_media(env, token, title_part):
    r = env.client.get("/api/v1/media", headers=env.device_headers(token)).json()
    return next(i for i in r["items"] if title_part in i["title"])


def _request_play(env, headers, media_id, action="play", **kw):
    return env.client.post(
        "/api/v1/playbacks",
        json={"media_id": media_id, "action": action, "source": "ui", **kw},
        headers=headers,
    )


@requires_ffprobe
@pytest.mark.slow
def test_stream_descriptor_mime_never_null(library_env):
    """网络源跳过探测（remote_probe_max_bytes=1）时 media.mime_type 为空——
    stream_descriptor 必须按扩展名推断回填，不得返回 null（2026-08-21 修复：
    TV 端 DTO 非空字段收到 null 全线解析失败，所有内容"暂时播不了"）。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")

    from kindo.models import Media as MediaModel

    with env.db.session() as s:
        row = s.query(MediaModel).filter(MediaModel.id == media["media_id"]).one()
        expected = "video/mp4" if row.path_key.lower().endswith(".mp4") else "video/x-matroska"
        row.mime_type = None
        s.commit()

    r = _request_play(env, headers, media["media_id"])
    assert r.status_code == 200, r.text
    desc = r.json()["stream_descriptor"]
    assert desc["mime_type"] == expected


def test_play_grant_and_range(library_env):
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")

    r = _request_play(env, headers, media["media_id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "allow"
    desc = body["stream_descriptor"]
    assert desc["grant"] and len(desc["grant"]) >= 40
    assert desc["url"].endswith(f"/api/v1/media/{media['media_id']}/stream")
    assert desc["duration_ms"] >= 7000
    # 外置字幕轨在 descriptor 中
    assert any(t["source_type"] == "external" for t in desc["subtitle_tracks"])

    stream_headers = {
        **headers, "X-Kindo-Playback-Grant": desc["grant"],
    }
    # 无 Range → 200 全量
    r = env.client.get(desc["url"], headers=stream_headers)
    assert r.status_code == 200
    full = r.content
    assert len(full) > 0
    size = len(full)

    # 单 Range → 206 + Content-Range
    r = env.client.get(desc["url"], headers={**stream_headers, "Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{size}"
    assert r.headers["accept-ranges"] == "bytes"
    assert len(r.content) == 100

    # open-ended Range
    r = env.client.get(desc["url"], headers={**stream_headers, "Range": "bytes=100-"})
    assert r.status_code == 206
    assert len(r.content) == size - 100

    # 越界 → 416
    r = env.client.get(desc["url"], headers={**stream_headers, "Range": f"bytes={size + 10}-"})
    assert r.status_code == 416

    # HEAD → 206
    r = env.client.head(desc["url"], headers={**stream_headers, "Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == b""


def test_stream_size_mismatch_self_heals(library_env):
    """源文件与入库 size_bytes 不一致：流长度以实际文件为准并自愈入库值。

    此前按（过期的）入库长度声明 Content-Length → 迭代器提前 EOF →
    Starlette "Response content shorter than Content-Length" 中断连接，
    播放器只看到误导性的 IO 网络错误（2026-09-01 Pad 实测复现）。"""
    env = library_env
    from kindo.api import tv as tv_api

    tv_api._size_cache.clear()
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")

    from kindo.models import Media as MediaModel

    with env.db.session() as s:
        row = s.get(MediaModel, media["media_id"])
        real_size = row.size_bytes
        assert real_size and real_size > 0
        row.size_bytes = real_size + 12345  # 模拟源文件被截断后的过期记录
        s.commit()

    body = _request_play(env, headers, media["media_id"]).json()
    stream_headers = {**headers, "X-Kindo-Playback-Grant": body["stream_descriptor"]["grant"]}
    r = env.client.get(body["stream_descriptor"]["url"], headers=stream_headers)
    assert r.status_code == 200
    assert len(r.content) == real_size  # 按实际大小交付，不再中断连接
    with env.db.session() as s:
        assert s.get(MediaModel, media["media_id"]).size_bytes == real_size  # 入库值已自愈
    tv_api._size_cache.clear()


def test_file_iter_short_read_logs_context(caplog):
    """上游提前 EOF：记录 media/declared/delivered 上下文（此前只有
    uvicorn 的无上下文 RuntimeError，无法定位是哪个媒体、差多少字节）。"""
    import logging
    import types

    from kindo.api import tv as tv_api

    class FakeReader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size):
            return b""  # 立即 EOF：上游短读

    class FakeProvider:
        def open_range(self, path_key, start, length=None):
            return FakeReader()

    state = types.SimpleNamespace(storage={"m1": FakeProvider()})
    media = types.SimpleNamespace(id="media-1", mount_id="m1", path_key="a.mp4")
    with caplog.at_level(logging.WARNING, logger="kindo.api.tv"):
        chunks = list(tv_api._file_iter(state, media, 0, 1024))
    assert chunks == []
    assert any("媒体流上游短读" in rec.getMessage() and "media-1" in rec.getMessage()
               for rec in caplog.records)


@requires_ffprobe
@pytest.mark.slow
def test_stream_without_or_bad_grant_rejected(library_env):
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")
    desc = _request_play(env, headers, media["media_id"]).json()["stream_descriptor"]
    url = desc["url"]

    # 缺 Grant
    assert env.client.get(url, headers=headers).status_code == 401
    # 伪 Grant
    r = env.client.get(url, headers={**headers, "X-Kindo-Playback-Grant": "forged" * 8})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "grant_invalid"

    # 另一台合法设备拿别人的 Grant → grant_mismatch（403）
    _d2, token2 = env.pair_device("电视2")
    r = env.client.get(url, headers={
        **env.device_headers(token2), "X-Kindo-Playback-Grant": desc["grant"],
    })
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "grant_mismatch"


@requires_ffprobe
@pytest.mark.slow
def test_stop_revokes_grant(library_env):
    """stop/revoke/Policy 更新后新 Range 被拒（P02）。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")
    body = _request_play(env, headers, media["media_id"]).json()
    playback_id = body["playback_id"]
    grant = body["stream_descriptor"]["grant"]
    url = body["stream_descriptor"]["url"]
    stream_headers = {**headers, "X-Kindo-Playback-Grant": grant}

    assert env.client.get(url, headers=stream_headers).status_code == 200

    # stop → Grant 撤销
    r = env.client.post(
        f"/api/v1/playbacks/{playback_id}/control",
        json={"action": "stop"}, headers=headers,
    )
    assert r.status_code == 200
    r = env.client.get(url, headers=stream_headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "grant_invalid"


@requires_ffprobe
@pytest.mark.slow
def test_policy_update_revokes_grant_and_pushes_stop(library_env):
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")
    body = _request_play(env, headers, media["media_id"]).json()
    grant = body["stream_descriptor"]["grant"]
    stream_headers = {**headers, "X-Kindo-Playback-Grant": grant}

    # Policy 保存（时段窗口外的硬规则）→ 撤销 Grant
    r = env.client.put(
        "/api/v1/admin/policy",
        json={"allowed_windows": [{"start": "03:00", "end": "03:30"}]},
        headers=env.admin_headers(),
    )
    assert r.status_code == 200
    assert r.json()["version"] >= 1
    r = env.client.get(body["stream_descriptor"]["url"], headers=stream_headers)
    assert r.status_code == 401  # Grant 因 Policy 变化被撤销
    # 播放状态被终止
    cur = env.client.get("/api/v1/playbacks/current", headers=headers).json()
    assert cur["playback"] is None or cur["playback"]["state"] in ("stopped",)


@requires_ffprobe
@pytest.mark.slow
def test_single_active_playback_and_switch(library_env):
    """A-11：profile 级单 active；新播放自动切换（停旧建新）。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    m1 = _first_media(env, token, "第1集")
    m2 = _first_media(env, token, "第2集")

    r1 = _request_play(env, headers, m1["media_id"]).json()
    r2 = _request_play(env, headers, m2["media_id"]).json()
    assert r1["playback_id"] != r2["playback_id"]
    cur = env.client.get("/api/v1/playbacks/current", headers=headers).json()
    assert cur["playback"]["playback_id"] == r2["playback_id"]  # 只有新的是 active

    # resume 无暂停播放 → 409
    r = _request_play(env, headers, m2["media_id"], action="resume")
    assert r.status_code == 409


@requires_ffprobe
@pytest.mark.slow
def test_playback_events_ack_dedup_and_intervals(library_env):
    """事件 ACK / event_id 去重 / 观看时长按 interval 累计 / seek 不计时（P03）。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")
    body = _request_play(env, headers, media["media_id"]).json()
    playback_id = body["playback_id"]

    with env.client.websocket_connect(
        f"/api/v1/realtime?token={token}"
    ) as ws:
        # TV 事件：started
        ws.send_json({"type": "playback.started", "event_id": "ev-1",
                      "playback_id": playback_id, "position_ms": 0})
        from conftest import wait_ack

        ack = wait_ack(ws, "ev-1")
        assert ack["payload"]["ack"] == "ev-1"

        # 重复 event_id → duplicate，不重复计时
        ws.send_json({"type": "playback.progress", "event_id": "ev-1",
                      "playback_id": playback_id, "position_ms": 5000})
        ack = _recv_until(ws, lambda m: m.get("type") == "ack" and
                          m.get("correlation_id") == "ev-1")
        assert ack["payload"].get("duplicate") is True

        # progress 与 seek
        ws.send_json({"type": "playback.progress", "event_id": "ev-2",
                      "playback_id": playback_id, "position_ms": 6000})
        ws.send_json({"type": "playback.seeked", "event_id": "ev-3",
                      "playback_id": playback_id, "position_ms": 300_000})
        time.sleep(1.2)  # 真实观看 1.2s（started 已 ack，服务端计时窗口确定）

        # paused → 关闭 interval
        ws.send_json({"type": "playback.paused", "event_id": "ev-4",
                      "playback_id": playback_id, "position_ms": 300_000})
        _recv_until(ws, lambda m: m.get("type") == "ack" and
                    m.get("correlation_id") == "ev-4")
        time.sleep(0.3)

    with env.db.session() as s:
        from kindo.models import Playback, ViewingInterval

        pb = s.get(Playback, playback_id)
        assert pb.state == "paused"
        assert pb.position_ms == 300_000
        assert pb.watched_ms >= 800  # 实际播放约 1.2s，留 CI 慢机余量
        assert pb.watched_ms < 60_000  # seek 位移没有被计入（从 6s 跳到 300s 只计 1s+）
        intervals = s.query(ViewingInterval).filter(
            ViewingInterval.playback_id == playback_id
        ).all()
        assert len(intervals) == 1 and intervals[0].ended_at is not None


@requires_ffprobe
@pytest.mark.slow
def test_breakpoint_and_completion(library_env):
    """断点规则：>30s 且距结尾 >60s 才保存；90% 完成标记（§9.6）。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    media = _first_media(env, token, "第1集")
    duration = media["duration_ms"]
    body = _request_play(env, headers, media["media_id"]).json()
    playback_id = body["playback_id"]

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "b1",
                      "playback_id": playback_id, "position_ms": 0})
        # 直接看完（ended at 95%）
        ws.send_json({"type": "playback.ended", "event_id": "b2",
                      "playback_id": playback_id,
                      "position_ms": int(duration * 0.95)})
        time.sleep(0.5)

    r = env.client.get(f"/api/v1/media/{media['media_id']}", headers=headers).json()
    assert r["watch"]["completed"] is True

    # 断点：第二集看到中间暂停（8s 视频在 5s 处：距结尾 3s < 60s 阈值 → 不保存断点）
    m2 = _first_media(env, token, "第2集")
    body2 = _request_play(env, headers, m2["media_id"]).json()
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "c1",
                      "playback_id": body2["playback_id"], "position_ms": 0})
        ws.send_json({"type": "playback.paused", "event_id": "c2",
                      "playback_id": body2["playback_id"], "position_ms": 5000})
        time.sleep(0.4)
    r = env.client.get(f"/api/v1/media/{m2['media_id']}", headers=headers).json()
    assert m2["duration_ms"] < 65_000, "样本视频时长须短于断点阈值才能构造'不保存'分支"
    watch = r["watch"]
    assert watch is None or watch["last_position_ms"] == 0, (
        f"距结尾 <60s 不应保存断点，got last_position_ms={watch and watch['last_position_ms']}")


def _recv_until(ws, predicate, timeout=5.0):
    import json

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = json.loads(ws.receive_text())
        except Exception:
            return None
        if predicate(msg):
            return msg
    return None
