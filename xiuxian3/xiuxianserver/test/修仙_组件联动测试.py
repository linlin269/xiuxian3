"""修仙组件联动测试。

运行方式：

    python test/修仙_组件联动测试.py
"""

from __future__ import annotations

import sys
from math import sqrt
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from 修仙.common import load_json
from 修仙.sql import XiuxianDB
from 修仙.wormhole_service import BOSS_FLOW_TEXT, BOSS_POOL, WAR_PREP_REWARD_PROFILES, WormholeService
from 修仙.首领.service import SeasonalBossService
import 修仙.wormhole_service as wormhole_module
from 修仙.world_materials import WorldMaterialService

boss_module = import_module("修仙.首领.service")


def main() -> None:
    """验证文档承诺的跨组件联动已经进入真实业务。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_linkage_test.db")
        try:
            _check_wormhole_metadata_and_war_prep_rewards(db)
            _check_treasure_map_city_radius(db)
            _check_sect_public_battle_bonus(db)
            _check_seasonal_boss_feather_rate(db)
        finally:
            db.close()

    print("修仙组件联动测试通过")


def _check_wormhole_metadata_and_war_prep_rewards(db: XiuxianDB) -> None:
    """普通虫洞要有结构化类型；战备奖励要真的按势力倾向抽池。"""

    wormhole = WormholeService(db)
    expected_flows = {"高频连击", "重击破防", "持续伤害", "压制控制", "生存续航", "反击护身", "斩杀收割", "首领协作"}
    assert {BOSS_FLOW_TEXT[name] for name, _kind, _factor in BOSS_POOL} == expected_flows
    event = wormhole._open_event("tester", "test", "天枢城")
    meta = load_json(event["result"], {})
    assert meta.get("event_type") == "normal"
    assert meta.get("boss_flow") in expected_flows

    force = "伏魔殿"
    book = wormhole._random_war_prep_book({"event_type": "war_prep", "force": force})
    assert book is not None
    enchant_id = load_json(book["effect"], {}).get("enchant_id")
    enchant = db.fetch_one("SELECT effect FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
    assert enchant is not None
    effect_keys = set(load_json(enchant["effect"], {}).keys())
    assert effect_keys.intersection(WAR_PREP_REWARD_PROFILES[force]["book_effects"])

    old_random = wormhole_module.random.random
    old_max_level = wormhole.weapon_core.random_max_level
    try:
        wormhole_module.random.random = lambda: 0.0
        wormhole.weapon_core.random_max_level = lambda: 20  # type: ignore[method-assign]
        weapon = wormhole._random_war_prep_weapon({"event_type": "war_prep", "force": "龙渊阁"})
    finally:
        wormhole_module.random.random = old_random
        wormhole.weapon_core.random_max_level = old_max_level  # type: ignore[method-assign]
    weapon_def = db.fetch_one("SELECT weapon_type FROM weapon_defs WHERE weapon_def_id = ?", (weapon["weapon_def_id"],))
    assert weapon_def is not None
    assert weapon_def["weapon_type"] in WAR_PREP_REWARD_PROFILES["龙渊阁"]["weapon_types"]
    assert int(weapon["max_level"]) >= WAR_PREP_REWARD_PROFILES["龙渊阁"]["max_level_floor"]

    _check_war_prep_reward_text(db, wormhole)


def _check_war_prep_reward_text(db: XiuxianDB, wormhole: WormholeService) -> None:
    """战备虫洞领奖必须展示来源、词条、倍率和定向奖励。"""

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO players (client_id, display_name, level, hp, max_hp, mp, max_mp, source_stones, created_at)
            VALUES ('worm_reward_tester', '虫洞领奖测', 10, 100, 100, 60, 60, 0, '2000-01-01T00:00:00')
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO wormholes (
                boss_name, boss_kind, location_name, x, y,
                level, max_hp, hp, attack, defense, difficulty,
                opened_by, source, status, opened_at, closes_at, killed_at, result
            )
            VALUES (
                '黑铠破界魔', '魔铠', '伏魔殿', -31, 21,
                12, 1000, 0, 50, 10, 1.2,
                'system', 'war_prep', '已击杀', '2000-01-01T00:00:00', '2999-01-01T00:00:00', '2000-01-01T00:05:00', ?
            )
            """,
            (
                '{"event_type":"war_prep","force":"伏魔殿","war_prep_name":"伏魔战备","loot_subtype":"魔类","affixes":["余烬未冷"],"boss_flow":"重击破防","reward_multiplier":1.2,"reward_tendency":"魔类战利品、破防或镇魔技能书","war_prep_cost":900}',
            ),
        )
        wormhole_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO wormhole_participants
            (wormhole_id, client_id, damage, challenge_count, last_challenge_at, reward_claimed, created_at, updated_at)
            VALUES (?, 'worm_reward_tester', 500, 1, '2000-01-01T00:01:00', 0, '2000-01-01T00:01:00', '2000-01-01T00:01:00')
            """,
            (wormhole_id,),
        )

    reward_text = wormhole.reward("worm_reward_tester")
    assert "战备来源：伏魔战备" in reward_text
    assert "势力：伏魔殿" in reward_text
    assert "词条：余烬未冷" in reward_text
    assert "奖励倍率：1.20x" in reward_text
    assert "异界法则：重击破防" in reward_text
    assert "定向奖励：魔类战利品、破防或镇魔技能书" in reward_text


def _check_treasure_map_city_radius(db: XiuxianDB) -> None:
    """无人竞拍藏宝图应优先落在所属城池影响半径内的荒地。"""

    world = WorldMaterialService(db)
    with db.transaction() as conn:
        conn.execute("UPDATE city_world_states SET city_level = 8 WHERE location_name = '天枢城'")
        conn.execute(
            """
            INSERT INTO treasure_maps
            (city_name, status, current_price, weapon_def_id, weapon_name, weapon_max_level, generated_at, expires_at, result)
            VALUES ('天枢城', '拍卖中', 12000, 'qinglan_duanjian', '青岚短剑', 70, '2000-01-01T00:00:00', '2000-01-01T00:00:00', '{}')
            """
        )
        world._settle_treasure_maps_conn(conn, "天枢城")
        row = conn.execute("SELECT * FROM treasure_maps WHERE city_name = '天枢城' ORDER BY map_id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row["status"] == "可拾取"
        result = load_json(row["result"], {})
        assert result.get("near_city") is True
        point = world._city_point("天枢城")
        assert point is not None
        assert world._distance(point["x"], point["y"], int(row["x"]), int(row["y"])) <= 8
        occupied = conn.execute("SELECT 1 FROM world_locations WHERE x = ? AND y = ?", (row["x"], row["y"])).fetchone()
        assert occupied is None

        conn.execute(
            """
            INSERT INTO players (client_id, display_name, location_name, x, y, hp, mp, created_at)
            VALUES ('sect_master', '宗主测', '天枢城', 0, 0, 100, 60, '2000-01-01T00:00:00')
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO sects (name, location_name, location_x, location_y, founder_id, master_client_id, created_at)
            VALUES ('测试宗', '宗门·测试宗', 9, 9, 'sect_master', 'sect_master', '2000-01-01T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO sect_members (client_id, sect_id, role, joined_at)
            VALUES ('sect_master', ?, '宗主', '2000-01-01T00:00:00')
            """,
            (cursor.lastrowid,),
        )
        conn.execute("UPDATE city_world_states SET city_level = 0 WHERE location_name = '天枢城'")
        conn.execute(
            """
            INSERT INTO treasure_maps
            (city_name, status, current_price, weapon_def_id, weapon_name, weapon_max_level, generated_at, expires_at, result)
            VALUES ('天枢城', '拍卖中', 12000, 'qinglan_duanjian', '青岚短剑', 70, '2000-01-01T00:00:00', '2000-01-01T00:00:00', '{}')
            """
        )
        for x in range(-100, 101):
            for y in range(-100, 101):
                conn.execute(
                    "INSERT OR IGNORE INTO world_locations (name, x, y, category, terrain, desc) VALUES (?, ?, ?, '测试占位', '测试', '')",
                    (f"测试占位{x}_{y}", x, y),
                )
        world._settle_treasure_maps_conn(conn, "天枢城")
        sect_row = conn.execute("SELECT * FROM treasure_maps WHERE city_name = '天枢城' ORDER BY map_id DESC LIMIT 1").fetchone()
        assert sect_row is not None
        assert sect_row["status"] == "宗主待领"
        assert sect_row["owner_client_id"] == "sect_master"


def _check_sect_public_battle_bonus(db: XiuxianDB) -> None:
    """宗门影响力要实际修正公共战斗珍贵掉落，而不是只写展示。"""

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO players (client_id, display_name, hp, mp, created_at)
            VALUES ('bonus_player', '加成测', 100, 60, '2000-01-01T00:00:00')
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO sects (name, location_name, location_x, location_y, founder_id, master_client_id, created_at)
            VALUES ('加成宗', '宗门·加成宗', 50, 50, 'bonus_player', 'bonus_player', '2000-01-01T00:00:00')
            """
        )
        sect_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO sect_members (client_id, sect_id, role, joined_at) VALUES ('bonus_player', ?, '宗主', '2000-01-01T00:00:00')",
            (sect_id,),
        )
        conn.execute(
            """
            INSERT INTO sect_stats
            (sect_id, level, exp, influence_merit, support_merit, build_merit, created_at, updated_at)
            VALUES (?, 100, 0, 100000, 0, 0, '2000-01-01T00:00:00', '2000-01-01T00:00:00')
            """,
            (sect_id,),
        )
    wormhole = WormholeService(db)
    boss = SeasonalBossService(db)
    assert wormhole._public_battle_reward_bonus("bonus_player") > 0
    assert boss._public_battle_reward_bonus("bonus_player") > 0

    old_random = boss_module.random.random
    try:
        boss_module.random.random = lambda: 0.93
        without_bonus = boss._good_loot_rolls(0.8, 4, 0.0)
        with_bonus = boss._good_loot_rolls(0.8, 4, 0.08)
    finally:
        boss_module.random.random = old_random
    assert without_bonus == 1
    assert with_bonus > without_bonus


def _check_seasonal_boss_feather_rate(db: XiuxianDB) -> None:
    """铭刻之羽按独立概率判定，不再混进珍贵池权重。"""

    boss = SeasonalBossService(db)
    rates = boss._reward_rates("每日旧愿")
    chance = boss._feather_chance("每日旧愿", sqrt(0.22), 2, rates, 0.0)
    assert 0.04 < chance < 0.05
    assert boss._roll_good_loot("每日旧愿", sqrt(0.22), 2, rates, allow_feather=False)["kind"] != "feather"


if __name__ == "__main__":
    main()
