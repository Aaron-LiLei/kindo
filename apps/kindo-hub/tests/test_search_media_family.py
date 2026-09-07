"""AI 检索的家庭可用性（2026-09-07，T-20260902-003-02 排查产出）：
① 停用挂载的内容不再进 AI 检索（与浏览页同口径——桥断开时不再把
   不可播放的内容推给孩子）；
② 中文数字归一：语音「第一集」（ASR 转写）命中标题「第1集」。"""
from __future__ import annotations


def _add_media(env, mid: str, title: str, mount_id: str = "family") -> None:
    from kindo.models import Media

    with env.db.session() as s:
        s.add(Media(id=mid, mount_id=mount_id, path_key=f"{mount_id}/{mid}",
                    title=title, media_type="episode", duration_ms=60_000,
                    playable=True))
        s.commit()


def _add_mount(env, storage_id: str, *, active: bool) -> None:
    from kindo.models import MediaMount

    with env.db.session() as s:
        s.add(MediaMount(id=f"m-{storage_id}", storage_id=storage_id, label=storage_id,
                         active=active, mount_type="local", root_id="", sub_path="",
                         config_json={"path": "x"}))
        s.commit()


def _search_titles(env, query: str, limit: int = 10) -> list[str]:
    from kindo.media.catalog import search_media

    with env.db.session() as s:
        hits, _cur = search_media(s, query, limit=limit)
        return [m.title for m in hits]


def test_normalize_cn_numerals():
    from kindo.media.catalog import normalize_cn_numerals

    assert normalize_cn_numerals("第一集") == "第1集"
    assert normalize_cn_numerals("第一季第二集") == "第1季第2集"
    assert normalize_cn_numerals("第十集") == "第10集"
    assert normalize_cn_numerals("第十五集") == "第15集"
    assert normalize_cn_numerals("第二十集") == "第20集"
    assert normalize_cn_numerals("两只老虎") == "2只老虎"
    assert normalize_cn_numerals("汪汪队") == "汪汪队"  # 无数字不变形


def test_cn_numeral_query_matches_digit_titles(env):
    """语音「我想看汪汪队 第一集」→ 标题「…第一季 第1集」可命中。"""
    _add_media(env, "m-1", "汪汪队立大功 第一季 第1集")
    _add_media(env, "m-2", "汪汪队立大功 第一季 第2集")
    assert "汪汪队立大功 第一季 第1集" in _search_titles(env, "第一集")
    assert "汪汪队立大功 第一季 第2集" in _search_titles(env, "汪汪队 第二集")
    # 原始数字写法不受影响
    assert "汪汪队立大功 第一季 第1集" in _search_titles(env, "第1集")


def test_search_excludes_inactive_mount_media(env):
    """停用挂载不进 AI 检索；重新启用后恢复可见（浏览页同口径）。"""
    _add_media(env, "m-family", "汪汪队立大功 第一季 第1集", mount_id="family")
    _add_media(env, "m-ghost", "汪汪队立大功 网络源 第19集", mount_id="ghost")
    _add_mount(env, "ghost", active=False)

    titles = _search_titles(env, "汪汪队")
    assert "汪汪队立大功 网络源 第19集" not in titles
    assert "汪汪队立大功 第一季 第1集" in titles

    # 重新启用 → 恢复可检索
    from kindo.models import MediaMount

    with env.db.session() as s:
        row = s.query(MediaMount).filter(MediaMount.storage_id == "ghost").one()
        row.active = True
        s.commit()
    assert "汪汪队立大功 网络源 第19集" in _search_titles(env, "汪汪队")
