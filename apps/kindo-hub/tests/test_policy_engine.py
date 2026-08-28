"""Policy 引擎单测（PRD POL-001~005/008，技术方案 §9.2/§15.1）。

直接构造数据库行，不依赖扫描/ffmpeg。
"""
from datetime import UTC, datetime

from kindo.models import (
    Media,
    Playback,
    PlaybackGrant,
    PolicyConfig,
    Profile,
    ViewingInterval,
)
from kindo.util import new_id


def seed(session_factory, media_type="episode", duration_ms=600_000, tags=None, mount="family"):
    with session_factory() as s:
        profile = s.query(Profile).first()
        profile_id = profile.id
        media = Media(
            id=new_id(), mount_id=mount, path_key=f"{new_id()}.mp4", title="测试内容",
            media_type=media_type, duration_ms=duration_ms, playable=True, missing=False,
            tags_json=tags or {},
        )
        s.add(media)
        s.commit()
        return profile_id, media.id


def set_policy(env, rules: dict):
    env.bootstrap_admin()
    r = env.client.put("/api/v1/admin/policy", json=rules, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    return r.json()


class TestAllowedWindow:
    # 注入确定性时间：测试配置时区为 Asia/Shanghai（UTC+8，无夏令时），
    # UTC 20:30 = 上海 04:30，窗口判定不随运行时刻漂移
    NOW_0430_LOCAL = datetime(2026, 1, 15, 20, 30, tzinfo=UTC)

    def test_outside_window_denied(self, env):
        profile_id, media_id = seed(env.db.session_factory)
        set_policy(env, {"allowed_windows": [{"start": "05:00", "end": "06:00"}]})

        engine = env.state.policy
        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = engine.may_start(s, profile_id, media, "play", self.NOW_0430_LOCAL)
            assert d.decision == "deny"
            assert d.reason_code == "outside_allowed_window"

    def test_inside_window_allowed(self, env):
        profile_id, media_id = seed(env.db.session_factory)
        set_policy(env, {"allowed_windows": [{"start": "04:00", "end": "05:00"}]})

        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(
                s, profile_id, media, "play", self.NOW_0430_LOCAL
            )
            assert d.allowed

    def test_course_continue_maps_to_course_rule(self, env):
        profile_id, media_id = seed(env.db.session_factory, media_type="lesson")
        # 本地 05:00-06:00 窗口外 → course_continue 映射为 course_rule_denied
        set_policy(env, {"allowed_windows": [{"start": "05:00", "end": "06:00"}]})
        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(
                s, profile_id, media, "course_continue",
                datetime(2026, 1, 15, 20, 30, tzinfo=UTC),
            )
            assert not d.allowed
            assert d.reason_code == "course_rule_denied"


class TestDailyLimit:
    def test_daily_limit_reached(self, env):
        profile_id, media_id = seed(env.db.session_factory)
        set_policy(env, {"daily_limit_minutes": 30})
        # 已观看 31 分钟
        with env.db.session() as s:
            pb = Playback(id=new_id(), device_id="d1", profile_id=profile_id,
                          media_id=media_id, action="play", source="ui", state="stopped")
            s.add(pb)
            s.flush()
            s.add(ViewingInterval(
                id=new_id(), playback_id=pb.id, started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC), duration_ms=31 * 60_000,
                close_reason="stopped",
            ))
            s.commit()
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(s, profile_id, media, "play",
                                           datetime.now(UTC))
            assert d.decision == "deny"
            assert d.reason_code == "daily_limit_reached"
            assert d.constraints["remaining"]["screen_total_seconds"] == 0

    def test_course_exempt_when_configured(self, env):
        profile_id, course_id = seed(env.db.session_factory, media_type="lesson")
        ep_id = None
        with env.db.session() as s:
            ep_media = Media(
                id=new_id(), mount_id="family", path_key=f"{new_id()}.mp4", title="动画",
                media_type="episode", duration_ms=600_000, playable=True, missing=False,
            )
            s.add(ep_media)
            s.commit()
            ep_id = ep_media.id
        set_policy(env, {
            "daily_limit_minutes": 30,
            "course_counts_as_entertainment": False,
        })
        with env.db.session() as s:
            pb = Playback(id=new_id(), device_id="d1", profile_id=profile_id,
                          media_id=ep_id, action="play", source="ui", state="stopped")
            s.add(pb)
            s.flush()
            s.add(ViewingInterval(
                id=new_id(), playback_id=pb.id, started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC), duration_ms=31 * 60_000,
                close_reason="stopped",
            ))
            s.commit()
            course_media = s.get(Media, course_id)
            d = env.state.policy.may_start(s, profile_id, course_media, "course_continue",
                                           datetime.now(UTC))
            # 课程不计入娱乐时长：课程应允许（软限制已用完也不拦课程）
            assert d.allowed


