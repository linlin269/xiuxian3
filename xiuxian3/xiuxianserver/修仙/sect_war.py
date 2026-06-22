"""宗门战公共规则。"""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from math import floor
from math import hypot
import sqlite3
from typing import Any

from .common import now, ts
from .constants import SECT_LEVEL_MAX


SECT_WAR_REWARD_ITEM_ID = "cuifengdan"
SECT_WAR_REWARD_ITEM_NAME = "淬锋丹"
SECT_WAR_SECT_REWARD_RATE = 0.30
SECT_WAR_PERSONAL_REWARD_RATE = 0.15
SECT_WAR_REWARD_TYPE_SECT_RANDOM = "sect_random"
SECT_WAR_REWARD_TYPE_PERSONAL_TOP = "personal_top"
SECT_CITY_BONUS_HARD_CAP = 0.75
SECT_CITY_SYNERGY_FACTORS = (1.0, 0.30, 0.15)
SECT_SELF_BONUS_CAP = 0.30
SECT_TOTAL_BONUS_CAP = 0.80
SECT_MERIT_EXP_WEIGHTS = {
    "build": 1.0,
    "support": 0.65,
    "influence": 0.45,
}
SECT_MERIT_COLUMNS = {
    "build": "build_merit",
    "support": "support_merit",
    "influence": "influence_merit",
}
SECT_MERIT_ALIASES = {
    "build": "build",
    "山门建设": "build",
    "建设": "build",
    "support": "support",
    "供养": "support",
    "influence": "influence",
    "影响力": "influence",
}


def sect_war_cycle_bounds(value: datetime | None = None) -> tuple[str, str]:
    """返回当前宗门战周期，周一开始，下周一零点结束。"""

    current = value or now()
    current_date = current.date()
    start = current_date - timedelta(days=current.weekday())
    end = start + timedelta(days=7)
    return start.isoformat(), end.isoformat()


def sect_war_display_cycle_end(cycle_end: str) -> str:
    """周期结束日展示为周日。"""

    return (datetime.fromisoformat(cycle_end).date() - timedelta(days=1)).isoformat()


def sect_war_cycle_finished(cycle_end: str, value: datetime | None = None) -> bool:
    """判断某个周期是否已经进入可结算时间。

    周日是本周期领取日；虽然周期边界到下周一零点，
    但周日全天不再计分，可以生成并领取本期奖励。
    """

    current = value or now()
    if current >= datetime.fromisoformat(cycle_end):
        return True
    cycle_start = datetime.fromisoformat(cycle_end).date() - timedelta(days=7)
    return current.date() >= cycle_start + timedelta(days=6)


def sect_war_qualified_count(total: int) -> int:
    """前 20% 宗门入围，向上取整。"""

    return ceil(max(0, int(total)) * 0.2) if total > 0 else 0


def sect_war_reward_member_count(total: int, bonus_rate: float = 0.0) -> int:
    """入围宗门成员获得奖励，基础 30%，吃本宗门最终增益。"""

    if total <= 0:
        return 0
    rate = SECT_WAR_SECT_REWARD_RATE * (1.0 + max(0.0, min(SECT_TOTAL_BONUS_CAP, float(bonus_rate))))
    return ceil(max(0, int(total)) * rate)


def sect_war_personal_reward_count(total: int) -> int:
    """个人贡献前 15% 获奖，向上取整。"""

    return ceil(max(0, int(total)) * 0.15) if total > 0 else 0


def sect_war_is_member_locked(value: datetime | None = None) -> bool:
    """周六和周日锁定宗门成员变动。"""

    current = value or now()
    return current.weekday() in (5, 6)


def sect_war_in_battle_window(value: datetime | None = None) -> bool:
    """宗门战斗计分窗口：周一到周六。"""

    current = value or now()
    return current.weekday() in (0, 1, 2, 3, 4, 5)


def sect_war_in_reward_claim_window(value: datetime | None = None) -> bool:
    """宗门战奖励领取窗口：周日全天。"""

    current = value or now()
    return current.weekday() == 6


