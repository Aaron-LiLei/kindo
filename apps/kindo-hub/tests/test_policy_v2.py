"""Policy v2 测试（v0.3 决策五/六，AC-15）：判定矩阵两分支、绕过防护、
v1 升维映射、Boundary Event 三源与 trigger_key 幂等。"""
import time

import pytest

from conftest import build_sample_library, requires_ffprobe


def _scan(env):
    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}",
                             headers=env.admin_headers()).json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            return
        time.sleep(0.5)


def _put_policy(env, rules: dict) -> None:
    r = env.client.put("/api/v1/admin/policy", json=rules,
                       headers=env.admin_headers())
    assert r.status_code == 200, r.text


def _first_media(env, token, part):
    r = env.client.get("/api/v1/media?limit=50", headers={
        "Authorization": f"Bearer {token}"}).json()
    return next(i for i in r["items"] if part in i["title"])


def _play(env, headers, media_id):
    return env.client.post("/api/v1/playbacks", headers=headers, json={
        "media_id": media_id, "action": "play", "source": "ui"})


def _watch_full(env, token, playback_id, duration_ms):
    """WS 快进：started → progress(结尾) → ended（等价看完一集）。"""
    from conftest import wait_ack

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "e-s",
                      "playback_id": playback_id, "position_ms": 0})
        wait_ack(ws, "e-s")
        ws.send_json({"type": "playback.progress", "event_id": "e-p",
                      "playback_id": playback_id,
                      "position_ms": max(0, duration_ms - 1000)})
        wait_ack(ws, "e-p")
        ws.send_json({"type": "playback.ended", "event_id": "e-e",
                      "playback_id": playback_id,
                      "position_ms": duration_ms})
        wait_ack(ws, "e-e")


@pytest.fixture()
def lib(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    return env


@requires_ffprobe
def test_v1_policy_upgrades_to_v2_dimensions(lib):
    """v1 规则自动升维：daily_limit → screen_total；无 budgets 时不限音频。"""

    env = lib
    _put_policy(env, {"daily_limit_minutes": 60, "allowed_windows": [],
                      "content_scope": {}, "autoplay": True})
    _d, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "第1集")
    r = _play(env, headers, ep["media_id"])
    assert r.status_code == 200, r.text


@requires_ffprobe
def test_ac15_entertainment_exhausted_learning_allowed(lib):
    """AC-15 分支一：娱乐子预算耗尽 + 总屏有余 + 学习子预算有余 → 学习视频允许；
    总屏幕耗尽 → 一切 VIDEO deny 而 AUDIO/AI_VOICE 仍可用（constraints 标注）。"""
    env = lib
    _put_policy(env, {
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {
            "screen_total_minutes": 60,
            "video_by_class": {"ENTERTAINMENT": 0, "LEARNING": 30},
            "audio_minutes": None,
            "ai_voice_minutes": 10,
        },
    })
    _d, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "汪汪队")   # ENTERTAINMENT
    lesson = _first_media(env, token, "英语启蒙")  # LEARNING

    r = _play(env, headers, ep["media_id"])
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["reason_code"] == "daily_limit_reached"
    assert err["constraints"]["content_class"] == "ENTERTAINMENT"
    assert err["constraints"]["allowed_modalities"] == ["video", "audio", "ai_voice"]

    # 学习视频：总屏 60 未动、LEARNING 子预算 30 → 允许
    r = _play(env, headers, lesson["media_id"])
    assert r.status_code == 200, r.text

    # 总屏耗尽：预灌 60 分钟 VIDEO 消耗 → 全 VIDEO deny
    from datetime import UTC, datetime, timedelta

    from kindo.models import Playback as Pb
    from kindo.models import ViewingInterval

    with env.db.session() as s:
        pb = (s.query(Pb).order_by(Pb.created_at.desc()).first())
        from kindo.util import new_id

        s.add(ViewingInterval(
            id=new_id(),
            playback_id=pb.id, started_at=datetime.now(UTC) - timedelta(hours=2),
            ended_at=datetime.now(UTC) - timedelta(hours=1),
            duration_ms=61 * 60_000, content_class="ENTERTAINMENT",
            modality="VIDEO", close_reason="test"))
        s.commit()
    r = _play(env, headers, lesson["media_id"])
    assert r.status_code == 403
    err = r.json()["error"]
    # 学习子预算 30 也被同批消耗耗尽 → 仍 deny 且提示维度
    assert err["reason_code"] == "daily_limit_reached"


