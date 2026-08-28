"""测试夹具：临时环境、配对设备、管理员会话、样本媒体库。"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_WINGET_GLOB = (Path.home() / "AppData/Local/Microsoft/WinGet/Packages").glob(
    "Gyan.FFmpeg*/ffmpeg-*/bin/ffprobe.exe"
)


def find_ffprobe() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    for p in _WINGET_GLOB:
        return str(p)
    return None


FFPROBE = find_ffprobe()
requires_ffprobe = pytest.mark.skipif(FFPROBE is None, reason="ffprobe 不可用")


def write_config(tmp: Path, *, media_dir: Path, data_dir: Path, asr_endpoint: str = "",
                 llm_base_url: str = "", port: int = 18090) -> Path:
    ffprobe_block = ""
    if FFPROBE:
        ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg")
        ffprobe_block = f"""
tools:
  ffprobe_path: "{Path(FFPROBE).as_posix()}"
  ffmpeg_path: "{Path(ffmpeg).as_posix()}"
"""
    cfg = tmp / "kindo.yaml"
    llm_block = ""
    if llm_base_url:
        llm_block = f"""
llm_providers:
  - id: main
    display_name: Test LLM
    protocol: openai_chat_completions
    base_url: {llm_base_url}
    model: test-model
"""
    cfg.write_text(
        f"""
server:
  bind: "127.0.0.1"
  port: {port}
  mdns_enabled: false
data_dir: "{data_dir.as_posix()}"
media_mounts:
  - id: family
    path: "{media_dir.as_posix()}"
    read_only: true
asr:
  endpoint: "{asr_endpoint}"
{ffprobe_block}{llm_block}
""",
        encoding="utf-8",
    )
    return cfg


class Env:
    """一个完整可用的 Hub 测试环境。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.media_dir = tmp / "media"
        self.data_dir = tmp / "data"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.port = 18000 + (uuid.uuid4().int % 3000)
        self._saved_env = {
            k: os.environ.get(k) for k in ("KINDO_CONFIG", "KINDO_ADMIN_BOOTSTRAP_TOKEN")
        }
        os.environ["KINDO_CONFIG"] = str(write_config(
            tmp, media_dir=self.media_dir, data_dir=self.data_dir, port=self.port
        ))
        os.environ["KINDO_ADMIN_BOOTSTRAP_TOKEN"] = "test-bootstrap-token"
        from kindo.app import create_app
        from kindo.config import load_config

        self.app = create_app(load_config())
        self.client = TestClient(self.app)
        self.state = self.app.state.kindo
        self.db = self.state.db

    def restore_env(self) -> None:
        """还原构造时污染的环境变量（此前 KINDO_ADMIN_BOOTSTRAP_TOKEN 会泄漏）。"""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def reconfigure(self, *, asr_endpoint: str = "", llm_base_url: str = "") -> None:
        write_config(self.tmp, media_dir=self.media_dir, data_dir=self.data_dir,
                     asr_endpoint=asr_endpoint, llm_base_url=llm_base_url, port=self.port)

    # ---------- 管理员 ----------

    def bootstrap_admin(self, username: str = "admin", password: str = "password123") -> dict:
        r = self.client.post("/api/v1/admin/auth/bootstrap", json={
            "username": username, "password": password,
            "bootstrap_token": "test-bootstrap-token",
        })
        assert r.status_code == 200, r.text
        return self.login_admin(username, password)

    def login_admin(self, username: str = "admin", password: str = "password123") -> dict:
        r = self.client.post("/api/v1/admin/auth/login", json={
            "username": username, "password": password,
        })
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def admin_headers(self) -> dict:
        return {"X-CSRF-Token": self.csrf}

    # ---------- 设备配对 ----------

    def pair_device(self, name: str = "测试电视") -> tuple[str, str]:
        """返回 (device_id, device_token)。走完整 pairing 流程。"""
        r = self.client.post("/api/v1/pairing/requests", json={
            "device_name": name, "app_instance_id": f"app-{uuid.uuid4().hex[:8]}",
            "capabilities": {"mic": True, "tts": "android"},
        })
        assert r.status_code == 200, r.text
        pr = r.json()
        if not hasattr(self, "csrf"):
            self.bootstrap_admin()
        r = self.client.post(
            f"/api/v1/admin/pairing/requests/{pr['pairing_id']}/approve",
            json={"confirm_code": pr["display_code"]},
            headers=self.admin_headers(),
        )
        assert r.status_code == 200, r.text
        device_id = r.json()["device_id"]
        r = self.client.get(
            f"/api/v1/pairing/requests/{pr['pairing_id']}",
            params={"pairing_secret": pr["pairing_secret"]},
        )
        assert r.status_code == 200, r.text
        token = r.json()["device_token"]
        assert token, "device_token 应在批准后首次拉取返回一次"
        # 再拉一次不应再返回 token
        r2 = self.client.get(
            f"/api/v1/pairing/requests/{pr['pairing_id']}",
            params={"pairing_secret": pr["pairing_secret"]},
        )
        assert "device_token" not in r2.json()
        return device_id, token

    def device_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def env(tmp_path: Path):
    e = Env(tmp_path)
    with e.client:
        yield e
    e.restore_env()


