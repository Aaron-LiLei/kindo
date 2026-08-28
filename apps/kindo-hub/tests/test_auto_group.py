"""自动归组与类型推断（2026-08-20 库内容治理）。

覆盖：纯函数（集号/季号/目录规则/顺序分配）、扫描器集成（sidecar/家长修正
优先级、幂等重扫、目录变化解除）、本地重建端点（存量回填、不动已声明归组）、
collections 聚合新字段。
"""
from __future__ import annotations

from conftest import Env, build_sample_library  # noqa: F401
from kindo.media.auto_group import (
    clean_series_title,
    compute_auto_groups,
    parse_episode_no,
    parse_leading_no,
    parse_season_no,
)

# ---------- 纯函数 ----------


def test_parse_episode_no_patterns():
    assert parse_episode_no("汪汪队立大功第10季 第01-02集.mp4") == (1, None)
    assert parse_episode_no("汪汪队第05集.mkv") == (5, None)
    assert parse_episode_no("Show.S01E05.1080p.mp4") == (5, 1)
    assert parse_episode_no("EP01 - hello.mp4") == (1, None)
    assert parse_episode_no("001_Doctor_Ted.mp4") == (1, None)
    assert parse_episode_no("002. Twinkle Twinkle Little Star.mp4") == (2, None)
    # 纯数字片名（1917.mp4）不得被当作集号
    assert parse_episode_no("1917.mp4") == (None, None)
    # "第10季"是季不是集
    assert parse_episode_no("第10季.mp4") == (None, None)
    assert parse_episode_no("Learn Letter A.mp4") == (None, None)


def test_parse_leading_no_and_season():
    assert parse_leading_no("01") == 1
    assert parse_leading_no("001-050") == 1
    assert parse_leading_no("L1 Getting ready") is None
    assert parse_season_no("汪汪队立大功 第10季 4K") == 10
    assert parse_season_no("Season 2") == 2
    assert parse_season_no("S02") == 2
    assert parse_season_no("Yakka Dee 1-5季+特别版") is None  # 区间语义不明，不猜


def test_clean_series_title():
    assert clean_series_title("1-加拿大外教精讲小猪佩奇视频") == "加拿大外教精讲小猪佩奇视频"
    assert clean_series_title("1. SSS儿歌") == "SSS儿歌"
    # 2026-08-21 内容边界重写：剥【…】宣传前缀、字母序号、季阶范围/清晰度尾巴
    assert clean_series_title("【完结】国家地理：海洋探秘") == "国家地理：海洋探秘"
    assert clean_series_title("A. 亚克迪 Yakka Dee") == "亚克迪 Yakka Dee"
    assert clean_series_title("1. 英语版 1-7季146集（英文字幕）1080P") == "英语版"
    assert clean_series_title("Yakka Dee 1-5季+特别版") == "Yakka Dee"
    assert clean_series_title("晴朗牛津阅读术1-3阶") == "晴朗牛津阅读术"
    assert clean_series_title("1【英文视频】Super JoJo 单集（179集）") == "Super JoJo 单集（179集）"
    # 剥空回退原名（纯数字目录名等）
    assert clean_series_title("2046") == "2046"


def test_compute_auto_groups_rules():
    assigs = compute_auto_groups([
        # 深度不足（挂载根直下）与单文件目录不成组
        "儿歌/单文件目录/唯一.mp4",
        "电影/阿凡达.mkv",
        # 显式集号 + 目录季号
        "动画片/汪汪队/国语版/汪汪队 第10季 4K/汪汪队第10季 第01-02集.mp4",
        "动画片/汪汪队/国语版/汪汪队 第10季 4K/汪汪队第10季 第05集.mp4",
        # 目录编号当集号（同名多文件允许重复集号）
        "动画片/佩奇精讲/01/动画片精选.mp4",
        "动画片/佩奇精讲/01/外教授课.mp4",
        # 无编号 → 自然排序顺序分配
        "动画片/Akili/Learn Letter B.mp4",
        "动画片/Akili/Learn Letter A.mp4",
        "动画片/Akili/Learn Letter C.mp4",
    ])
    assert "儿歌/单文件目录/唯一.mp4" not in assigs
    assert "电影/阿凡达.mkv" not in assigs

    a1 = assigs["动画片/汪汪队/国语版/汪汪队 第10季 4K/汪汪队第10季 第01-02集.mp4"]
    assert a1.series_title == "汪汪队"
    assert a1.season_no == 10
    assert a1.episode_no == 1
    a2 = assigs["动画片/汪汪队/国语版/汪汪队 第10季 4K/汪汪队第10季 第05集.mp4"]
    assert a2.episode_no == 5 and a2.season_no == 10

    p1 = assigs["动画片/佩奇精讲/01/动画片精选.mp4"]
    p2 = assigs["动画片/佩奇精讲/01/外教授课.mp4"]
    assert p1.series_title == "佩奇精讲"
    assert p1.episode_no == 1 and p2.episode_no == 1  # 同号双文件保留显式值

    aka = assigs["动画片/Akili/Learn Letter A.mp4"]
    akb = assigs["动画片/Akili/Learn Letter B.mp4"]
    akc = assigs["动画片/Akili/Learn Letter C.mp4"]
    assert aka.series_title == "Akili"
    assert (aka.episode_no, akb.episode_no, akc.episode_no) == (1, 2, 3)


