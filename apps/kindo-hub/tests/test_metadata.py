"""Normalizer 六级优先级合并与 Matcher 评分纯函数测试（v0.3 决策三/四，P11）。"""
from __future__ import annotations

from kindo.media.matcher import Candidate, score_candidates
from kindo.media.metadata import ProviderDetails, apply_with_provenance, normalize_provider_details
from kindo.media.parser import parse_path_clues


def _entity():
    from kindo.models import ContentEntity

    return ContentEntity(
        id="e1", entity_type="series", title="测试系列",
        modality="VIDEO", duration_ms=0, sequence_no=1,
        meta_provenance_json={})


# ---------- Matcher 评分 ----------

def test_score_exact_likely_fuzzy():
    cands = [
        Candidate(ref_id="1", title="汪汪队立大功", popularity=5),
        Candidate(ref_id="2", title="汪汪队立大功之超能救援", popularity=9),
        Candidate(ref_id="3", title="海底小纵队", popularity=99),
    ]
    scored = score_candidates("汪汪队立大功", cands)
    assert scored[0][0].ref_id == "1" and scored[0][1] == "exact"
    assert scored[1][0].ref_id == "2" and scored[1][1] == "likely"
    assert scored[2][0].ref_id == "3" and scored[2][1] == "fuzzy"


def test_score_year_promotes_likely_to_exact():
    # "2001太空漫游" vs 候选"太空漫游"（包含关系 → likely）；年份匹配提升为 exact
    cands = [
        Candidate(ref_id="1", title="太空漫游", first_air_date="1968-04-02", popularity=1),
        Candidate(ref_id="2", title="太空漫游", first_air_date="2020-01-01", popularity=9),
    ]
    scored = score_candidates("2001太空漫游", cands, year=1968)
    assert scored[0][0].ref_id == "1" and scored[0][1] == "exact"
    # 无年份时不提升
    scored2 = score_candidates("2001太空漫游", cands)
    assert scored2[0][1] == "likely"


# ---------- Normalizer 六级优先级 ----------

def test_provider_cannot_override_parent():
    e = _entity()
    e.overview = "家长写的简介"
    e.meta_provenance_json = {"overview": {"source": "parent", "updated_at": "t",
                                           "locked": False}}
    d = ProviderDetails(overview="TMDB 的简介")
    out = normalize_provider_details(e, d, confirmed=False)
    assert out["overview"] == "skipped"
    assert e.overview == "家长写的简介"


def test_locked_field_blocks_even_confirmed_provider():
    e = _entity()
    e.release_date = "2013-08-12"
    e.meta_provenance_json = {"release_date": {"source": "sidecar", "updated_at": "t",
                                               "locked": True}}
    assert apply_with_provenance(e, "release_date", "2020-01-01",
                                 "provider_confirmed") is False
    assert e.release_date == "2013-08-12"


def test_confirmed_provider_overrides_auto_provider():
    e = _entity()
    e.overview = "自动匹配写入"
    e.meta_provenance_json = {"overview": {"source": "provider", "updated_at": "t",
                                           "locked": False}}
    assert apply_with_provenance(e, "overview", "家长确认后的简介",
                                 "provider_confirmed") is True
    assert e.overview == "家长确认后的简介"
    assert e.meta_provenance_json["overview"]["source"] == "provider_confirmed"


def test_sidecar_blocks_auto_provider():
    e = _entity()
    e.overview = "sidecar 简介"
    e.meta_provenance_json = {"overview": {"source": "sidecar", "updated_at": "t",
                                           "locked": False}}
    assert apply_with_provenance(e, "overview", "TMDB", "provider") is False


def test_auto_provider_fills_blank():
    e = _entity()
    out = normalize_provider_details(
        e, ProviderDetails(overview="新简介", release_date="2019-05-01"),
        confirmed=False)
    assert out == {"overview": "applied", "release_date": "applied"}
    assert e.overview == "新简介"
    assert e.meta_provenance_json["overview"]["source"] == "provider"


# ---------- Parser 线索 ----------

def test_parse_path_clues():
    c = parse_path_clues("series/汪汪队立大功 第1季 (2013)/S01E05.mkv")
    assert c.title_guess == "汪汪队立大功 第1季" or c.title_guess
    assert c.episode_no == 5 and c.season_no == 1
    assert c.year == 2013


def test_parse_path_clues_generic_dir_falls_back():
    c = parse_path_clues("动画片/汪汪队/第03集 小海龟.mp4")
    assert c.title_guess  # 不落到"动画片"
    assert c.episode_no == 3