def make_sample_video(path: Path, seconds: int = 8, text: str = "") -> None:
    """用 ffmpeg 生成小体积测试视频（H.264/AAC MP4）。"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg") if FFPROBE else "ffmpeg"
    cmd[0] = ffmpeg
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)


def make_sample_image(path: Path, color: str = "orange") -> None:
    """生成单色测试图片（海报源）。"""
    ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg") if FFPROBE else "ffmpeg"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=320x240:d=1", "-frames:v", "1", str(path)],
        check=True, capture_output=True, timeout=60,
    )


SAMPLE_LIBRARY = {
    "series/汪汪队/S01E01.mkv": {
        "title": "汪汪队立大功 第一季 第1集",
        "series": {"name": "汪汪队立大功", "season_no": 1, "episode_no": 1},
        "characters": ["天天", "阿奇", "毛毛"],
        "themes": ["救援", "合作"],
    },
    "series/汪汪队/S01E02.mkv": {
        "title": "汪汪队立大功 第一季 第2集",
        "series": {"name": "汪汪队立大功", "season_no": 1, "episode_no": 2},
        "characters": ["天天", "小砾"],
        "themes": ["海洋"],
    },
    "courses/英语启蒙/L01.mp4": {
        "title": "英语启蒙 第1课",
        "course": {"name": "英语启蒙", "chapter_no": 1, "lesson_no": 1},
        "language": "en-US",
        "themes": ["英语", "数字"],
    },
    "movies/海底小纵队.mp4": {
        "title": "海底小纵队大电影",
        "characters": ["巴克队长", "皮医生"],
        "themes": ["海洋", "动物"],
    },
}


def build_sample_library(media_dir: Path) -> None:
    """生成样本媒体 + 目录级/文件级 sidecar + 外置字幕。"""
    import yaml

    for rel, meta in SAMPLE_LIBRARY.items():
        target = media_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        make_sample_video(target, seconds=8)
        sidecar = target.parent / (target.stem + ".kindo.yaml")
        sidecar.write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")

    # 外置字幕：S01E01 的中文字幕（含"天天"台词，用于 Grounding）
    srt = media_dir / "series/汪汪队/S01E01.zh.srt"
    srt.write_text(
        """1
00:00:00,500 --> 00:00:03,000
天天，快来！我们的飞行装备准备好了

2
00:00:03,500 --> 00:00:06,000
汪汪队，出发救援！<b>今天</b>的任务在海边

3
00:00:06,500 --> 00:00:08,000
好耶，我们去救小海豹
""",
        encoding="utf-8",
    )

    # 目录级 sidecar 默认值（poster 显式声明 → 海报源优先级 1）
    (media_dir / "movies/kindo.yaml").write_text(
        yaml.safe_dump({"language": "zh-CN", "age_band": "3-6", "poster": "cover.jpg"}),
        encoding="utf-8",
    )
    make_sample_image(media_dir / "movies/cover.jpg", color="steelblue")

    # 同名约定式海报（→ 优先级 2）；S01E01 / L01 无图源，走本地抽帧兜底
    make_sample_image(media_dir / "series/汪汪队/S01E02.jpg", color="seagreen")


def wait_ack(ws, event_id: str, timeout_s: float = 10.0) -> dict:
    """阻塞读取 WS 直到指定 event_id 的 ack（确保服务端已处理该事件）。"""
    import json as _json
    import time as _time

    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            msg = _json.loads(ws.receive_text())
        except Exception:
            break
        if msg.get("type") == "ack" and msg.get("correlation_id") == event_id:
            return msg
    raise AssertionError(f"未收到 {event_id} 的 ack")