def sect_war_member_lock_text(value: datetime | None = None) -> str:
    """展示当前成员变动规则。"""

    if sect_war_is_member_locked(value):
        return "锁定中，周六/周日不能建立、加入或退出宗门"
    return "开放中，周一到周五可以加入或退出"


def sect_war_robbery_influence(*, success: bool, item_value: int, battle: dict) -> int:
    """按抢劫结果计算宗门影响力。"""

    actions = battle.get("actions")
    action_count = len(actions) if isinstance(actions, list) else 0
    left_level = max(1, int(battle.get("left_level", 1) or 1))
    right_level = max(1, int(battle.get("right_level", 1) or 1))
    difficulty = max(1, right_level)
    level_gap = max(-20, right_level - left_level)
    duration_bonus = min(30, action_count * 2)
    difficulty_bonus = min(80, difficulty * 2 + max(0, level_gap) * 4)
    value_bonus = min(400, max(0, int(item_value)) // 120)
    if success:
        return max(20, 30 + duration_bonus + difficulty_bonus + value_bonus)
    return max(8, 10 + duration_bonus // 2 + difficulty_bonus // 3)


def sect_level_exp_need(level: int) -> int:
    """宗门升下一级需要的经验。"""

    current = max(1, min(SECT_LEVEL_MAX, int(level)))
    if current >= SECT_LEVEL_MAX:
        return 0
    return int(floor(1500 * (current ** 1.65) + 3600 * current))


def sect_base_bonus(level: int) -> float:
    """宗门等级自带基础增益。"""

    current = max(1, min(SECT_LEVEL_MAX, int(level)))
    return min(SECT_SELF_BONUS_CAP, SECT_SELF_BONUS_CAP * ((current / SECT_LEVEL_MAX) ** 0.82))


def sect_city_carry_rate(level: int) -> float:
    """宗门等级决定能承载多少城池范围增益。"""

    current = max(1, min(SECT_LEVEL_MAX, int(level)))
    return min(1.0, 0.22 + 0.78 * ((current / SECT_LEVEL_MAX) ** 0.72))


def normalize_sect_merit_category(category: str) -> str:
    """把底蕴分类统一为英文内部键。"""

    return SECT_MERIT_ALIASES.get(str(category).strip(), "")


def ensure_sect_stats_conn(conn: sqlite3.Connection, sect_id: int) -> sqlite3.Row | None:
    """确保宗门长期状态存在。"""

    if int(sect_id) <= 0:
        return None
    now_text = ts()
    conn.execute(
        """
        INSERT OR IGNORE INTO sect_stats
        (sect_id, level, exp, influence_merit, support_merit, build_merit, created_at, updated_at)
        SELECT sect_id, 1, 0, 0, 0, 0, ?, ?
        FROM sects
        WHERE sect_id = ?
        """,
        (now_text, now_text, int(sect_id)),
    )
    return conn.execute("SELECT * FROM sect_stats WHERE sect_id = ?", (int(sect_id),)).fetchone()


def sect_stats_conn(conn: sqlite3.Connection, sect_id: int) -> dict[str, Any] | None:
    """读取宗门长期状态，并补充升级所需经验。"""

    row = ensure_sect_stats_conn(conn, int(sect_id))
    if not row:
        return None
    result = dict(row)
    level = max(1, min(SECT_LEVEL_MAX, int(result.get("level") or 1)))
    result["level"] = level
    result["next_exp"] = 0 if level >= SECT_LEVEL_MAX else sect_level_exp_need(level)
    return result


def sect_bonus_conn(conn: sqlite3.Connection, sect_id: int) -> dict[str, Any]:
    """读取宗门等级、城池覆盖和最终宗门增益。"""

    stats = sect_stats_conn(conn, int(sect_id)) or {
        "sect_id": int(sect_id),
        "level": 1,
        "exp": 0,
        "next_exp": sect_level_exp_need(1),
        "influence_merit": 0,
        "support_merit": 0,
        "build_merit": 0,
    }
    level = max(1, min(SECT_LEVEL_MAX, int(stats.get("level") or 1)))
    city = sect_city_bonus_conn(conn, int(sect_id))
    city_bonus = max(0.0, float(city.get("bonus", 0.0) or 0.0))
    base_bonus = sect_base_bonus(level)
    carry_rate = sect_city_carry_rate(level)
    effective_city_bonus = city_bonus * carry_rate
    total_bonus = min(SECT_TOTAL_BONUS_CAP, base_bonus + effective_city_bonus)
    return {
        "stats": stats,
        "level": level,
        "exp": int(stats.get("exp") or 0),
        "next_exp": int(stats.get("next_exp") or 0),
        "influence_merit": int(stats.get("influence_merit") or 0),
        "support_merit": int(stats.get("support_merit") or 0),
        "build_merit": int(stats.get("build_merit") or 0),
        "base_bonus": base_bonus,
        "city_bonus": city_bonus,
        "city_carry_rate": carry_rate,
        "effective_city_bonus": effective_city_bonus,
        "total_bonus": total_bonus,
        "city": city,
    }


def sect_bonus_for_client_conn(conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
    """读取玩家当前宗门增益；未入宗则为 0。"""

    membership = conn.execute(
        "SELECT sect_id FROM sect_members WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not membership:
        return {"sect_id": 0, "total_bonus": 0.0, "base_bonus": 0.0, "city_bonus": 0.0, "city_carry_rate": 0.0}
    result = sect_bonus_conn(conn, int(membership["sect_id"]))
    result["sect_id"] = int(membership["sect_id"])
    return result


def sect_direction_bonus_conn(conn: sqlite3.Connection, client_id: str, category: str) -> float:
    """按底蕴方向取用宗门最终增益。"""

    key = normalize_sect_merit_category(category)
    if not key:
        return 0.0
    bonus = sect_bonus_for_client_conn(conn, client_id)
    sect_id = int(bonus.get("sect_id") or 0)
    if sect_id <= 0:
        return 0.0
    influence = int(bonus.get("influence_merit") or 0)
    support = int(bonus.get("support_merit") or 0)
    build = int(bonus.get("build_merit") or 0)
    total = max(1, influence + support + build)
    category_value = {"influence": influence, "support": support, "build": build}.get(key, 0)
    focus = min(1.0, max(0.0, category_value / total * 3.0))
    return max(0.0, float(bonus.get("total_bonus", 0.0) or 0.0)) * focus


def record_sect_merit_conn(
    conn: sqlite3.Connection,
    client_id: str,
    category: str,
    amount: int,
    *,
    source: str,
    detail: str = "",
    sect_id: int | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """把成员行为沉淀为宗门三底蕴。"""

    key = normalize_sect_merit_category(category)
    value = max(0, int(amount))
    if not key or value <= 0:
        return {"added": 0}
    if sect_id is None:
        membership = conn.execute(
            "SELECT sect_id FROM sect_members WHERE client_id = ?",
            (client_id,),
        ).fetchone()
    else:
        membership = conn.execute(
            "SELECT sect_id FROM sect_members WHERE client_id = ? AND sect_id = ?",
            (client_id, int(sect_id)),
        ).fetchone()
    if not membership:
        return {"added": 0}

    actual_sect_id = int(membership["sect_id"])
    stats = ensure_sect_stats_conn(conn, actual_sect_id)
    if not stats:
        return {"added": 0}

    old_level = max(1, min(SECT_LEVEL_MAX, int(stats["level"] or 1)))
    current_level = old_level
    exp_gain = max(1, int(ceil(value * SECT_MERIT_EXP_WEIGHTS[key])))
    current_exp = 0 if current_level >= SECT_LEVEL_MAX else max(0, int(stats["exp"] or 0)) + exp_gain
    while current_level < SECT_LEVEL_MAX:
        need = sect_level_exp_need(current_level)
        if current_exp < need:
            break
        current_exp -= need
        current_level += 1
    if current_level >= SECT_LEVEL_MAX:
        current_exp = 0

    column = SECT_MERIT_COLUMNS[key]
    now_text = ts(occurred_at or now())
    conn.execute(
        f"""
        UPDATE sect_stats
        SET level = ?,
            exp = ?,
            {column} = {column} + ?,
            updated_at = ?
        WHERE sect_id = ?
        """,
        (current_level, current_exp, value, now_text, actual_sect_id),
    )
    conn.execute(
        """
        INSERT INTO sect_merit_records
        (sect_id, client_id, category, amount, exp_gain, source, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (actual_sect_id, client_id, key, value, exp_gain, source, detail, now_text),
    )
    return {
        "added": value,
        "sect_id": actual_sect_id,
        "category": key,
        "exp_gain": exp_gain,
        "old_level": old_level,
        "level": current_level,
        "leveled": current_level > old_level,
    }


def sect_city_bonus_for_position_conn(conn: sqlite3.Connection, x: int, y: int) -> dict[str, Any]:
    """按宗门山门坐标计算城池范围增益。"""

    city_rows = conn.execute(
        """
        SELECT c.location_name, c.city_level, t.x, t.y
        FROM city_world_states AS c
        JOIN trade_locations AS t ON t.name = c.location_name
        ORDER BY c.location_name
        """
    ).fetchall()
    covers: list[dict[str, Any]] = []
    for row in city_rows:
        level = max(1, int(row["city_level"] or 1))
        distance = hypot(int(x) - int(row["x"]), int(y) - int(row["y"]))
        if distance > level:
            continue
        distance_factor = max(0.0, 1.0 - distance / (level + 1))
        raw_bonus = _sect_city_level_bonus(level) * distance_factor
        covers.append(
            {
                "location_name": str(row["location_name"]),
                "city_level": level,
                "x": int(row["x"]),
                "y": int(row["y"]),
                "distance": distance,
                "radius": level,
                "distance_factor": distance_factor,
                "strength": level * distance_factor,
                "raw_bonus": raw_bonus,
            }
        )

    covers.sort(key=lambda item: (-float(item["strength"]), -float(item["raw_bonus"]), str(item["location_name"])))
    total_bonus = 0.0
    applied: list[dict[str, Any]] = []
    for index, cover in enumerate(covers[: len(SECT_CITY_SYNERGY_FACTORS)]):
        factor = SECT_CITY_SYNERGY_FACTORS[index]
        applied_bonus = float(cover["raw_bonus"]) * factor
        item = dict(cover)
        item["synergy_factor"] = factor
        item["applied_bonus"] = applied_bonus
        item["role"] = "主影响" if index == 0 else "协同"
        applied.append(item)
        total_bonus += applied_bonus

    total_bonus = min(SECT_CITY_BONUS_HARD_CAP, total_bonus)
    return {
        "bonus": total_bonus,
        "covers": applied,
        "all_cover_count": len(covers),
    }


def sect_city_bonus_conn(conn: sqlite3.Connection, sect_id: int) -> dict[str, Any]:
    """读取宗门山门并计算城池范围增益。"""

    sect = conn.execute(
        "SELECT location_x, location_y FROM sects WHERE sect_id = ?",
        (int(sect_id),),
    ).fetchone()
    if not sect:
        return {"bonus": 0.0, "covers": [], "all_cover_count": 0}
    return sect_city_bonus_for_position_conn(conn, int(sect["location_x"]), int(sect["location_y"]))


def _sect_city_level_bonus(level: int) -> float:
    """城池等级转为可提供的宗门增益上限，按等级段平滑增长。"""

    current = max(1, min(107, int(level)))
    if current <= 20:
        return 0.10 * current / 20
    if current <= 50:
        return 0.10 + (current - 20) / 30 * 0.15
    if current <= 80:
        return 0.25 + (current - 50) / 30 * 0.15
    return 0.40 + (current - 80) / 27 * 0.10


def _apply_sect_city_bonus_conn(conn: sqlite3.Connection, sect_id: int, influence: int) -> tuple[int, dict[str, Any]]:
    """把宗门最终增益应用到本次宗门影响力。"""

    bonus = sect_bonus_conn(conn, sect_id)
    rate = max(0.0, float(bonus.get("total_bonus", 0.0) or 0.0))
    if rate <= 0 or influence <= 0:
        return influence, bonus
    boosted = max(int(influence), ceil(int(influence) * (1.0 + rate)))
    return boosted, bonus


def _sect_city_bonus_detail(bonus: dict[str, Any], base_influence: int, final_influence: int) -> str:
    """生成宗门战影响力流水里的宗门增益摘要。"""

    rate = max(0.0, float(bonus.get("total_bonus", 0.0) or 0.0))
    if rate <= 0 or final_influence <= base_influence:
        return ""
    city = bonus.get("city") if isinstance(bonus.get("city"), dict) else {}
    covers = city.get("covers") if isinstance(city.get("covers"), list) else []
    main = covers[0] if covers else {}
    city_name = str(main.get("location_name", "")) if isinstance(main, dict) else ""
    return (
        f"sect_bonus={rate:.4f}, base_bonus={float(bonus.get('base_bonus', 0.0) or 0.0):.4f}, "
        f"city_bonus={float(bonus.get('city_bonus', 0.0) or 0.0):.4f}, "
        f"carry={float(bonus.get('city_carry_rate', 0.0) or 0.0):.4f}, "
        f"city={city_name}, base={base_influence}, final={final_influence}"
    )


def record_sect_robbery_influence_conn(
    conn: sqlite3.Connection,
    client_id: str,
    *,
    sect_id: int,
    success: bool,
    item_value: int,
    battle: dict,
    detail: str = "",
    occurred_at: datetime | None = None,
) -> int:
    """把抢劫产生的宗门影响力记到抢劫者当时所属宗门。"""

    occurred_time = occurred_at or now()
    if not sect_war_in_battle_window(occurred_time):
        return 0
    if sect_id <= 0:
        return 0
    membership = conn.execute(
        "SELECT 1 FROM sect_members WHERE client_id = ? AND sect_id = ? LIMIT 1",
        (client_id, int(sect_id)),
    ).fetchone()
    if not membership:
        return 0

    influence = sect_war_robbery_influence(success=success, item_value=item_value, battle=battle)
    if influence <= 0:
        return 0
    base_influence = influence
    influence, city_bonus = _apply_sect_city_bonus_conn(conn, sect_id, influence)
    bonus_detail = _sect_city_bonus_detail(city_bonus, base_influence, influence)
    if bonus_detail:
        detail = f"{detail}; {bonus_detail}" if detail else bonus_detail
    record_sect_merit_conn(
        conn,
        client_id,
        "influence",
        influence,
        source="宗门战抢劫",
        detail=detail,
        sect_id=sect_id,
        occurred_at=occurred_time,
    )
    cycle_start, cycle_end = sect_war_cycle_bounds(occurred_time)
    conn.execute(
        """
        INSERT INTO sect_influence_records
        (sect_id, client_id, action, influence, item_value, success, cycle_start, cycle_end, detail, created_at)
        VALUES (?, ?, '抢劫', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sect_id, cycle_start) DO UPDATE SET
            client_id = excluded.client_id,
            action = excluded.action,
            influence = sect_influence_records.influence + excluded.influence,
            item_value = sect_influence_records.item_value + excluded.item_value,
            success = sect_influence_records.success + excluded.success,
            cycle_end = excluded.cycle_end,
            detail = excluded.detail,
            created_at = excluded.created_at
        """,
        (
            int(sect_id),
            client_id,
            influence,
            max(0, int(item_value)),
            1 if success else 0,
            cycle_start,
            cycle_end,
            detail,
            ts(occurred_time),
        ),
    )
    conn.execute(
        """
        INSERT INTO sect_contribution_records
        (sect_id, client_id, influence, item_value, success, cycle_start, cycle_end, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sect_id, client_id, cycle_start) DO UPDATE SET
            influence = sect_contribution_records.influence + excluded.influence,
            item_value = sect_contribution_records.item_value + excluded.item_value,
            success = sect_contribution_records.success + excluded.success,
            cycle_end = excluded.cycle_end,
            detail = excluded.detail,
            created_at = excluded.created_at
        """,
        (
            int(sect_id),
            client_id,
            influence,
            max(0, int(item_value)),
            1 if success else 0,
            cycle_start,
            cycle_end,
            detail,
            ts(occurred_time),
        ),
    )
    return influence
