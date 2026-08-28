"""扫描期海报管线 + admin 媒体列表扩展字段（§13.2 cache/posters，2026-08-20 展示重构）。"""

from conftest import build_sample_library, requires_ffprobe


def _scan(env):
    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    import time

    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"扫描超时: {job}")


def _items(env) -> dict:
    r = env.client.get("/api/v1/admin/media?limit=100")
    assert r.status_code == 200, r.text
    return {i["title"]: i for i in r.json()["items"]}


@requires_ffprobe
def test_poster_sources_and_list_fields(env):
    build_sample_library(env.media_dir)
    job = _scan(env)
    assert job["state"] == "done", job
    by_title = _items(env)

    # 海报三类来源：sidecar 声明 / 同名约定 / 本地抽帧兜底
    assert by_title["海底小纵队大电影"]["has_poster"] is True
    assert by_title["汪汪队立大功 第一季 第2集"]["has_poster"] is True
    assert by_title["汪汪队立大功 第一季 第1集"]["has_poster"] is True
    assert by_title["英语启蒙 第1课"]["has_poster"] is True

    # 所属系列/课程（批量序列化，episode → series、lesson → course）
    ep1 = by_title["汪汪队立大功 第一季 第1集"]
    assert ep1["series"]["title"] == "汪汪队立大功"
    assert ep1["series"]["episode_no"] == 1
    assert ep1["course"] is None
    lesson = by_title["英语启蒙 第1课"]
    assert lesson["course"]["title"] == "英语启蒙"
    assert lesson["series"] is None
    assert by_title["海底小纵队大电影"]["series"] is None


@requires_ffprobe
def test_poster_endpoint(env):
    build_sample_library(env.media_dir)
    _scan(env)
    movie = _items(env)["海底小纵队大电影"]

    r = env.client.get(f"/api/v1/admin/media/{movie['media_id']}/poster")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content[:3] == b"\xff\xd8\xff"  # JPEG magic
    assert "no-store" not in r.headers.get("cache-control", "")

    # 无海报 / 不存在的媒体 → 404
    r = env.client.get("/api/v1/admin/media/00000000-0000-0000-0000-000000000000/poster")
    assert r.status_code == 404


@requires_ffprobe
def test_poster_endpoint_requires_admin(env):
    build_sample_library(env.media_dir)
    _scan(env)
    # 未登录会话
    from fastapi.testclient import TestClient

    with TestClient(env.app) as anon:
        r = anon.get("/api/v1/admin/media/whatever/poster")
        assert r.status_code == 401


@requires_ffprobe
def test_poster_idempotent_across_rescan(env):
    build_sample_library(env.media_dir)
    _scan(env)
    ep1 = _items(env)["汪汪队立大功 第一季 第1集"]
    poster = env.data_dir / "cache" / "posters" / f"{ep1['media_id']}.jpg"
    assert poster.is_file()
    first_mtime = poster.stat().st_mtime_ns

    _scan(env)  # 重扫：抽帧结果已存在，不重新生成
    assert poster.stat().st_mtime_ns == first_mtime


@requires_ffprobe
def test_collections_and_series_filter(env):
    build_sample_library(env.media_dir)
    _scan(env)

    r = env.client.get("/api/v1/admin/collections")
    assert r.status_code == 200, r.text
    data = r.json()
    series = {s["title"]: s for s in data["series"]}
    assert series["汪汪队立大功"]["count"] == 2
    assert series["汪汪队立大功"]["duration_ms"] > 0
    assert series["汪汪队立大功"]["cover_media_id"]
    courses = {c["title"]: c for c in data["courses"]}
    assert courses["英语启蒙"]["count"] == 1

    # series_id 筛选：只返回该系列两集
    sid = series["汪汪队立大功"]["series_id"]
    r = env.client.get(f"/api/v1/admin/media?series_id={sid}&limit=100")
    got = r.json()["items"]
    assert len(got) == 2
    assert all(i["series"]["series_id"] == sid for i in got)


