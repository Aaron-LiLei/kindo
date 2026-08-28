"""ContentEntity 融合回归（2026-08-26 工程治理）。

覆盖：① 搜索索引含系列/课程标题与实体树标题（TMDB 中文系列名检索到剧集）；
② 系列↔实体结构锚点关联（episode entity→season→series root）——改名不断链；
③ get_or_create_series_entity 改名后复用既有实体（保留匹配与海报）；
④ legacy 无锚点行回退 title 匹配。
"""
from __future__ import annotations

from kindo.media.catalog import admin_collections, media_detail, search_media
from kindo.media.content_catalog import (
    get_or_create_series_entity,
    series_entities_by_series,
)
from kindo.models import ContentEntity, Course, Episode, Lesson, Media, Series
from kindo.util import new_id


def _add_series_with_entities(env, *, series_title: str, entity_title: str,
                              ep_titles: list[str]) -> tuple[str, list[str]]:
    """直插 v0.2 行 + 完整实体树（episode→season→series）。返回 (series_id, media_ids)。"""
    sid, ent_series, ent_season = new_id(), new_id(), new_id()
    media_ids: list[str] = []
    with env.db.session() as s:
        s.add(Series(id=sid, title=series_title))
        s.add(ContentEntity(id=ent_series, entity_type="series", title=entity_title))
        s.add(ContentEntity(id=ent_season, entity_type="season", parent_id=ent_series,
                            title="第 1 季", sequence_no=1))
        s.flush()
        for i, t in enumerate(ep_titles, start=1):
            mid = new_id()
            s.add(Media(id=mid, mount_id="family", path_key=f"/x/{mid}.mp4",
                        title=t, media_type="episode", duration_ms=10_000))
            s.flush()
            s.add(Episode(id=new_id(), series_id=sid, media_id=mid,
                          season_no=1, episode_no=i, title=t))
            s.add(ContentEntity(id=new_id(), entity_type="episode",
                                parent_id=ent_season, source_media_id=mid,
                                title=t, sequence_no=i))
            s.flush()
            media_ids.append(mid)
        s.commit()
    return sid, media_ids


def test_search_matches_series_and_entity_titles(env):
    """集标题不含系列名时：系列行标题与实体树标题（TMDB 中文名）都能检索到集。"""
    # v0.2 系列行叫 "Maisy Show"；实体树系列标题是中文 "小鼠波波"（TMDB 确认后）
    _sid, media_ids = _add_series_with_entities(
        env, series_title="Maisy Show", entity_title="小鼠波波",
        ep_titles=["Maisy EP01", "Maisy EP02"])
    with env.db.session() as s:
        # 1) 实体树标题（中文系列名）
        hits, _cur = search_media(s, "小鼠波波")
        assert sorted(m.id for m in hits) == sorted(media_ids)
        # 2) v0.2 系列行标题
        hits, _cur = search_media(s, "Maisy Show")
        assert {m.id for m in hits} == set(media_ids)
        # 3) 集自身标题（原有行为不回归）
        hits, _cur = search_media(s, "EP01")
        assert [m.id for m in hits] == [media_ids[0]]

        # 课程标题同样可检索
        cid, lid = new_id(), new_id()
        s.add(Course(id=cid, title="趣味英语课"))
        s.add(Media(id=lid, mount_id="family", path_key=f"/x/{lid}.mp4",
                    title="Lesson A", media_type="lesson", duration_ms=5_000))
        s.flush()
        s.add(Lesson(id=new_id(), course_id=cid, media_id=lid,
                     chapter_no=1, lesson_no=1, title="Lesson A"))
        s.commit()
        hits, _cur = search_media(s, "趣味英语")
        assert [m.id for m in hits] == [lid]


def test_rename_keeps_entity_linkage(env):
    """系列行改名后：结构锚点关联不断链（entity_id/海报保留），且不新建实体。"""
    sid, media_ids = _add_series_with_entities(
        env, series_title="原名", entity_title="TMDB 名", ep_titles=["A", "B"])
    env.bootstrap_admin()  # collections 不需要登录，但保持环境完整

    with env.db.session() as s:
        before = series_entities_by_series(s, [s.get(Series, sid)]).get(sid)
        assert before is not None and before.title == "TMDB 名"

        s.get(Series, sid).title = "家长改的新名"
        s.commit()

        after = series_entities_by_series(s, [s.get(Series, sid)]).get(sid)
        assert after is not None and after.id == before.id  # 同一实体

        # 改名后 get_or_create 复用锚点实体，不新建（匹配/海报保留）
        reused = get_or_create_series_entity(s, sid, "家长改的新名")
        assert reused.id == before.id
        assert s.query(ContentEntity).filter(
            ContentEntity.entity_type == "series").count() == 1

    with env.db.session() as s:
        cols = admin_collections(s)
        card = next(c for c in cols["series"] if c["series_id"] == sid)
        assert card["entity_id"] == before.id
        detail = media_detail(s, s.get(Media, media_ids[0]), profile_id="default")
        assert detail["series_entity_id"] == before.id


def test_legacy_series_falls_back_to_title(env):
    """无实体树锚点的 legacy 系列：title 匹配回退（旧行为保持）。"""
    sid = new_id()
    mid = new_id()
    ent_id = new_id()
    with env.db.session() as s:
        s.add(Series(id=sid, title="老库系列"))
        s.add(Media(id=mid, mount_id="family", path_key=f"/x/{mid}.mp4",
                    title="老库系列 第1集", media_type="episode"))
        # 只有系列实体（无 episode/season 锚点——如迁移期半程状态）
        s.add(ContentEntity(id=ent_id, entity_type="series", title="老库系列"))
        s.flush()
        s.add(Episode(id=new_id(), series_id=sid, media_id=mid,
                      season_no=1, episode_no=1))
        s.commit()
        mapped = series_entities_by_series(s, [s.get(Series, sid)]).get(sid)
        assert mapped is not None and mapped.id == ent_id
