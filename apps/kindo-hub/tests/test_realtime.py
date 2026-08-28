"""Realtime：seq 单调、断线重放、sync.required（技术方案 §4.2）。"""
import json


def test_seq_monotonic_and_replay(env):
    env.bootstrap_admin()
    _d, token = env.pair_device()

    # 设备离线时 emit 也推进 seq（供重连重放）
    state = env.state
    for i in range(3):
        state.realtime.emit(_d, "conversation.state", {"state": "thinking", "i": i})

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        # hello with last_server_seq=0 → 重放全部
        ws.send_json({"type": "hello", "last_server_seq": 0})
        seqs = []
        while len(seqs) < 3:
            msg = json.loads(ws.receive_text())
            if "seq" in msg:
                seqs.append(msg["seq"])
        assert seqs == sorted(seqs), "seq 必须单调递增"
        last = seqs[-1]

        # 再发一条新事件并重连重放
        state.realtime.emit(_d, "conversation.state", {"state": "speaking"})
        ws.send_json({"type": "hello", "last_server_seq": last})
        msg = json.loads(ws.receive_text())
        assert msg["seq"] == last + 1


def test_sync_required_when_window_missed(env):
    """重放窗口（256 事件/60s）不足 → sync.required（§4.1）。"""
    env.bootstrap_admin()
    _d, token = env.pair_device()
    state = env.state
    for _ in range(300):  # 超过 ring buffer 容量
        state.realtime.emit(_d, "assistant.text.delta", {"delta": "x"})

    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "hello", "last_server_seq": 0})
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "sync.required"


async def test_realtime_disconnect_does_not_end_session(env):
    """Realtime 断开不结束 Conversation Session（§4.2）。"""
    env.bootstrap_admin()

    _d, token = env.pair_device()
    conv = env.state.conversation_manager.create(_d, "default", "p1", "m1", None)
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}"):
        pass  # 立即断开
    still = env.state.conversation_manager.get_optional(conv.session_id)
    assert still is not None


def test_invalid_ws_token_rejected(env):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/api/v1/realtime?token=bad"):
            pass