def test_compute_auto_groups_content_boundary():
    """2026-08-21 内容边界重写：分类节点拆分、容器并入、通用词/版本词链头继承。"""
    assigs = compute_auto_groups([
        # 品牌打包层下多个真系列 → 拆分（不再压成一个巨型合集）
        "儿歌与韵律/爱丽丝儿歌/1. SSS儿歌/2. 儿歌视频489集（英文字幕）/001. A.mp4",
        "儿歌与韵律/爱丽丝儿歌/1. SSS儿歌/2. 儿歌视频489集（英文字幕）/002. B.mp4",
        "儿歌与韵律/爱丽丝儿歌/5. CoCoMelon/01. Kids Songs/01. C.mp4",
        "儿歌与韵律/爱丽丝儿歌/5. CoCoMelon/01. Kids Songs/02. D.mp4",
        # 通用词链 + 季/特别篇容器 → 系列挂有意义的上位目录
        "动画片/小猪佩奇系列/动画片/中英双版/第一季/S1/第01集.mp4",
        "动画片/小猪佩奇系列/动画片/中英双版/第一季/S1/第02集.mp4",
        "动画片/小猪佩奇系列/动画片/中英双版/Special Episodes/Christmas.mp4",
        # 编号分段目录（001-050）是容器 → 整目录一个系列
        "动画片/BBC睡前故事/001-050/001_Ted.mp4",
        "动画片/BBC睡前故事/001-050/002_Hat.mp4",
        "动画片/BBC睡前故事/051-100/051_Bed.mp4",
        # 版本词多分支 → 各自成系列
        "动画片/汪汪队/英文版/Season 11/第01集.mp4",
        "动画片/汪汪队/英文版/Season 11/第02集.mp4",
        "动画片/汪汪队/国语版/第01集.mp4",
        "动画片/汪汪队/国语版/第02集.mp4",
    ])
    sss = assigs["儿歌与韵律/爱丽丝儿歌/1. SSS儿歌/2. 儿歌视频489集（英文字幕）/001. A.mp4"]
    coco = assigs["儿歌与韵律/爱丽丝儿歌/5. CoCoMelon/01. Kids Songs/01. C.mp4"]
    assert sss.series_title == "儿歌视频489集（英文字幕）"
    assert coco.series_title == "Kids Songs"
    assert sss.series_key != coco.series_key  # 两个独立系列

    pep = assigs["动画片/小猪佩奇系列/动画片/中英双版/第一季/S1/第01集.mp4"]
    sp = assigs["动画片/小猪佩奇系列/动画片/中英双版/Special Episodes/Christmas.mp4"]
    assert pep.series_title == "小猪佩奇系列" and pep.season_no == 1
    assert sp.series_title == "小猪佩奇系列"  # 特别篇并入同一系列

    bbc1 = assigs["动画片/BBC睡前故事/001-050/001_Ted.mp4"]
    bbc2 = assigs["动画片/BBC睡前故事/051-100/051_Bed.mp4"]
    assert bbc1.series_title == "BBC睡前故事" and bbc1.episode_no == 1
    assert bbc2.series_title == "BBC睡前故事" and bbc2.episode_no == 51
    assert bbc1.series_key == bbc2.series_key

    en = assigs["动画片/汪汪队/英文版/Season 11/第01集.mp4"]
    zh = assigs["动画片/汪汪队/国语版/第01集.mp4"]
    assert en.series_title == "汪汪队 英文版" and en.season_no == 11
    assert zh.series_title == "汪汪队 国语版" and zh.episode_no == 1
    assert en.series_key != zh.series_key


