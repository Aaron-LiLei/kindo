"""Growth Transition 集成测试（v0.3 决策六/七，AC-14）：deny 触发 → offer →
拒绝即止 → 当日不重复；时间盒；音频路由受预算约束；兴趣信号落库。"""
import json
import time

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


def _configure(env):
    env.client.put("/api/v1/admin/policy", headers=env.admin_headers(), json={
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 60,
                    "video_by_class": {"ENTERTAINMENT": 0}},
        "transition_policy": {"enabled": True, "max_minutes": 1,
                              "daily_offer_limit": 1},
    })
    # 配一个 LLM Provider（offer 前置检查 LLM 可用性；不实际调用）
    r = env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "display_name": "测试模型", "protocol": "openai_chat_completions",
        "base_url": "http://llm.test/v1", "model": "test-model", "api_key": "k",
    })
    assert r.status_code == 200, r.text


def _first_media(env, token, part):
    r = env.client.get("/api/v1/media?limit=50", headers={
        "Authorization": f"Bearer {token}"}).json()
    return next(i for i in r["items"] if part in i["title"])


def _recv_until(ws, pred, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = json.loads(ws.receive_text())
        if pred(msg):
            return msg
    raise AssertionError("等待事件超时")


@requires_ffprobe
def test_transition_full_lifecycle_reject_and_daily_limit(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    _configure(env)
    _did, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "汪汪队")

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        # deny → boundary → tick → transition.offer（幂等：单次）
        r = env.client.post("/api/v1/playbacks", headers=headers, json={
            "media_id": ep["media_id"], "action": "play", "source": "ui"})
        assert r.status_code == 403
        env.state.transition.tick()
        offer = _recv_until(ws, lambda m: m["type"] == "transition.offer")
        assert offer["payload"]["opening_text"]
        assert 1 <= len(offer["payload"]["options"]) <= 3
        transition_id = offer["payload"]["transition_id"]

        # 拒绝即止（硬性约束 11）：立即 ended(rejected)，不重复说服
        ws.send_json({"type": "transition.reject", "event_id": "tr-r",
                      "transition_id": transition_id})
        ended = _recv_until(
            ws, lambda m: m["type"] == "transition.ended"
            and m["payload"]["transition_id"] == transition_id)
        assert ended["payload"]["ended_reason"] == "rejected"

    from kindo.models import InterestSignal, TransitionSession

    with env.db.session() as s:
        ts = s.get(TransitionSession, transition_id)
        assert ts.state == "ended" and ts.rejected and ts.ended_reason == "rejected"
        sig = (s.query(InterestSignal)
               .filter_by(signal_type="transition_rejected").one())
        assert sig.source == "transition"

    # 当日频控（daily_offer_limit=1）：再次 deny 不再 offer
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        env.client.post("/api/v1/playbacks", headers=headers, json={
            "media_id": ep["media_id"], "action": "play", "source": "ui"})
        env.state.transition.tick()
        from kindo.models import TransitionSession as TS

        with env.db.session() as s:
            offers = s.query(TS).filter_by(profile_id=ts.profile_id).count()
            assert offers == 1  # 没有新建 transition 行


@requires_ffprobe
def test_transition_select_interaction_and_deadline(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    env.client.put("/api/v1/admin/policy", headers=env.admin_headers(), json={
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 60,
                    "video_by_class": {"ENTERTAINMENT": 0}},
        "transition_policy": {"enabled": True, "max_minutes": 5,
                              "daily_offer_limit": 3},
    })
    env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "display_name": "测试模型", "protocol": "openai_chat_completions",
        "base_url": "http://llm.test/v1", "model": "test-model", "api_key": "k"})
    _did, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    ep = _first_media(env, token, "汪汪队")

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        env.client.post("/api/v1/playbacks", headers=headers, json={
            "media_id": ep["media_id"], "action": "play", "source": "ui"})
        env.state.transition.tick()
        offer = _recv_until(ws, lambda m: m["type"] == "transition.offer")
        tid = offer["payload"]["transition_id"]

        # 选择 knowledge → interaction
        ws.send_json({"type": "transition.select", "event_id": "tr-s",
                      "transition_id": tid, "option_type": "knowledge"})
        state = _recv_until(
            ws, lambda m: m["type"] == "transition.state"
            and m["payload"].get("state") == "interaction")
        assert state["payload"]["selected_type"] == "knowledge"

    # 时间盒：select 后把 deadline 拨到过去，tick 收尾 ended(timeout)
    from datetime import UTC, datetime, timedelta

    from kindo.models import TransitionSession

    with env.db.session() as s:
        row = s.get(TransitionSession, tid)
        row.deadline = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    env.state.transition.tick()
    from kindo.models import InterestSignal, TransitionSession

    with env.db.session() as s:
        ts = s.get(TransitionSession, tid)
        assert ts.state == "ended" and ts.ended_reason == "timeout"
        assert ts.accepted and ts.selected_type == "knowledge"
        sig = (s.query(InterestSignal)
               .filter_by(signal_type="transition_joined").one())
        assert sig.entity_id is not None  # 兴趣信号带内容引用
