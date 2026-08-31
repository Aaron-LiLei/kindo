"""兴趣信号反哺（REC-002/ANA-007）测试：

- 信号写入：selected（选择播放，来源 browse/ai/transition）与 watched（自然看完）
  在播放统一入口/ended 事件落库（此前仅 transition_joined/rejected 有写入方）；
- 首页 explore_themes 按近 30 天兴趣信号频次前置排序（使用行为排序）；
- 接力选项查找池并入近期兴趣主题（刚播主题 ∪ 兴趣主题），标签无刚播主题时
  用兴趣主题。
"""
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


def _setup(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    _did, token = env.pair_device()
    return {"Authorization": f"Bearer {token}"}


def _media(env, headers, part):
    r = env.client.get("/api/v1/media?limit=50", headers=headers).json()
    return next(i for i in r["items"] if part in i["title"])


def _signals(env, signal_type=None):
    from kindo.models import InterestSignal

    with env.db.session() as s:
        q = s.query(InterestSignal)
        if signal_type:
            q = q.filter_by(signal_type=signal_type)
        return [(r.signal_type, r.source, r.entity_id) for r in q.all()]


@requires_ffprobe
def test_playback_records_selected_and_watched_signals(env):
    headers = _setup(env)
    ep = _media(env, headers, "第1集")

    with env.client.websocket_connect(
            f"/api/v1/realtime?token={headers['Authorization'][7:]}") as ws:
        r = env.client.post("/api/v1/playbacks", headers=headers, json={
            "media_id": ep["media_id"], "action": "play", "source": "ui"})
        assert r.status_code == 200, r.text
        pb_id = r.json()["playback_id"]
        # 选择即记录（ANA-007：来源 browse）
        selected = _signals(env, "selected")
        assert ("selected", "browse", selected[0][2]) in selected
        assert selected[0][2]  # entity 引用非空（统一目录实体）

        ws.send_json({"type": "playback.started", "event_id": "iv-1",
                      "playback_id": pb_id, "position_ms": 0})
        ws.send_json({"type": "playback.ended", "event_id": "iv-2",
                      "playback_id": pb_id, "position_ms": 8000})
        for _ in range(20):
            if _signals(env, "watched"):
                break
            time.sleep(0.3)
        watched = _signals(env, "watched")
        assert ("watched", "browse", watched[0][2]) in watched

    # AI 来源的选择记为 ai（agent play_media 同入口）
    r = env.client.post("/api/v1/playbacks", headers=headers, json={
        "media_id": ep["media_id"], "action": "play", "source": "ai"})
    assert r.status_code == 200, r.text
    assert ("selected", "ai", None) in [
        (t, src, None) for t, src, _e in _signals(env, "selected")]


@requires_ffprobe
def test_home_orders_themes_by_interest(env):
    headers = _setup(env)
    ep2 = _media(env, headers, "第2集")  # 主题：海洋

    with env.client.websocket_connect(
            f"/api/v1/realtime?token={headers['Authorization'][7:]}") as ws:
        for i in range(2):  # 两次完整观看 → 海洋主题信号累积
            r = env.client.post("/api/v1/playbacks", headers=headers, json={
                "media_id": ep2["media_id"], "action": "play", "source": "ui"})
            pb_id = r.json()["playback_id"]
            ws.send_json({"type": "playback.started", "event_id": f"hs-{i}",
                          "playback_id": pb_id, "position_ms": 0})
            ws.send_json({"type": "playback.ended", "event_id": f"he-{i}",
                          "playback_id": pb_id, "position_ms": 8000})
            time.sleep(0.2)

    home = env.client.get("/api/v1/home", headers=headers).json()
    themes = home["explore_themes"]
    assert "海洋" in themes
    # 兴趣反哺（REC-002）：孩子反复接触的主题排到最前
    assert themes[0] == "海洋"


@requires_ffprobe
def test_transition_options_weighted_by_interest(env):
    headers = _setup(env)
    from kindo.models import ContentEntity, ContentTopic, EntityTopic, InterestSignal
    from kindo.util import new_id

    with env.db.session() as s:
        profile_id = env.state.playback.default_profile_id(s)
        # 插入音频实体（海洋主题）——选项查找池的候选
        ocean = s.query(ContentTopic).filter_by(name="海洋").one()
        audio = ContentEntity(id=new_id(), entity_type="story", title="海洋的故事",
                              content_class="STORY", modality="AUDIO")
        s.add(audio)
        s.flush()
        s.add(EntityTopic(entity_id=audio.id, topic_id=ocean.id))
        # 孩子近期兴趣：第2集（海洋主题）反复观看 ×3
        ent2 = (s.query(ContentEntity)
                .filter(ContentEntity.title.like("%第2集%")).one())
        for _ in range(3):
            s.add(InterestSignal(id=new_id(), profile_id=profile_id,
                                 entity_id=ent2.id, signal_type="watched",
                                 source="browse"))
        s.commit()

    env.client.put("/api/v1/admin/policy", headers=env.admin_headers(), json={
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 60,
                    "video_by_class": {"ENTERTAINMENT": 0}},
        "transition_policy": {"enabled": True, "max_minutes": 1,
                              "daily_offer_limit": 3},
    })
    env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "display_name": "测试模型", "protocol": "openai_chat_completions",
        "base_url": "http://llm.test/v1", "model": "test-model", "api_key": "k"})

    events: list[tuple[str, dict]] = []
    env.state.transition._notify = (
        lambda device_id, event_type, payload, **kw: events.append((event_type, payload)))

    # 刚播内容 = 第1集（救援/合作，与兴趣主题"海洋"不同）
    ep1 = _media(env, headers, "第1集")
    r = env.client.post("/api/v1/playbacks", headers=headers, json={
        "media_id": ep1["media_id"], "action": "play", "source": "ui"})
    assert r.status_code == 403
    env.state.transition.tick()
    for _ in range(30):
        if any(t == "transition.offer" for t, _p in events):
            break
        time.sleep(0.3)
    payload = next(p for t, p in events if t == "transition.offer")

    song = next(o for o in payload["options"] if o["type"] == "song_story")
    # 查找池 = 刚播主题(救援/合作) ∪ 兴趣主题(海洋)；音频实体经兴趣主题命中
    assert "海洋" in song["topics"]
    # 主题按 sorted() 稳定取首项融入句子（合作 U+5408 < 救援 U+6551）；其余
    # 选项保持通用短句不带括号后缀（UX 视觉审查 2026-08-31：去「相关」
    # 成人书面语与括号后缀；不排序会因主题行创建顺序漂移而偶现失败）
    assert song["label"] == "听个合作的故事"
    other = [o for o in payload["options"] if o["type"] != "song_story"]
    for o in other:
        assert "（" not in o["label"]
