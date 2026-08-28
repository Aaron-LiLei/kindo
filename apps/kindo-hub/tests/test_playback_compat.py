"""Direct Play 兼容兜底回归（2026-08-26 工程治理，§1.2"不支持时明确报错"闭环）。

覆盖：probe._judge 矩阵分支单测（此前零覆盖）；不兼容媒体播放请求 400 结构化
reason_code=media_not_playable + notes；详情 API compatibility 块。
"""
from __future__ import annotations

from kindo.media.probe import ProbeStream, _judge
from kindo.models import Media
from kindo.util import new_id


def _aud(*codecs: str) -> list[ProbeStream]:
    return [ProbeStream(index=i, kind="audio", codec=c) for i, c in enumerate(codecs)]


def test_judge_matrix_branches():
    # 基线：h264+aac in mp4 → 可播无提示
    assert _judge("mp4", "h264", _aud("aac")) == (True, [])
    # hevc 按矩阵为 FULL 可播（spec §1.2）
    assert _judge("mp4", "hevc", _aud("aac"))[0] is True
    # av1 / ac3 / eac3 → 可播 + "视设备"提示
    ok, notes = _judge("mp4", "av1", _aud("ac3", "eac3"))
    assert ok is True and len(notes) == 3
    # 矩阵外：mpeg4 视频 / dts 音频 / avi 容器 → 不可播 + notes
    ok, notes = _judge("mp4", "mpeg4", [])
    assert ok is False and any("视频编码" in n for n in notes)
    ok, notes = _judge("mp4", "h264", _aud("dts"))
    assert ok is False and any("音频编码" in n for n in notes)
    ok, notes = _judge("avi", "h264", [])
    assert ok is False and any("容器" in n for n in notes)


def _insert_media(env, *, playable: bool, notes: list[str], media_type: str = "movie"):
    mid = new_id()
    with env.db.session() as s:
        s.add(Media(id=mid, mount_id="family", path_key=f"/x/{mid}.mp4",
                    title="不兼容测试片", media_type=media_type, playable=playable,
                    probe_json={"container": "avi", "video_codec": "mpeg4", "notes": notes}))
        s.commit()
    return mid


def test_incompatible_media_denied_with_structured_reason(env):
    env.bootstrap_admin()
    _d, token = env.pair_device()
    mid = _insert_media(env, playable=False,
                        notes=["容器 avi 不在 V0.1 兼容矩阵，不承诺可播放"])
    r = env.client.post("/api/v1/playbacks",
                        json={"media_id": mid, "action": "play", "source": "ui"},
                        headers=env.device_headers(token))
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["details"]["reason_code"] == "media_not_playable"
    assert err["details"]["container"] == "avi"
    assert any("avi" in n for n in err["details"]["notes"])

    # 详情 API 携带兼容信息（TV 预检提示与 Admin 排查用）
    r = env.client.get(f"/api/v1/media/{mid}", headers=env.device_headers(token))
    assert r.status_code == 200
    compat = r.json()["compatibility"]
    assert compat["playable"] is False and compat["probed"] is True
    assert compat["container"] == "avi" and compat["video_codec"] == "mpeg4"
    assert any("avi" in n for n in compat["notes"])


def test_missing_media_denied_with_reason(env):
    env.bootstrap_admin()
    _d, token = env.pair_device()
    mid = _insert_media(env, playable=True, notes=[])
    with env.db.session() as s:
        s.get(Media, mid).missing = True
        s.commit()
    # REST 入口先行 404（媒体不存在）；service 层 media_missing 分支为纵深防御
    r = env.client.post("/api/v1/playbacks",
                        json={"media_id": mid, "action": "play", "source": "ui"},
                        headers=env.device_headers(token))
    assert r.status_code == 404
    # 直接调 service 验证结构化 reason（Voice/AI 路径 media 已加载的场景）
    from kindo.errors import KindoError

    with env.db.session() as s:
        from kindo.models import Device

        dev = s.query(Device).first()
        media = s.get(Media, mid)
        try:
            env.state.playback.request_playback(
                s, dev, media, "play", None, "ui", None)
            raised = None
        except KindoError as exc:
            raised = exc
        assert raised is not None and raised.details["reason_code"] == "media_missing"
