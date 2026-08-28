"""批量清理设备契约测试（2026-08-25：已撤销/长期离线，在线永不清理）。"""
from datetime import UTC, datetime, timedelta

from conftest import requires_ffprobe


@requires_ffprobe
def test_devices_cleanup_criteria(env):
    env.bootstrap_admin()
    from kindo.models import Device
    from kindo.util import new_id

    now = datetime.now(UTC)
    n = [0]

    def dev(name: str, *, status: str = "active",
            paired: timedelta = timedelta(days=1),
            last_seen: timedelta | None = None) -> Device:
        n[0] += 1
        return Device(
            id=new_id(), name=name, token_hash=f"hash-{n[0]}-{name}", status=status,
            paired_at=now - paired,
            last_seen_at=now - last_seen if last_seen else None)

    rows = {
        "revoked_old": dev("撤销已久", status="revoked", paired=timedelta(days=40)),
        "revoked_recent": dev("刚撤销", status="revoked", paired=timedelta(days=1)),
        "stale_offline": dev("闲置30天", paired=timedelta(days=35),
                             last_seen=timedelta(days=30)),
        "fresh": dev("今天在用", paired=timedelta(days=90),
                     last_seen=timedelta(hours=1)),
        "never_seen_old": dev("配对后从未活跃", paired=timedelta(days=60)),
    }
    with env.db.session() as s:
        s.add_all(rows.values())
        s.commit()

    # 7 天阈值 + 清理已撤销：应删 revoked_old/revoked_recent/stale_offline/never_seen_old
    r = env.client.post("/api/v1/admin/devices/cleanup",
                        json={"revoked": True, "offline_days": 7},
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 4
    assert "今天在用" not in body["devices"]

    with env.db.session() as s:
        left = {d.name for d in s.query(Device).all()}
    assert left == {"今天在用"}, left

    # 幂等：再跑一次无可清理
    r = env.client.post("/api/v1/admin/devices/cleanup",
                        json={"revoked": True, "offline_days": 7},
                        headers=env.admin_headers())
    assert r.json()["deleted"] == 0
