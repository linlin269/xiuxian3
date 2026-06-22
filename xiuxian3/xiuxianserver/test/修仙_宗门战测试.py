"""修仙宗门战测试。

运行方式：

    python test/修仙_宗门战测试.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from apscheduler.triggers.cron import CronTrigger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from 修仙.common import format_effect, ts
from 修仙.sect_war import (
    record_sect_merit_conn,
    record_sect_robbery_influence_conn,
    sect_bonus_conn,
    sect_city_bonus_for_position_conn,
    sect_direction_bonus_conn,
    sect_war_cycle_bounds,
    sect_war_in_battle_window,
    sect_war_in_reward_claim_window,
    sect_war_is_member_locked,
    sect_war_reward_member_count,
    sect_war_robbery_influence,
)
from 修仙.sql import XiuxianDB
from 修仙.背包.service import BackpackService
from 修仙.纳戒.service import RingService
from 修仙.玩家.service import PlayerService
from 修仙.宗门 import scheduler as sect_scheduler
from 修仙.宗门.service import SectService
from 修仙.武器.service import WeaponService


def main() -> None:
    """验证宗门战关键流程。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_sect_war_test.db")
        player = PlayerService(db)
        backpack = BackpackService(db)
        ring = RingService(db)
        sect = SectService(db)
        sect._is_member_locked = lambda value=None: False  # type: ignore[method-assign]
        weapon = WeaponService(db)
        try:
            assert _index_exists(db, "idx_sects_founder_id")
            _check_sect_war_scheduler()
            assert "创建成功" in player.create("owner", "宗主甲")
            assert "创建成功" in player.create("member", "宗门乙")
            weapon.ensure_starter_weapon("owner")
            weapon.ensure_starter_weapon("member")
            _build_sect(db, sect, "owner", "青云宗")
            _join_sect(db, sect, "member", "青云宗")
            owner_sect = db.fetch_one("SELECT sect_id FROM sects WHERE name = ?", ("青云宗",))
            assert owner_sect is not None
            owner_sect_id = int(owner_sect["sect_id"])
            stats = _sect_stats(db, owner_sect_id)
            assert stats is not None
            assert int(stats["level"]) == 1
            db.execute("UPDATE city_world_states SET city_level = 40 WHERE location_name = '流沙海市'")
            with db.transaction() as conn:
                city_bonus = sect_city_bonus_for_position_conn(conn, -49, -49)
                sect_bonus = sect_bonus_conn(conn, owner_sect_id)
            assert float(city_bonus["bonus"]) > 0
            assert city_bonus["covers"][0]["location_name"] == "流沙海市"
            assert float(sect_bonus["total_bonus"]) > float(sect_bonus["base_bonus"]) > 0

            overview_text = sect.overview("owner")
            assert "宗门等级" in overview_text
            assert "底蕴" in overview_text
            assert "宗门加持" in overview_text
            assert "原始城池增益" in overview_text
            assert "主影响：流沙海市" in overview_text

            monday = datetime(2026, 6, 15, 12, 0, 0)
            saturday = datetime(2026, 6, 20, 12, 0, 0)
            sunday = datetime(2026, 6, 21, 12, 0, 0)
            next_monday = datetime(2026, 6, 22, 12, 0, 0)
            finished_monday = datetime(2026, 6, 8, 12, 0, 0)
            finished_saturday = datetime(2026, 6, 13, 12, 0, 0)
            finished_sunday = datetime(2026, 6, 14, 12, 0, 0)
            finished_cycle_start = "2026-06-08"
            finished_cycle_end = "2026-06-15"
            assert sect_war_cycle_bounds(monday) == ("2026-06-15", "2026-06-22")
            assert sect_war_cycle_bounds(sunday) == ("2026-06-15", "2026-06-22")
            assert sect_war_cycle_bounds(next_monday) == ("2026-06-22", "2026-06-29")
            assert sect_war_in_battle_window(monday)
            assert sect_war_in_battle_window(saturday)
            assert not sect_war_in_battle_window(sunday)
            assert not sect_war_in_reward_claim_window(saturday)
            assert sect_war_in_reward_claim_window(sunday)
            assert not sect_war_is_member_locked(monday)
            assert sect_war_is_member_locked(saturday)
            assert sect_war_is_member_locked(sunday)

            base_influence = sect_war_robbery_influence(
                success=True,
                item_value=3000,
                battle={"actions": [1, 2, 3], "left_level": 10, "right_level": 12},
            )
            assert sect_war_reward_member_count(10) == 3
            assert sect_war_reward_member_count(10, 0.8) == 6
            owner_influence = _record_robbery_influence(db, "owner", finished_monday)
            assert owner_influence > base_influence
            _record_robbery_influence(db, "owner", finished_monday)
            member_influence = _record_robbery_influence(db, "member", finished_saturday)
            stats = _sect_stats(db, owner_sect_id)
            assert stats is not None
            assert int(stats["influence_merit"]) >= owner_influence * 2 + member_influence
            assert int(stats["exp"]) > 0
            assert _sect_merit_count(db, owner_sect_id, "influence") >= 3
            with db.transaction() as conn:
                record_sect_merit_conn(conn, "owner", "support", 1000, source="测试供养")
                support_bonus = sect_direction_bonus_conn(conn, "owner", "support")
            assert support_bonus > 0
            assert _cycle_record_count(db, finished_cycle_start) == 1
            assert _cycle_influence(db, finished_cycle_start) > 0
            assert _personal_influence(db, "owner", finished_cycle_start) == owner_influence * 2
            assert _personal_influence(db, "member", finished_cycle_start) == member_influence
            with db.transaction() as conn:
                wrong_sect = record_sect_robbery_influence_conn(
                    conn,
                    "owner",
                    sect_id=999999,
                    success=True,
                    item_value=3000,
                    battle={"actions": [1, 2, 3], "left_level": 10, "right_level": 12},
                    detail="wrong sect",
                    occurred_at=finished_monday,
                )
            assert wrong_sect == 0
            before_sunday = _cycle_influence(db, finished_cycle_start)
            _record_robbery_influence(db, "owner", finished_sunday)
            assert _cycle_influence(db, finished_cycle_start) == before_sunday
            _record_robbery_influence(db, "outsider", finished_monday)
            assert _cycle_record_count(db, finished_cycle_start) == 1
            assert _cycle_influence(db, "2026-06-15") == 0
            _record_robbery_influence(db, "owner", monday)
            assert _cycle_record_count(db, "2026-06-15") == 1
            assert _cycle_influence(db, "2026-06-15") > 0

            war_text = sect.war("owner")
            assert "宗门战" in war_text
            assert "本期影响力" in war_text
            assert "个人贡献" in war_text
            assert "宗主甲" in war_text
            assert "宗门加持" in war_text
            assert "城池范围" in war_text
            assert "%" in war_text

            reward = sect.claim_war_reward("owner")
            assert "宗门战奖励" in reward or "没有可领取" in reward

            with db.transaction() as conn:
                sect._ensure_rewards_generated_conn(conn, finished_cycle_start, finished_cycle_end)
            rewards = db.fetch_all(
                "SELECT reward_type, client_id FROM sect_war_rewards WHERE cycle_start = ? ORDER BY reward_type, client_id",
                (finished_cycle_start,),
            )
            assert any(row["reward_type"] == "sect_random" for row in rewards)
            assert any(row["reward_type"] == "personal_top" and row["client_id"] == "owner" for row in rewards)

            db.execute("DELETE FROM sect_members WHERE client_id = ?", ("member",))
            _build_sect(db, sect, "member", "临时宗", x=-48, y=-48)
            temp_sect = db.fetch_one("SELECT sect_id FROM sects WHERE name = ?", ("临时宗",))
            assert temp_sect is not None
            temp_sect_id = int(temp_sect["sect_id"])
            with db.transaction() as conn:
                record_sect_robbery_influence_conn(
                    conn,
                    "member",
                    sect_id=temp_sect_id,
                    success=True,
                    item_value=1000,
                    battle={"actions": [1], "left_level": 10, "right_level": 10},
                    detail="temporary sect",
                    occurred_at=datetime(2026, 6, 1, 12, 0, 0),
                )
            assert any(row["name"] == "临时宗" for row in sect._cycle_rankings("2026-06-01"))
            db.execute("DELETE FROM sect_members WHERE client_id = ?", ("member",))
            assert "创立过仍存世的宗门" in sect.create("member", "-47 -47 再起宗")
            with db.transaction() as conn:
                conn.execute("DELETE FROM sect_members WHERE sect_id = ?", (temp_sect_id,))
                conn.execute("DELETE FROM sects WHERE sect_id = ?", (temp_sect_id,))
                sect._ensure_rewards_generated_conn(conn, "2026-06-01", "2026-06-08")
            assert not any(row["name"] == "临时宗" for row in sect._cycle_rankings("2026-06-01"))
            assert _reward_count_for_sect(db, temp_sect_id, "2026-06-01") == 0
            _build_sect(db, sect, "member", "再起宗", x=-47, y=-47)
            assert db.fetch_one("SELECT * FROM sects WHERE name = ?", ("再起宗",)) is not None

            db.execute("DELETE FROM ring_items WHERE client_id = ? AND ring_item_id = 'cuifengdan'", ("owner",))
            db.execute("UPDATE sect_war_rewards SET claimed = 0, claimed_at = NULL WHERE client_id = ?", ("owner",))
            before_claim = _cuifengdan_count(db, "owner")
            pending_reward = db.fetch_one(
                """
                SELECT claimed
                FROM sect_war_rewards
                WHERE client_id = ? AND cycle_start = ?
                LIMIT 1
                """,
                ("owner", finished_cycle_start),
            )
            assert pending_reward and int(pending_reward["claimed"]) == 0
            assert _cuifengdan_count(db, "owner") == before_claim
            assert "待领奖励" in sect.war("owner")
            claim_text = sect.claim_war_reward("owner")
            assert "已领取宗门战奖励" in claim_text
            assert _cuifengdan_count(db, "owner") > before_claim
            claimed_reward = db.fetch_one(
                """
                SELECT claimed
                FROM sect_war_rewards
                WHERE client_id = ? AND cycle_start = ?
                LIMIT 1
                """,
                ("owner", finished_cycle_start),
            )
            assert claimed_reward and int(claimed_reward["claimed"]) == 1

            item = db.fetch_one("SELECT * FROM ring_item_defs WHERE ring_item_id = 'cuifengdan'")
            assert item is not None
            assert int(item["usable"]) == 0
            assert item["category"] == "专属道具"
            assert "武器等级上限+1，最高100级" in format_effect(item["effect"])

            db.execute("DELETE FROM ring_items WHERE client_id = ? AND ring_item_id = 'cuifengdan'", ("owner",))
            _give_cuifengdan(db, "owner", 3)
            blocked = backpack.use_item("owner", "淬锋丹")
            assert "淬锋丹不能直接使用" in blocked
            assert _cuifengdan_count(db, "owner") == 3

            weapon_id = int(db.fetch_one("SELECT weapon_id FROM player_weapons WHERE holder_id = ?", ("owner",))["weapon_id"])
            extra_weapon_id = weapon.create_weapon("owner", "qinglan_duanjian", "凡品", 40)

            default_temper_text = ring.temper_weapon("owner", "")
            assert "淬锋成功" in default_temper_text
            row = db.fetch_one("SELECT max_level FROM player_weapons WHERE weapon_id = ?", (weapon_id,))
            assert row is not None and int(row["max_level"]) == 41

            temper_text = ring.temper_weapon("owner", str(extra_weapon_id))
            assert "淬锋成功" in temper_text
            row = db.fetch_one("SELECT max_level FROM player_weapons WHERE weapon_id = ?", (extra_weapon_id,))
            assert row is not None and int(row["max_level"]) == 41
            assert _cuifengdan_count(db, "owner") == 1
        finally:
            db.close()

    print("修仙宗门战测试通过")


