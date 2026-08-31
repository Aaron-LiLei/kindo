"""家长声音讲故事（对话内朗读，2026-08-31）测试：

- sidecar story_text 入库：音频文件 sidecar 声明 entity_type=story + story_text
  → 扫描写入 content_entity.story_text（超长截断 3000）；
- read_story Tool：标题命中 / 主题命中 / 无匹配返回候选澄清 / 无可读故事提示；
- 编排器直接播报：原文分句 delta + tts.request（克隆回退链由 TtsService 承担），
  末句 last_tts_id 驱动追问窗口，final 文本 = 全文拼接；原文不经 LLM。
"""
import asyncio
import time
from types import SimpleNamespace

from conftest import requires_ffprobe

_STORY_TEXT = (
    "小星星住在高高的天空上。每天晚上，她都对着大海眨眼睛。\n"
    "有一天，小海龟仰起头问：“你为什么一闪一闪的呀？”"
    "小星星笑着说：“我在给迷路的小船指路呢。”小海龟听了，开心地游回家了。"
)


def _add_story_library(env) -> None:
    import yaml as _yaml

    d = env.media_dir / "stories"
    d.mkdir(parents=True, exist_ok=True)
    (d / "小星星的故事.mp3").write_bytes(b"ID3" + b"\x00" * 128)
    (d / "小星星的故事.kindo.yaml").write_text(_yaml.safe_dump({
        "entity_type": "story",
        "title": "小星星的故事",
        "themes": ["海洋", "睡前"],
        "story_text": _STORY_TEXT,
    }, allow_unicode=True), encoding="utf-8")
    # 第二个故事：用于澄清与兴趣路径
    (d / "小海龟回家.mp3").write_bytes(b"ID3" + b"\x00" * 128)
    (d / "小海龟回家.kindo.yaml").write_text(_yaml.safe_dump({
        "entity_type": "story",
        "title": "小海龟回家",
        "themes": ["海洋"],
        "story_text": "小海龟慢慢爬呀爬，终于回到了大海。",
    }, allow_unicode=True), encoding="utf-8")


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


def _runtime(env):
    from kindo.agent.tools import ToolRuntime

    return ToolRuntime(env.db.session_factory, env.state.policy,
                       env.state.playback, env.state.history)


def _conv():
    from kindo.conversation.service import ConversationSession

    return ConversationSession(session_id="s-read", device_id="d1",
                               profile_id="default", provider_id="p", model_id="m")


def _device(env):
    from kindo.models import Device

    with env.db.session() as s:
        return s.query(Device).first()


def _call_tool(env, args):
    rt = _runtime(env)
    with env.db.session():
        return rt.execute(_conv(), _device(env), "default", "read_story", args, "call-1")


@requires_ffprobe
def test_story_text_ingest_from_sidecar(env):
    from conftest import build_sample_library

    build_sample_library(env.media_dir)
    _add_story_library(env)
    env.bootstrap_admin()
    _scan(env)

    from kindo.models import ContentEntity

    with env.db.session() as s:
        story = (s.query(ContentEntity)
                 .filter(ContentEntity.title == "小星星的故事").one())
        assert story.entity_type == "story"
        assert story.story_text == _STORY_TEXT
        assert story.modality == "AUDIO"
        # song 实体不带朗读文本
        assert (s.query(ContentEntity)
                .filter(ContentEntity.title == "小海龟回家").one().story_text
                is not None)


@requires_ffprobe
def test_read_story_tool_match_and_clarify(env):
    env.bootstrap_admin()
    _add_story_library(env)
    _scan(env)

    # 标题命中 → direct_speak 结果（原文只做播报素材）
    r = _call_tool(env, {"query": "小星星"})
    assert r["status"] == "ok"
    assert r["data"]["direct_speak"] is True
    assert r["data"]["title"] == "小星星的故事"
    assert r["data"]["speak_text"] == _STORY_TEXT

    # 主题命中两个 → 返回候选由 LLM 澄清
    r = _call_tool(env, {"query": "海洋"})
    assert r["status"] == "clarify"
    titles = {c["title"] for c in r["data"]["candidates"]}
    assert titles == {"小星星的故事", "小海龟回家"}

    # 无偏好 → 无近期兴趣信号时取字典序第一个（确定性）
    r = _call_tool(env, {})
    assert r["status"] == "ok"
    assert r["data"]["title"] in ("小星星的故事", "小海龟回家")

    # 无可读故事（清空后）→ not_found 提示
    from kindo.models import ContentEntity

    with env.db.session() as s:
        s.query(ContentEntity).filter(
            ContentEntity.entity_type == "story").update({"story_text": None})
        s.commit()
    r = _call_tool(env, {"query": "小星星"})
    assert r["status"] == "not_found"
    assert r["message_hint"]


def test_speak_story_direct_streaming():
    """编排器直接播报（不经 LLM）：分句 delta + tts.request + final 全文。"""
    from kindo.conversation.orchestrator import Orchestrator, split_sentences
    from kindo.providers.tts import TtsService

    orch = Orchestrator.__new__(Orchestrator)
    events: list[tuple[str, dict]] = []
    orch._realtime = SimpleNamespace(
        emit=lambda device_id, event_type, payload, **kw: events.append((event_type, payload)))
    orch._tts = TtsService()  # 未配置克隆 → android_tts 指令（无网络 IO）
    conv = _conv()

    asyncio.run(orch._speak_story(conv, _STORY_TEXT))

    deltas = [p["delta"] for t, p in events if t == "assistant.text.delta"]
    tts_reqs = [p for t, p in events if t == "tts.request"]
    finals = [p for t, p in events if t == "assistant.text.final"]
    assert len(deltas) == len(split_sentences(_STORY_TEXT)) >= 3
    assert "".join(deltas) == finals[0]["text"]
    assert finals[0]["text"] == _STORY_TEXT.replace("\n", "")  # 句读含换行，切分后剥离
    assert len(tts_reqs) == len(deltas)
    assert all(p["provider"] == "android_tts" for p in tts_reqs)
    # 末句 last_tts_id（仅末句 tts.finished 开追问窗口）
    assert conv.last_tts_id == tts_reqs[-1]["tts_id"]
    assert set(conv.tts_to_session) == {p["tts_id"] for p in tts_reqs}

    # 无句读超长强制切分（与流式播报同规则）
    long_text = "海" * 250
    assert max(len(s) for s in split_sentences(long_text)) <= 100