@requires_ffprobe
def test_episode_poster_falls_back_to_series_entity(env):
    """集级无自有海报 → 系列实体海报（2026-08-27 MED-013 URL 级回退）。

    场景=网盘直挂库：集级 has_poster=0（probe skip / 未逐集刮削）但系列
    有 TMDB 实体图——系列墙有海报、集网格空的缺口；TV/Admin 两端点同语义。
    has_poster 语义不变（仍=自有真实海报）。
    """
    import pathlib

    from conftest import make_sample_image

    build_sample_library(env.media_dir)
    _scan(env)  # 内部完成 bootstrap_admin
    _d, token = env.pair_device()
    dh = env.device_headers(token)

    ep = _items(env)["汪汪队立大功 第一季 第1集"]
    media_id = ep["media_id"]

    # 模拟集级无自有海报
    cache = pathlib.Path(env.data_dir) / "cache" / "posters"
    (cache / f"{media_id}.jpg").unlink(missing_ok=True)

    # 系列实体插一张 poster（唯一字节标记，区别于默认图）
    from kindo.media.content_catalog import series_entities_by_series
    from kindo.models import ArtworkAsset, Episode, Media, Series
    from kindo.util import new_id

    art_dir = pathlib.Path(env.data_dir) / "cache" / "artwork"
    art_dir.mkdir(parents=True, exist_ok=True)
    art_file = art_dir / "series-poster-fallback-test.jpg"
    make_sample_image(art_file, color="red")

    with env.db.session() as s:
        s.query(Media).filter_by(id=media_id).update({"has_poster": False})
        ep_row = s.query(Episode).filter_by(media_id=media_id).one()
        series = s.get(Series, ep_row.series_id)
        sent = series_entities_by_series(s, [series])[series.id]
        s.add(ArtworkAsset(id=new_id(), entity_id=sent.id, kind="poster",
                           source="provider",
                           file_path=f"cache/artwork/{art_file.name}"))
        s.commit()
        expected = art_file.read_bytes()

    # TV 设备端点：返回系列实体图（而非默认图）
    r = env.client.get(f"/api/v1/media/{media_id}/poster", headers=dh)
    assert r.status_code == 200, r.text
    assert r.content == expected

    # Admin 端点同一语义
    r2 = env.client.get(f"/api/v1/admin/media/{media_id}/poster")
    assert r2.status_code == 200, r2.text
    assert r2.content == expected


@requires_ffprobe
def test_series_episode_listing_fields(env):
    """TV 系列集列表带集号与断点/完看（2026-08-27：集网格数字卡数据基础）。"""
    build_sample_library(env.media_dir)
    _scan(env)
    _d, token = env.pair_device()
    dh = env.device_headers(token)

    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    ep1 = next(i for i in items if i["title"].startswith("汪汪队"))
    sid = ep1["series"]["series_id"]

    r = env.client.get(f"/api/v1/media?series_id={sid}&limit=20", headers=dh)
    assert r.status_code == 200, r.text
    eps = r.json()["items"]
    assert len(eps) >= 2
    nos = [e["episode_no"] for e in eps]
    assert nos == sorted(nos), "按集号升序"
    assert all("last_position_ms" in e and "completed" in e for e in eps)
    assert eps[0]["episode_no"] == 1


def test_default_poster_variants(env):
    """默认海报 v4：糖果色微笑太阳六变体，按 seed 稳定轮换（无海报内容
    不再是空白墙——2026-08-27 产品反馈）。"""
    import hashlib

    from kindo.config import load_config
    from kindo.media.posters import _DEFAULT_POSTER_VARIANTS, default_poster

    cfg = load_config()
    seen = {}
    for i in range(40):
        seed = f"seed-{i}"
        p = default_poster(cfg, seed=seed)
        idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % _DEFAULT_POSTER_VARIANTS
        seen.setdefault(idx, p)
        assert p.name == f"_default_v4_{idx}.jpg", p.name
        assert p.is_file() and p.stat().st_size > 0
    assert len(seen) >= 2, "多种 seed 应命中不同变体"
    # 无 seed → 变体 0；字节非空白（有实质图形内容）
    p0 = default_poster(cfg)
    assert p0.name == "_default_v4_0.jpg"
    from PIL import Image

    img = Image.open(seen[0]).convert("RGB")
    colors = {img.getpixel((240, 320)),  # 太阳脸盘中心
              img.getpixel((10, 10))}    # 背景角
    assert len(colors) == 2 and colors != {(255, 247, 235)}