def _build_sect(db: XiuxianDB, sect: SectService, client_id: str, name: str, *, x: int = -49, y: int = -49) -> None:
    """把玩家移动到一个可建宗坐标并建立宗门。"""

    db.execute("UPDATE players SET x = ?, y = ? WHERE client_id = ?", (x, y, client_id))
    assert "宗门创建成功" in sect.create(client_id, f"{x} {y} {name}")


def _join_sect(db: XiuxianDB, sect: SectService, client_id: str, name: str) -> None:
    """加入宗门。"""

    db.execute("UPDATE players SET x = -49, y = -49 WHERE client_id = ?", (client_id,))
    assert "已加入宗门" in sect.join(client_id, name)


def _record_robbery_influence(
    db: XiuxianDB,
    client_id: str,
    occurred_at: datetime,
) -> int:
    """按真实宗门战规则写入一笔抢劫影响力。"""

    influence = 0
    with db.transaction() as conn:
        membership = conn.execute("SELECT sect_id FROM sect_members WHERE client_id = ?", (client_id,)).fetchone()
        sect_id = int(membership["sect_id"]) if membership else 0
        influence = record_sect_robbery_influence_conn(
            conn,
            client_id,
            sect_id=sect_id,
            success=True,
            item_value=3000,
            battle={"actions": [1, 2, 3], "left_level": 10, "right_level": 12},
            detail="test",
            occurred_at=occurred_at,
        )
    return influence


