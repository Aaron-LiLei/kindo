"""本地 sidecar overlay + 目录索引去请求 + PATCH 系列归组（2026-08-20 直连网盘模式）。

覆盖三块新能力：
1. 本地 overlay（data/sidecars/<mount_id>/ 镜像目录）：命中即覆盖源 sidecar 且不再读源
2. 网络源 sidecar/海报候选经目录索引判定，缺失候选零请求（不发 GET/HEAD/stat）
3. PATCH /media/{id} 系列归组（家长修正通道）：设置/解除/互斥校验/重扫不覆盖
"""
import time

import pytest

from conftest import make_sample_image, make_sample_video, requires_ffprobe
from kindo.media.storage import StorageObject


def _wait_job(env, job_id, timeout=90):
    for _ in range(timeout * 2):
        j = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if j["state"] in ("done", "failed", "interrupted"):
            return j
        time.sleep(0.5)
    pytest.fail("扫描任务超时")


def _scan_family(env):
    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    j = _wait_job(env, r.json()["job_id"])
    assert j["state"] == "done", j
    return env.client.get("/api/v1/admin/media?limit=100").json()["items"]


# ==================== 本地 overlay（family 本地源） ====================

@requires_ffprobe
def test_overlay_wins_and_skips_source_sidecar(env):
    """overlay 命中：覆盖源 sidecar 的标题/系列，源文件不再被读取。"""
    env.bootstrap_admin()
    d = env.media_dir / "动画片"
    d.mkdir(parents=True)
    make_sample_video(d / "S01E01.mp4", seconds=6)
    # 源 sidecar（应被 overlay 覆盖）
    (d / "kindo.yaml").write_text("title: 源标题\nseries: {name: 源系列}\n", encoding="utf-8")

    ov = env.data_dir / "sidecars" / "family" / "动画片"
    ov.mkdir(parents=True)
    (ov / "kindo.yaml").write_text(
        "title: 本地修正标题\nseries: {name: 本地系列, season_no: 1, episode_no: 1}\n",
        encoding="utf-8")

    items = _scan_family(env)
    target = [i for i in items if i["path_key"] == "动画片/S01E01.mp4"]
    assert target, items
    assert target[0]["title"] == "本地修正标题"
    assert target[0]["media_type"] == "episode"
    assert (target[0]["series"] or {}).get("title") == "本地系列"

    cols = env.client.get("/api/v1/admin/collections").json()
    assert any(s["title"] == "本地系列" for s in cols["series"])
    assert not any(s["title"] == "源系列" for s in cols["series"])


@requires_ffprobe
def test_overlay_poster_from_local_image(env):
    """overlay 的 poster 声明解析到 overlay 目录内的图片，海报生成成功。"""
    env.bootstrap_admin()
    d = env.media_dir / "动画B"
    d.mkdir(parents=True)
    make_sample_video(d / "E01.mp4", seconds=6)
    ov = env.data_dir / "sidecars" / "family" / "动画B"
    ov.mkdir(parents=True)
    (ov / "kindo.yaml").write_text("poster: cover.jpg\n", encoding="utf-8")
    make_sample_image(ov / "cover.jpg")

    items = _scan_family(env)
    target = [i for i in items if i["path_key"] == "动画B/E01.mp4"]
    assert target and target[0]["has_poster"], target


@requires_ffprobe
def test_overlay_dir_level_cascades_to_nested_videos(env):
    """目录级 overlay 沿祖先链生效：系列根的 kindo.yaml 覆盖多层嵌套的视频，
    就近目录覆盖远目录；海报图放在系列根 overlay 目录同样生效。"""
    env.bootstrap_admin()
    deep = env.media_dir / "牛津树" / "L1" / "Stories"
    deep.mkdir(parents=True)
    make_sample_video(deep / "EP01.mp4", seconds=6)
    make_sample_video(deep / "EP02.mp4", seconds=6)

    ov_root = env.data_dir / "sidecars" / "family"
    (ov_root / "牛津树").mkdir(parents=True)
    (ov_root / "牛津树" / "kindo.yaml").write_text(
        "series: {name: 牛津阅读树, season_no: 1}\nlanguage: en\nposter: poster.jpg\n",
        encoding="utf-8")
    make_sample_image(ov_root / "牛津树" / "poster.jpg")
    # 更近一层覆盖系列根的语言
    (ov_root / "牛津树" / "L1").mkdir()
    (ov_root / "牛津树" / "L1" / "kindo.yaml").write_text("language: zh-CN\n", encoding="utf-8")

    items = _scan_family(env)
    eps = sorted((i for i in items if i["path_key"].startswith("牛津树/")),
                 key=lambda i: i["path_key"])
    assert len(eps) == 2, items
    for e in eps:
        assert e["media_type"] == "episode"
        assert (e["series"] or {})["title"] == "牛津阅读树"
        assert e["language"] == "zh-CN"  # 就近目录级覆盖远目录级
        assert e["has_poster"]  # 系列根 overlay 的 poster.jpg 对嵌套视频生效


# ==================== 网络源：目录索引零盲探 ====================

