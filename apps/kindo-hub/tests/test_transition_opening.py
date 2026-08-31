"""成长接力开场白个性化（GRW-002）+ 家长声音（TTS-005）测试：

- LLM 生成开场白（承接刚播内容上下文），失败/超时/超长回退与清洗；
- 克隆可用时 transition.offer 携带 opening_audio_path，否则不携带；
- 未绑定 LLM 时保持模板开场（既有行为）。
"""
import asyncio
import time

from conftest import build_sample_library, requires_ffprobe
from kindo.providers.llm import LlmEvent
from kindo.providers.tts import TtsService


class FakeLlm:
    """同步可控的 LLM 适配器假件（异步生成器契约同 OpenAIChatCompletionsAdapter）。"""

    def __init__(self, text: str = "", error: bool = False):
        self.text = text
        self.error = error
        self.calls = 0

    async def generate(self, provider, messages, tools, request_id):
        self.calls += 1
        if self.error:
            yield LlmEvent(type="error", error="llm_test_boom")
            return
        yield LlmEvent(type="text_delta", text=self.text)
        yield LlmEvent(type="completed", finish_reason="stop")


class _StubVoiceStore:
    def exists(self) -> bool:
        return True

    def fingerprint(self) -> str:
        return "fp-test"

    def wav_base64(self) -> str:
        return ""

    def prompt_text(self) -> str:
        return "参考文本"


class _StubHubTts:
    """克隆容器桩：health ready + 声纹已载入 + 合成返回 WAV 字节。"""

    @property
    def configured(self) -> bool:
        return True

    async def health(self) -> dict:
        return {"ready": True, "voice_loaded": True}

    async def ensure_voice(self, wav_base64: str, prompt_text: str) -> bool:
        return True

    async def synthesize(self, tts_id: str, text: str) -> bytes:
        return b"RIFAKEWAV"


def _run_sync(coro) -> None:
    asyncio.run(coro)


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


def _prepare(env, llm, tts=None):
    """装配样例库 + 娱乐 0 预算 + 假 LLM/TTS + 事件捕获；返回事件列表。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    env.client.put("/api/v1/admin/policy", headers=env.admin_headers(), json={
        "allowed_windows": [], "content_scope": {}, "autoplay": True,
        "budgets": {"screen_total_minutes": 60,
                    "video_by_class": {"ENTERTAINMENT": 0}},
        "transition_policy": {"enabled": True, "max_minutes": 1,
                              "daily_offer_limit": 3},
    })
    # offer 前置检查要求注册了 LLM Provider（不实际调用——llm 已被假件替换）
    r = env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "display_name": "测试模型", "protocol": "openai_chat_completions",
        "base_url": "http://llm.test/v1", "model": "test-model", "api_key": "k",
    })
    assert r.status_code == 200, r.text

    events: list[tuple[str, dict]] = []
    env.state.transition._notify = (
        lambda device_id, event_type, payload, **kw: events.append((event_type, payload)))
    env.state.transition.bind(llm=llm, tts=tts, submit=_run_sync)
    return events


def _trigger_deny(env) -> None:
    _did, token = env.pair_device()
    headers = {"Authorization": f"Bearer {token}"}
    r = env.client.get("/api/v1/media?limit=50", headers=headers).json()
    ep = next(i for i in r["items"] if "汪汪队" in i["title"])
    resp = env.client.post("/api/v1/playbacks", headers=headers, json={
        "media_id": ep["media_id"], "action": "play", "source": "ui"})
    assert resp.status_code == 403
    env.state.transition.tick()


def _offer_payload(events) -> dict:
    return next(p for t, p in events if t == "transition.offer")


# ---------- 纯单元：开场白清洗 ----------

def test_sanitize_opening_strips_and_caps():
    from kindo.conversation.transition import TransitionOrchestrator

    s = TransitionOrchestrator._sanitize_opening
    assert s("「小海龟游得真远！要不要听听它的故事？」") == \
        "小海龟游得真远！要不要听听它的故事？"
    assert s("```text\n你好\n```") == "text 你好"  # 围栏剥除、换行压空白
    long_no_punct = "这" * 100
    assert s(long_no_punct) == "这" * 60 + "…"
    with_punct = "一句。二句。三句。" * 10
    out = s(with_punct)
    assert len(out) <= 60 and out.endswith("。")
    assert s("   ") is None


# ---------- 集成：LLM 开场 + 家长声音 ----------

@requires_ffprobe
def test_opening_generated_by_llm_with_clone_audio(env):
    llm = FakeLlm(text="小海龟游过大海去找妈妈啦！想不想听听海龟的故事？")
    tts = TtsService(hub_tts=_StubHubTts(), voice_store=_StubVoiceStore())
    events = _prepare(env, llm, tts=tts)
    _trigger_deny(env)

    assert llm.calls == 1
    payload = _offer_payload(events)
    assert payload["opening_text"] == "小海龟游过大海去找妈妈啦！想不想听听海龟的故事？"
    # 克隆可用：offer 携带 Hub 预合成音频路径（TV 端 hub_tts 优先播放）
    assert payload["opening_audio_path"].startswith("/api/v1/tts/")
    assert 1 <= len(payload["options"]) <= 3


@requires_ffprobe
def test_opening_llm_failure_falls_back_to_template_without_audio(env):
    llm = FakeLlm(error=True)
    tts = TtsService(hub_tts=_StubHubTts(), voice_store=_StubVoiceStore())
    events = _prepare(env, llm, tts=tts)
    _trigger_deny(env)

    payload = _offer_payload(events)
    # 模板兜底（GRW-002 的失败语义）；克隆仍可用故有音频路径
    assert "刚才的《" in payload["opening_text"]
    assert "opening_audio_path" in payload


@requires_ffprobe
def test_opening_without_clone_has_no_audio_path(env):
    llm = FakeLlm(text="刚才那集真好看！我们聊两句好不好？")
    events = _prepare(env, llm, tts=TtsService())  # 未配置克隆 → android_tts
    _trigger_deny(env)

    payload = _offer_payload(events)
    assert payload["opening_text"] == "刚才那集真好看！我们聊两句好不好？"
    assert "opening_audio_path" not in payload  # TV 本地系统 TTS 朗读兜底


@requires_ffprobe
def test_opening_overlong_llm_output_is_truncated(env):
    llm = FakeLlm(text="海洋里住着好多好多好朋友，" * 10)
    events = _prepare(env, llm, tts=TtsService())
    _trigger_deny(env)

    payload = _offer_payload(events)
    assert len(payload["opening_text"]) <= 61  # 60 + 省略号（无句读硬截断）