def test_compute_auto_groups_natural_sort_level_ordering():
    """跨子目录无编号文件按自然排序连续编号（Level 2 不应排到 Level 10 后）。"""
    assigs = compute_auto_groups([
        "分级/牛津/L1/Level 1 First Stories Get on.mp4",
        "分级/牛津/L1/Level 2 Stories The Go-kart.mp4",
        "分级/牛津/L2/Level 10 Stories The Balloon.mp4",
    ])
    eps = [assigs[k].episode_no for k in sorted(assigs)]
    assert eps == [1, 2, 3]
    assert assigs["分级/牛津/L2/Level 10 Stories The Balloon.mp4"].episode_no == 3


# ---------- 扫描器集成 ----------


def _scan_until_done(env: Env):
    """触发 family 挂载扫描并等完成（进程内 worker，轮询 job 状态）。"""
    import time

    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    for _ in range(200):
        job = env.state.scanner.get_job(r.json()["job_id"])
        if job.state in ("done", "failed", "interrupted"):
            break
        time.sleep(0.1)
    assert job.state == "done", job.error_summary
    return job


def _media(env: Env, path_key: str):
    with env.db.session() as s:
        from kindo.models import Media

        return (
            s.query(Media).filter(Media.path_key == path_key, Media.mount_id == "family")
            .one_or_none()
        )


def test_scan_auto_groups_bare_files(env: Env):
    """无 sidecar 裸文件按目录结构归组，media_type 变为 episode。"""
    (env.media_dir / "动画片/汪汪队").mkdir(parents=True)
    (env.media_dir / "动画片/汪汪队/第01集.mp4").write_bytes(b"x")
    (env.media_dir / "动画片/汪汪队/第02集.mp4").write_bytes(b"x")
    (env.media_dir / "电影").mkdir(parents=True)
    (env.media_dir / "电影/独行月球.mp4").write_bytes(b"x")

    _scan_until_done(env)

    ep1 = _media(env, "动画片/汪汪队/第01集.mp4")
    ep2 = _media(env, "动画片/汪汪队/第02集.mp4")
    movie = _media(env, "电影/独行月球.mp4")
    assert ep1.media_type == "episode" and ep2.media_type == "episode"
    assert ep1.auto_series_key == "动画片/汪汪队"
    assert movie.media_type == "movie" and movie.auto_series_key is None

    with env.db.session() as s:
        from kindo.models import Episode, Series

        series = s.query(Series).filter(Series.title == "汪汪队").one()
        eps = (
            s.query(Episode).filter(Episode.series_id == series.id)
            .order_by(Episode.episode_no).all()
        )
        assert [e.episode_no for e in eps] == [1, 2]


def test_scan_auto_group_idempotent_and_release(env: Env):
    """重扫幂等（版本不涨）；目录缩成单文件后解除归组、类型回退 movie。"""
    d = env.media_dir / "动画片/汪汪队"
    d.mkdir(parents=True)
    (d / "第01集.mp4").write_bytes(b"x")
    (d / "第02集.mp4").write_bytes(b"x")

    _scan_until_done(env)
    ep1 = _media(env, "动画片/汪汪队/第01集.mp4")
    v1 = ep1.metadata_version
    _scan_until_done(env)
    ep1 = _media(env, "动画片/汪汪队/第01集.mp4")
    assert ep1.metadata_version == v1  # 幂等：自动归组不产生版本抖动

    (d / "第02集.mp4").unlink()
    _scan_until_done(env)
    ep1 = _media(env, "动画片/汪汪队/第01集.mp4")
    assert ep1.media_type == "movie"
    assert ep1.auto_series_key is None
    with env.db.session() as s:
        from kindo.models import Episode

        assert s.query(Episode).filter(Episode.media_id == ep1.id).count() == 0