class _CountingNetProvider:
    """模拟网络源：统计 read_text/stat 次数，验证缺失候选不发请求。"""

    mount_id = "fake-net"

    def __init__(self, entries, sidecar_texts):
        self._entries = entries
        self._sidecar_texts = sidecar_texts
        self.read_text_calls: list[str] = []
        self.stat_calls: list[str] = []

    def list_entries(self):
        yield from self._entries

    def list_videos(self):
        return iter([])

    def list_subtitles(self):
        return iter([])

    def sidecar_candidates(self, video):
        parent = video.path_key.rpartition("/")[0]
        stem = video.name.rsplit(".", 1)[0]
        return (f"{parent}/kindo.yaml" if parent else "kindo.yaml",
                f"{parent}/{stem}.kindo.yaml" if parent else f"{stem}.kindo.yaml")

    def read_text(self, path_key, limit_bytes=5 * 1024 * 1024):
        self.read_text_calls.append(path_key)
        return self._sidecar_texts.get(path_key, "")

    def stat(self, path_key):
        self.stat_calls.append(path_key)
        raise AssertionError("网络源海报探测不应逐候选 stat（应走目录索引）")

    def open_range(self, path_key, start, length=None):
        raise AssertionError("跳过探测时不应下载数据")

    def health(self):
        return {"mount_id": self.mount_id, "healthy": True}


def _fake_video(path_key="库/EP.mp4", size=1000):
    return StorageObject(path_key=path_key, name=path_key.rpartition("/")[2],
                         size=size, mtime_ms=1700000000000)


def test_network_no_request_for_missing_sidecar_and_poster(env):
    """无 overlay、无源 sidecar：0 次 read_text、0 次 stat（全部经目录索引判定）。"""
    env.bootstrap_admin()
    entries = [_fake_video(size=10 * 1024 * 1024)]  # 大于 remote_probe_max_bytes 上限 → 跳过探测
    provider = _CountingNetProvider(entries, sidecar_texts={})
    env.state.storage.register(provider)

    r = env.client.post("/api/v1/admin/media-mounts/fake-net/scan", headers=env.admin_headers())
    j = _wait_job(env, r.json()["job_id"])
    assert j["state"] == "done", j

    assert provider.read_text_calls == []
    assert provider.stat_calls == []
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert len(items) == 1
    assert items[0]["playable"] is True  # 超限跳过探测不阻断播放
    assert items[0]["duration_ms"] == 0


def test_network_existing_sidecar_read_once_and_overlay_skips_it(env):
    """源 sidecar 存在：仅读存在的候选；overlay 命中后源 sidecar 完全不读。"""
    env.bootstrap_admin()
    video = _fake_video()
    entries = [video, StorageObject(path_key="库/kindo.yaml", name="kindo.yaml",
                                    size=10, mtime_ms=1)]
    provider = _CountingNetProvider(
        entries, sidecar_texts={"库/kindo.yaml": "title: 网络sidecar标题\n"})
    env.state.storage.register(provider)

    r = env.client.post("/api/v1/admin/media-mounts/fake-net/scan", headers=env.admin_headers())
    _wait_job(env, r.json()["job_id"])
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert items[0]["title"] == "网络sidecar标题"
    assert provider.read_text_calls == ["库/kindo.yaml"]  # 只读存在的目录级，缺失的文件级不发请求

    # 放入 overlay 后重扫：源 sidecar 不再读
    ov = env.data_dir / "sidecars" / "fake-net" / "库"
    ov.mkdir(parents=True)
    (ov / "kindo.yaml").write_text("title: overlay标题\n", encoding="utf-8")
    provider.read_text_calls.clear()
    r = env.client.post("/api/v1/admin/media-mounts/fake-net/scan", headers=env.admin_headers())
    _wait_job(env, r.json()["job_id"])
    assert provider.read_text_calls == []
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert items[0]["title"] == "overlay标题"


# ==================== PATCH 系列归组（家长修正通道） ====================

@requires_ffprobe
def test_patch_series_group_set_rescan_keep_clear(env):
    """PATCH 归组 → 系列建立；带冲突 overlay 重扫不覆盖；显式 null 解除。"""
    env.bootstrap_admin()
    d = env.media_dir / "散片"
    d.mkdir(parents=True)
    make_sample_video(d / "clip.mp4", seconds=6)

    items = _scan_family(env)
    target = [i for i in items if i["path_key"] == "散片/clip.mp4"][0]
    assert target["media_type"] == "movie"

    # 归组
    r = env.client.patch(f"/api/v1/admin/media/{target['media_id']}",
                         json={"series": {"name": "家长定的系列", "episode_no": 3}},
                         headers=env.admin_headers())
    assert r.status_code == 200, r.text
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    t = [i for i in items if i["media_id"] == target["media_id"]][0]
    assert t["media_type"] == "episode"
    assert (t["series"] or {})["title"] == "家长定的系列"

    # overlay 声称另一个系列 → 重扫后家长修正仍赢
    ov = env.data_dir / "sidecars" / "family" / "散片"
    ov.mkdir(parents=True)
    (ov / "kindo.yaml").write_text("series: {name: overlay系列}\n", encoding="utf-8")
    items = _scan_family(env)
    t = [i for i in items if i["media_id"] == target["media_id"]][0]
    assert (t["series"] or {})["title"] == "家长定的系列"

    # 显式解除
    r = env.client.patch(f"/api/v1/admin/media/{target['media_id']}",
                         json={"series": None}, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    t = [i for i in items if i["media_id"] == target["media_id"]][0]
    assert t["media_type"] == "movie"
    assert t["series"] is None


def test_patch_series_course_mutually_exclusive(env):
    """series 与 course 同时提交 → 400。"""
    env.bootstrap_admin()
    d = env.media_dir / "x"
    d.mkdir(parents=True)
    make_sample_video(d / "a.mp4", seconds=5)
    items = _scan_family(env)
    mid = items[0]["media_id"]
    r = env.client.patch(f"/api/v1/admin/media/{mid}",
                         json={"series": {"name": "S"}, "course": {"name": "C"}},
                         headers=env.admin_headers())
    assert r.status_code == 400
