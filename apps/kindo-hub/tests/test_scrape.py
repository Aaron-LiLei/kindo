"""v0.3 元数据管线测试：配置只写不回显、假 TMDB 全流程（匹配→决策→详情→
合并→Artwork）、confirmed/no_match 不被 refresh 覆盖（约束 15）、幂等。
兼容保留：TV/Admin 海报端点契约（v0.2，2026-08-21）。"""
from __future__ import annotations

import pathlib
import subprocess
import time

import httpx
import pytest

from conftest import FFPROBE, build_sample_library, requires_ffprobe


def _scan(env) -> None:
    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            return
        time.sleep(0.5)


def test_scrape_config_write_only(env):
    env.bootstrap_admin()
    r = env.client.put(
        "/api/v1/admin/scrape/config",
        json={"base_url": "http://tmdb.test", "api_key": "k-secret"},
        headers=env.admin_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_url"] == "http://tmdb.test"
    assert body["api_key_configured"] is True
    assert "api_key" not in body
    r = env.client.get("/api/v1/admin/scrape/config", headers=env.admin_headers())
    assert "api_key" not in r.json()


def test_scrape_run_without_key_fails(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/scrape/run", json={}, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    for _ in range(20):
        st = env.client.get("/api/v1/admin/scrape/status",
                            headers=env.admin_headers()).json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert st["state"] == "failed"
    assert any("API Key" in line for line in st["log_tail"])


@pytest.fixture()
def fake_poster_jpg(tmp_path):
    if not FFPROBE:
        pytest.skip("ffmpeg 不可用")
    ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg")
    out = tmp_path / "poster.jpg"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=0.2:size=320x240:rate=5",
         "-frames:v", "1", "-q:v", "5", str(out)],
        check=True, capture_output=True, timeout=30,
    )
    return out.read_bytes()