def _give_cuifengdan(db: XiuxianDB, client_id: str, quantity: int) -> None:
    """补测试用淬锋丹。"""

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ring_items (client_id, ring_item_id, quantity)
            VALUES (?, 'cuifengdan', ?)
            ON CONFLICT(client_id, ring_item_id)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (client_id, quantity),
        )


def _cycle_record_count(db: XiuxianDB, cycle_start: str) -> int:
    """读取某周期影响力记录条数。"""

    row = db.fetch_one(
        "SELECT COUNT(*) AS count FROM sect_influence_records WHERE cycle_start = ?",
        (cycle_start,),
    )
    return int(row["count"]) if row else 0


def _cycle_influence(db: XiuxianDB, cycle_start: str) -> int:
    """读取某周期宗门影响力总量。"""

    row = db.fetch_one(
        "SELECT COALESCE(SUM(influence), 0) AS influence FROM sect_influence_records WHERE cycle_start = ?",
        (cycle_start,),
    )
    return int(row["influence"]) if row else 0


def _sect_stats(db: XiuxianDB, sect_id: int):
    """读取宗门长期底蕴。"""

    return db.fetch_one("SELECT * FROM sect_stats WHERE sect_id = ?", (int(sect_id),))


def _sect_merit_count(db: XiuxianDB, sect_id: int, category: str) -> int:
    """读取某类宗门底蕴流水数量。"""

    row = db.fetch_one(
        "SELECT COUNT(*) AS count FROM sect_merit_records WHERE sect_id = ? AND category = ?",
        (int(sect_id), category),
    )
    return int(row["count"]) if row else 0