def test_sidecar_declared_group_not_touched_by_auto(env: Env):
    """sidecar 声明的归组优先，自动归组让位（auto_series_key 保持空）。"""
    build_sample_library(env.media_dir)
    d = env.media_dir / "series/汪汪队"
    # 再放一个无 sidecar 的裸文件进同一目录：自动归组键 = series/汪汪队，
    # 与 sidecar 系列"汪汪队立大功"不同名也不冲突（sidecar 的归组不被覆盖）
    (d / "S01E03.mkv").write_bytes(b"x")

    _scan_until_done(env)

    s1 = _media(env, "series/汪汪队/S01E01.mkv")
    s3 = _media(env, "series/汪汪队/S01E03.mkv")
    assert s1.auto_series_key is None  # sidecar 声明
    with env.db.session() as s:
        from kindo.models import Episode, Series

        declared = s.query(Series).filter(Series.title == "汪汪队立大功").one()
        n_declared = s.query(Episode).filter(Episode.series_id == declared.id).count()
        assert n_declared == 2  # S01E03 未被并入 sidecar 系列
        auto = s.query(Series).filter(Series.title == "汪汪队").one()
        assert s3.auto_series_key == "series/汪汪队"
        assert (
            s.query(Episode).filter(Episode.series_id == auto.id).count() == 1
        )


def test_parent_edit_group_wins_and_blocks_auto(env: Env):
    """家长 PATCH 归组后重扫不被自动归组改写。"""
    d = env.media_dir / "动画片/佩奇"
    d.mkdir(parents=True)
    (d / "01.mp4").write_bytes(b"x")
    (d / "02.mp4").write_bytes(b"x")

    _scan_until_done(env)
    m = _media(env, "动画片/佩奇/01.mp4")
    r = env.client.patch(
        f"/api/v1/admin/media/{m.id}",
        json={"series": {"name": "小猪佩奇（家长整理）", "episode_no": 1}},
        headers=env.admin_headers(),
    )
    assert r.status_code == 200, r.text
    _scan_until_done(env)

    m = _media(env, "动画片/佩奇/01.mp4")
    assert m.auto_series_key is None
    with env.db.session() as s:
        from kindo.models import Episode, Series

        parent_series = (
            s.query(Series).filter(Series.title == "小猪佩奇（家长整理）").one()
        )
        assert (
            s.query(Episode).filter(Episode.series_id == parent_series.id).count() == 1
        )


# ---------- 本地重建端点 ----------