class TestContentScope:
    def test_blocked_tags(self, env):
        profile_id, media_id = seed(
            env.db.session_factory, tags={"themes": ["恐怖"]}
        )
        set_policy(env, {"content_scope": {"blocked_tags": ["恐怖"]}})
        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(s, profile_id, media, "play",
                                           datetime.now(UTC))
            assert d.decision == "deny"
            assert d.reason_code == "content_not_allowed"

    def test_mount_whitelist(self, env):
        profile_id, media_id = seed(env.db.session_factory, mount="family")
        set_policy(env, {"content_scope": {"allowed_mount_ids": ["other"]}})
        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(s, profile_id, media, "play",
                                           datetime.now(UTC))
            assert d.reason_code == "content_not_allowed"


class TestAutoplayAndEpisodes:
    def test_autoplay_disabled_next(self, env):
        profile_id, media_id = seed(env.db.session_factory)
        set_policy(env, {"autoplay": False})
        with env.db.session() as s:
            media = s.get(Media, media_id)
            d = env.state.policy.may_start(s, profile_id, media, "next",
                                           datetime.now(UTC))
            assert d.reason_code == "autoplay_disabled"
            assert "choose" in d.constraints.get("allowed_actions", [])
            d2 = env.state.policy.may_start(s, profile_id, media, "play",
                                            datetime.now(UTC))
            assert d2.allowed  # play 不受 autoplay 限制

    def test_episode_limit_counting(self, env):
        """单集 ≥50% 或 ended 计一次，每集每日最多一次（§9.6）。"""
        profile_id, m1 = seed(env.db.session_factory, duration_ms=100_000)
        _p, m2 = seed(env.db.session_factory, duration_ms=100_000)
        set_policy(env, {"daily_episode_limit": 1})
        with env.db.session() as s:
            # m1 已看 60%（≥50%）计一次
            pb = Playback(id=new_id(), device_id="d1", profile_id=profile_id, media_id=m1,
                          action="play", source="ui", state="stopped", watched_ms=60_000)
            s.add(pb)
            s.commit()
            media2 = s.get(Media, m2)
            d = env.state.policy.may_start(s, profile_id, media2, "next",
                                           datetime.now(UTC))
            assert d.reason_code == "episode_limit_reached"
            # 同一集重复播放不重复计数：m1 再次 play 仍被拦（已达上限）
            media1 = s.get(Media, m1)
            d2 = env.state.policy.may_start(s, profile_id, media1, "play",
                                            datetime.now(UTC))
            assert d2.reason_code == "episode_limit_reached"


class TestMayContinue:
    def test_soft_limit_does_not_cut_current(self, env):
        """软限制不切断进行中的当前集（POL-008 / H-1）。"""
        profile_id, media_id = seed(env.db.session_factory)
        set_policy(env, {"daily_limit_minutes": 30})
        with env.db.session() as s:
            pb = Playback(id=new_id(), device_id="d1", profile_id=profile_id,
                          media_id=media_id, action="play", source="ui", state="playing",
                          position_ms=100_000, watched_ms=0)
            s.add(pb)
            s.flush()
            s.add(ViewingInterval(
                id=new_id(), playback_id=pb.id, started_at=datetime.now(UTC),
                duration_ms=0,
            ))
            # 已看 31 分钟（超过每日限额）但当前集在播放中
            s.query(ViewingInterval).filter(
                ViewingInterval.playback_id == pb.id
            ).update({"duration_ms": 31 * 60_000, "ended_at": datetime.now(UTC),
                      "close_reason": "test"})
            s.commit()
            grant = PlaybackGrant(
                id=new_id(), playback_id=pb.id, device_id="d1", media_id=media_id,
                token_hash="h" * 64, policy_version=self._latest_policy_version(s),
            )
            s.add(grant)
            s.commit()
            media = s.get(Media, media_id)
            d = env.state.policy.may_continue(s, pb, media, datetime.now(UTC))
            assert d.allowed  # 软限制放行当前集

    @staticmethod
    def _latest_policy_version(s):
        return s.query(PolicyConfig.version).order_by(PolicyConfig.version.desc()).first()[0]

    def test_hard_cutoff_stops_current(self, env):
        """硬截止（时段结束）到点停止当前播放。"""
        profile_id, media_id = seed(env.db.session_factory)
        # 本地（Asia/Shanghai）04:30 不在 00:00-00:01 窗口内 → deny（确定性）
        set_policy(env, {"allowed_windows": [{"start": "00:00", "end": "00:01"}]})
        with env.db.session() as s:
            pb = Playback(id=new_id(), device_id="d1", profile_id=profile_id,
                          media_id=media_id, action="play", source="ui", state="playing",
                          position_ms=10_000)
            s.add(pb)
            s.flush()
            version = self._latest_policy_version(s)
            s.add(PlaybackGrant(
                id=new_id(), playback_id=pb.id, device_id="d1", media_id=media_id,
                token_hash="h2" * 32, policy_version=version,
            ))
            s.commit()
            media = s.get(Media, media_id)
            d = env.state.policy.may_continue(s, pb, media,
                                              datetime(2026, 1, 15, 20, 30, tzinfo=UTC))
            assert d.decision == "deny"
            assert d.reason_code == "outside_allowed_window"