def _reward_count_for_sect(db: XiuxianDB, sect_id: int, cycle_start: str) -> int:
    """读取某宗门某周期奖励行数。"""

    row = db.fetch_one(
        "SELECT COUNT(*) AS count FROM sect_war_rewards WHERE sect_id = ? AND cycle_start = ?",
        (sect_id, cycle_start),
    )
    return int(row["count"]) if row else 0


def _personal_influence(db: XiuxianDB, client_id: str, cycle_start: str) -> int:
    """读取某玩家某周期个人贡献。"""

    row = db.fetch_one(
        "SELECT COALESCE(SUM(influence), 0) AS influence FROM sect_contribution_records WHERE client_id = ? AND cycle_start = ?",
        (client_id, cycle_start),
    )
    return int(row["influence"]) if row else 0


def _cuifengdan_count(db: XiuxianDB, client_id: str) -> int:
    """读取测试玩家纳戒里的淬锋丹数量。"""

    row = db.fetch_one(
        "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = 'cuifengdan'",
        (client_id,),
    )
    return int(row["quantity"]) if row else 0


def _index_exists(db: XiuxianDB, name: str) -> bool:
    """确认关键数据库约束索引存在。"""

    return db.fetch_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (name,),
    ) is not None


def _check_sect_war_scheduler() -> None:
    """确认宗门战结算定时任务能被 APScheduler 接受。"""

    jobs = [
        task
        for task in sect_scheduler.Scheduler.sync_list
        if task.get("func") is sect_scheduler.sect_war_generate_rewards
    ]
    assert jobs, "宗门战结算定时任务没有注册"
    task = jobs[0]
    args = task.get("args", ())
    assert args == ("cron",)
    CronTrigger(**{k: v for k, v in task.get("kwargs", {}).items() if k != "id"})


if __name__ == "__main__":
    main()