def test_auto_group_rebuild_endpoint(env: Env):
    """存量库零网络回填：直接造 DB 行 → POST 重建 → 归组与类型正确；
    sidecar/家长建立的归组不被触碰；重复调用幂等。"""
    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    from kindo.models import Episode, Media, Series
    from kindo.util import new_id

    with env.db.session() as s:
        # 模拟存量：裸文件（重扫前的状态）
        for pk in ("动画片/汪汪队/第01集.mp4", "动画片/汪汪队/第02集.mp4", "电影/大电影.mp4"):
            s.add(Media(id=new_id(), mount_id="family", path_key=pk, title=pk,
                        media_type="movie", metadata_version=1))
        # 模拟 sidecar 已建立的归组（auto_series_key 为空 + Episode 存在）
        declared = Media(id=new_id(), mount_id="family",
                         path_key="动画片/牛津树/L1.mp4", title="L1",
                         media_type="episode", metadata_version=1)
        s.add(declared)
        series = Series(id=new_id(), title="牛津阅读树动画")
        s.add(series)
        s.flush()
        s.add(Episode(id=new_id(), series_id=series.id, media_id=declared.id,
                      season_no=1, episode_no=1))
        # 声明归组但集号是缺省占位（sidecar 只写系列名的形态）
        bare = Media(id=new_id(), mount_id="family",
                     path_key="动画片/牛津树动画/第03集.mp4", title="第03集",
                     media_type="episode", metadata_version=1)
        bare2 = Media(id=new_id(), mount_id="family",
                      path_key="动画片/牛津树动画/第07集.mp4", title="第07集",
                      media_type="episode", metadata_version=1)
        s.add_all([bare, bare2])
        bare_series = Series(id=new_id(), title="牛津树（无集号）")
        s.add(bare_series)
        s.flush()
        s.add_all([
            Episode(id=new_id(), series_id=bare_series.id, media_id=bare.id,
                    season_no=1, episode_no=1),
            Episode(id=new_id(), series_id=bare_series.id, media_id=bare2.id,
                    season_no=1, episode_no=1),
        ])
        s.commit()
        declared_id = declared.id
        bare_id = bare.id

    r = env.client.post("/api/v1/admin/media/auto-group", json={}, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["grouped"] >= 2
    assert stats["kept"] >= 1  # sidecar 声明的那条
    assert stats.get("ep_no_filled", 0) == 2  # 占位集号被文件名推断补齐

    with env.db.session() as s:
        ep = s.query(Episode).filter(Episode.media_id == declared_id).one()
        assert ep.series_id == series.id and ep.episode_no == 1  # 未被改写
        bare_ep = s.query(Episode).filter(Episode.media_id == bare_id).one()
        assert bare_ep.episode_no == 3  # 第03集.mp4 推断补齐
        s.query(Series).filter(Series.title == "汪汪队").one()
        media = s.query(Media).filter(Media.path_key == "动画片/汪汪队/第01集.mp4").one()
        assert media.media_type == "episode"
        assert media.auto_series_key == "动画片/汪汪队"
        movie = s.query(Media).filter(Media.path_key == "电影/大电影.mp4").one()
        assert movie.media_type == "movie"

    # 幂等：第二次全部 kept
    r = env.client.post("/api/v1/admin/media/auto-group", json={}, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    assert r.json().get("grouped", 0) == 0 and r.json().get("rebound", 0) == 0


def test_collections_exclude_missing(env: Env):
    """源端已删除（missing）的媒体不得计入合集集数/体积（2026-08-21 治理回归：
    幽灵条目曾把合集集数翻倍、空壳系列常驻合集视图）。"""
    build_sample_library(env.media_dir)
    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    import time

    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    for _ in range(100):
        job = env.state.scanner.get_job(r.json()["job_id"])
        if job.state in ("done", "failed", "interrupted"):
            break
        time.sleep(0.1)
    assert job.state == "done", job.error_summary

    # 删掉系列内一半文件并重扫 → missing 标记
    (env.media_dir / "series/汪汪队/S01E02.mkv").unlink()
    for suffix in (".mkv", ".kindo.yaml", ".zh.srt"):
        f = env.media_dir / f"series/汪汪队/S01E02{suffix}"
        if f.exists():
            f.unlink()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    for _ in range(100):
        job = env.state.scanner.get_job(r.json()["job_id"])
        if job.state in ("done", "failed", "interrupted"):
            break
        time.sleep(0.1)

    r = env.client.get("/api/v1/admin/collections", headers=env.admin_headers())
    by_title = {s["title"]: s for s in r.json()["series"]}
    pa = by_title["汪汪队立大功"]
    assert pa["count"] == 1  # 只剩 S01E01，missing 的 E02 不计入


def test_collections_aggregates(env: Env):
    """collections 聚合返回 age_band/tags/size_bytes/mounts；
    多系列时各系列计数独立（回归：join 缺父子连接条件会笛卡尔积）。"""
    build_sample_library(env.media_dir)
    import yaml

    other = env.media_dir / "series/道奇"
    other.mkdir(parents=True)
    for name, ep in (("第01集.mp4", 1),):
        (other / name).write_bytes(b"x")
        (other / f"{name.rsplit('.', 1)[0]}.kindo.yaml").write_text(
            yaml.safe_dump({"series": {"name": "道奇", "season_no": 1, "episode_no": ep}},
                           allow_unicode=True), encoding="utf-8")
    if not hasattr(env, "csrf"):
        env.bootstrap_admin()
    import time

    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    for _ in range(100):
        job = env.state.scanner.get_job(r.json()["job_id"])
        if job.state in ("done", "failed", "interrupted"):
            break
        time.sleep(0.1)
    assert job.state == "done", job.error_summary

    r = env.client.get("/api/v1/admin/collections", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    by_title = {s["title"]: s for s in data["series"]}
    pa = by_title["汪汪队立大功"]
    assert pa["count"] == 2
    # 笛卡尔积回归：道奇只有 1 集，不得串成 2×N
    assert by_title["道奇"]["count"] == 1
    assert sum(s["count"] for s in data["series"]) == 3
    assert pa["duration_ms"] > 0
    assert pa["size_bytes"] > 0
    assert "天天" in pa["tags"]  # 出现频次 top
    assert pa["age_band"] is None
    assert pa["mounts"] == [{"mount_id": "family", "label": "family"}]