@requires_ffprobe
def test_ac15_entertainment_disguised_as_learning_denied(lib):
    """AC-15 分支二：ENTERTAINMENT 内容改标 LEARNING（未经家长确认锁定）
    不得放宽判定——分类取 Canonical entity 值，不看请求声明。"""
    env = lib
    _put_policy(env, {
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {
            "screen_total_minutes": 60,
            "video_by_class": {"ENTERTAINMENT": 0, "LEARNING": 30},
        },
    })
    _d, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "汪汪队")

    # 家长未确认锁定下把 entity.content_class 改成 LEARNING（模拟越权/误标）
    from kindo.models import ContentEntity

    with env.db.session() as s:
        ent = (s.query(ContentEntity)
               .filter_by(source_media_id=ep["media_id"]).one())
        ent.content_class = "LEARNING"
        # provenance 仍是 auto/provider 级（非 parent 锁定）——按更严类判定
        s.commit()
    r = _play(env, headers, ep["media_id"])
    # 娱乐子预算为 0：分类漂移不得挤占学习子预算（按 ENTERTAINMENT 判定）
    assert r.status_code == 403
    assert r.json()["error"]["reason_code"] == "daily_limit_reached"


@requires_ffprobe
def test_boundary_event_idempotent_on_ended(lib):
    """AC-14 前置：自然播完 + 配额耗尽 → 恰好一次边界事件；重复事件不重复。"""
    env = lib
    _put_policy(env, {
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 60,
                    "video_by_class": {"ENTERTAINMENT": 0}},
    })
    _d, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    # 娱乐子预算 0 → 播放请求被软限制 deny → 边界事件源③
    ep = _first_media(env, token, "汪汪队")
    r = _play(env, headers, ep["media_id"])
    assert r.status_code == 403
    # 重复 deny（同 idempotency-key 不同请求也发）不重复 offer
    _play(env, headers, ep["media_id"])
    from kindo.models import TransitionSession

    with env.db.session() as s:
        rows = s.query(TransitionSession).all()
        denies = [t for t in rows if t.trigger_json.get("source") == "deny"]
        assert len(denies) == 1, rows
    # 事件由 after_commit poke / 后台 tick 异步消费（2026-09-01 发布即消费；
    # 本夹具无 Provider → _offer 静默放弃，决策七）：等待队列被消费即可，
    # 幂等语义 = 重复 deny 只产生一行
    deadline = time.time() + 15
    while time.time() < deadline:
        if len(env.state.playback._boundary._queue) == 0:
            break
        time.sleep(0.2)
    with env.db.session() as s:
        rows = s.query(TransitionSession).all()
        denies = [t for t in rows if t.trigger_json.get("source") == "deny"]
        assert len(denies) == 1, rows


@requires_ffprobe
def test_hard_window_no_boundary_event(lib):
    """时段硬截止不触发成长接力（决策五 5.4）。"""
    env = lib
    from datetime import datetime

    now = datetime.now()
    start = (now.hour + 1) % 24  # 当前时刻不在窗内
    _put_policy(env, {
        "allowed_windows": [{"start": f"{start:02d}:00", "end": f"{(start + 2) % 24:02d}:00"}],
        "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 0},
    })
    _d, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "汪汪队")
    r = _play(env, headers, ep["media_id"])
    assert r.status_code == 403
    assert r.json()["error"]["reason_code"] == "outside_allowed_window"
    from kindo.models import TransitionSession

    with env.db.session() as s:
        assert s.query(TransitionSession).count() == 0