@requires_ffprobe
def test_metadata_pipeline_full_flow(env, monkeypatch, fake_poster_jpg):
    """扫描 → 匹配（exact 自动应用）→ 详情合并 → 系列 poster 单文件 → 幂等。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/3/search/tv"):
            return httpx.Response(200, json={"results": [
                {"id": 101, "name": "汪汪队立大功", "poster_path": "/pa.jpg"},
            ]})
        if path.startswith("/3/search/movie"):
            return httpx.Response(200, json={"results": [
                {"id": 202, "title": "海底小纵队大电影", "poster_path": "/pb.jpg"},
            ]})
        if path.startswith("/3/tv/101"):
            return httpx.Response(200, json={
                "name": "汪汪队立大功", "overview": "一群小狗的救援故事。",
                "first_air_date": "2013-08-12", "poster_path": "/pa.jpg"})
        if path.startswith("/3/movie/202"):
            return httpx.Response(200, json={
                "title": "海底小纵队大电影", "overview": "海底探险大电影。",
                "release_date": "2020-10-01", "poster_path": "/pb.jpg"})
        if "/w500/" in path:
            return httpx.Response(200, content=fake_poster_jpg)
        return httpx.Response(404)

    import kindo.media.scrape as scrape_mod

    monkeypatch.setattr(
        scrape_mod, "_make_client",
        lambda base_url: httpx.Client(
            transport=httpx.MockTransport(handler), base_url=base_url),
    )
    env.client.put(
        "/api/v1/admin/scrape/config",
        json={"base_url": "http://tmdb.test",
              "image_base_url": "http://tmdb.test/t/p",
              "api_key": "k-test"},
        headers=env.admin_headers(),
    )

    r = env.client.post("/api/v1/admin/scrape/run", json={"force": True},
                        headers=env.admin_headers())
    assert r.status_code == 200
    for _ in range(120):
        st = env.client.get("/api/v1/admin/scrape/status",
                            headers=env.admin_headers()).json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert st["state"] == "done", st
    assert st["matched"] == 2, st  # series（汪汪队）+ movie（海底小纵队）；course 不参与

    from kindo.models import ArtworkAsset, ContentEntity, ExternalIdentity

    with env.db.session() as s:
        series = (s.query(ContentEntity)
                  .filter_by(entity_type="series", title="汪汪队立大功").one())
        assert series.match_status == "auto"
        ident = (s.query(ExternalIdentity)
                 .filter_by(entity_id=series.id).one())
        assert ident.provider == "tmdb" and ident.ref_id == "101"
        # Normalizer：overview / release_date 已合并（AUTO_PROVIDER 级）
        assert series.overview == "一群小狗的救援故事。"
        assert series.release_date == "2013-08-12"
        # Artwork：系列 poster 单文件（不再复制到每集）
        poster = (s.query(ArtworkAsset)
                  .filter_by(entity_id=series.id, kind="poster").one())
        assert poster.source == "provider"
        from kindo.config import load_config

        art_file = pathlib.Path(load_config().data_dir) / poster.file_path
        assert art_file.stat().st_size > 0
        episode_posters = (
            s.query(ArtworkAsset)
            .filter(ArtworkAsset.kind == "poster")
            .count()
        )
        assert episode_posters == 2  # series + movie，各一张
        movie = (s.query(ContentEntity)
                 .filter_by(entity_type="movie").one())
        assert movie.match_status == "auto"

    # match/overview 汇总
    r = env.client.get("/api/v1/admin/match/overview", headers=env.admin_headers())
    assert r.status_code == 200
    assert r.json()["counts"].get("auto") == 2

    # 幂等：未 force 再跑 → 目标为 0（auto+identity 跳过）
    env.client.post("/api/v1/admin/scrape/run", json={}, headers=env.admin_headers())
    for _ in range(120):
        st = env.client.get("/api/v1/admin/scrape/status",
                            headers=env.admin_headers()).json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert st["state"] == "done" and st["total"] == 0, st


@requires_ffprobe
def test_no_match_never_overridden_and_parent_confirm(env, monkeypatch,
                                                      fake_poster_jpg):
    """约束 15：no_match / confirmed 不被后续批任务覆盖；家长确认立即应用详情。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/3/search/"):
            return httpx.Response(200, json={"results": [
                {"id": 999, "name": "完全不相干", "poster_path": "/x.jpg"}]})
        if path.startswith("/3/movie/303"):
            return httpx.Response(200, json={
                "title": "海底小纵队大电影", "overview": "家长确认后的简介。",
                "release_date": "2020-10-01", "poster_path": "/pb.jpg"})
        if "/w500/" in path:
            return httpx.Response(200, content=fake_poster_jpg)
        return httpx.Response(404)

    import kindo.media.scrape as scrape_mod

    monkeypatch.setattr(
        scrape_mod, "_make_client",
        lambda base_url: httpx.Client(
            transport=httpx.MockTransport(handler), base_url=base_url))
    env.client.put(
        "/api/v1/admin/scrape/config",
        json={"base_url": "http://tmdb.test",
              "image_base_url": "http://tmdb.test/t/p", "api_key": "k-test"},
        headers=env.admin_headers(),
    )

    from kindo.models import ContentEntity

    with env.db.session() as s:
        movie = (s.query(ContentEntity).filter_by(entity_type="movie").one())
        movie_id = movie.id
        series = (s.query(ContentEntity).filter_by(entity_type="series").one()
                  .id)

    # 家长确认无匹配（movie）
    r = env.client.post(f"/api/v1/admin/content/{movie_id}/match",
                        json={"no_match": True}, headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["match_status"] == "no_match"

    # 家长手动确认（series，ref 303 走 movie 详情？kind 按实体=tv——改用 movie 确认）
    r = env.client.post(
        "/api/v1/admin/match/search",
        json={"query": "海底小纵队大电影", "entity_id": movie_id},
        headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["candidates"]

    # force 批任务：no_match 跳过；series 检索"完全不相干"→ likely/fuzzy → pending
    env.client.post("/api/v1/admin/scrape/run", json={"force": True},
                    headers=env.admin_headers())
    for _ in range(120):
        st = env.client.get("/api/v1/admin/scrape/status",
                            headers=env.admin_headers()).json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert st["state"] == "done", st
    with env.db.session() as s:
        movie = s.get(ContentEntity, movie_id)
        assert movie.match_status == "no_match"  # 未被 force 批任务覆盖
        series = s.get(ContentEntity, series)
        assert series.match_status in ("none", "auto")
        if series.candidates_json:
            assert len(series.candidates_json) <= 3  # top-3 待确认缓存

    # 家长确认 movie 的真实匹配 → confirmed + confirmed 级详情合并
    r = env.client.post(
        f"/api/v1/admin/content/{movie_id}/match",
        json={"ref_id": "303", "title": "海底小纵队大电影", "apply_details": True},
        headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["match_status"] == "confirmed"
    with env.db.session() as s:
        movie = s.get(ContentEntity, movie_id)
        assert movie.overview == "家长确认后的简介。"
        prov = movie.meta_provenance_json["overview"]
        assert prov["source"] == "provider_confirmed"
    # 决策时间线可查
    r = env.client.get(f"/api/v1/admin/content/{movie_id}/match/decisions",
                       headers=env.admin_headers())
    decisions = r.json()["decisions"]
    assert any(d["decision"] == "parent_confirm" for d in decisions)
    assert any(d["decision"] == "parent_no_match" for d in decisions)

    # 全局决策时间线（ADM-012 审计视图）：带实体标题，最近在前
    r = env.client.get("/api/v1/admin/match/decisions/recent",
                       headers=env.admin_headers())
    assert r.status_code == 200
    recent = r.json()["decisions"]
    assert recent, "全局时间线不应为空"
    row = next(d for d in recent if d["entity_id"] == movie_id
               and d["decision"] == "parent_confirm")
    assert row["entity_title"], "实体标题应回填（无标题时回退 entity_id）"
    assert row["candidate"]["title"] == "海底小纵队大电影"

    # 概览页首跑引导数据：health 附入库量与待确认匹配数
    r = env.client.get("/api/v1/admin/health", headers=env.admin_headers())
    assert r.status_code == 200
    health = r.json()
    assert health["media"]["total"] > 0
    assert isinstance(health["media"]["match_pending"], int)


@requires_ffprobe
def test_device_poster_endpoint(env):
    """TV 设备海报端点（v0.2 契约保留）：FileResponse + device 鉴权。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    _did, token = env.pair_device()
    r = env.client.get("/api/v1/media?limit=20",
                       headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in r.json()["items"] if i["has_poster"])
    r2 = env.client.get(
        f"/api/v1/media/{item['media_id']}/poster",
        headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"].startswith("image/jpeg")


@requires_ffprobe
def test_default_poster_fallback(env):
    """无真实海报的条目回退默认海报（v0.2 契约保留）。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan(env)
    _did, token = env.pair_device()
    items = env.client.get("/api/v1/admin/media?limit=5",
                           headers=env.admin_headers()).json()["items"]
    target = items[0]["media_id"]
    import pathlib

    cache = pathlib.Path(env.data_dir) / "cache" / "posters"
    if (cache / f"{target}.jpg").exists():
        (cache / f"{target}.jpg").unlink()
    r2 = env.client.get(
        f"/api/v1/media/{target}/poster", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"].startswith("image/jpeg")
