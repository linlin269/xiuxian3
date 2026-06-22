"""修仙 WS 命令压力测试。

运行方式：

    python test/修仙_命令压力测试.py

测试使用临时 SQLite，不写入真实 xiuxian.db。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from launch.adapter.ws import WsMessageHandler
from 修仙.common import business_day, dump_json, now, ts
from 修仙.sql import XiuxianDB
from 修仙.weapon_core import WeaponCore
from 修仙.首领.service import BOSS_DEFS
from 修仙_ws触发测试 import FakeManager, _dispatch, _patch_modules, _restore_modules


PRESSURE_ROUNDS = 3


async def main_async() -> None:
    """覆盖所有修仙 WS 命令，并做多轮快速触发。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_pressure_test.db")
        manager = FakeManager()
        old_state = _patch_modules(db, manager)
        try:
            await WsMessageHandler.run()
            await _prepare_players(db, manager)
            weapon_id = _prepare_resources(db)
            cases = _command_cases(weapon_id)
            _assert_all_commands_covered(cases)

            for client_id, message in cases:
                await _send(db, manager, client_id, message)

            for _ in range(PRESSURE_ROUNDS):
                for client_id, message in cases:
                    await _send(db, manager, client_id, message)
        finally:
            _restore_modules(old_state)
            db.close()

    print(f"修仙命令压力测试通过：{len(_command_cases(1))} 个命令，多轮 {PRESSURE_ROUNDS} 次")


async def _prepare_players(db: XiuxianDB, manager: FakeManager) -> None:
    """先创建压力测试玩家。"""

    await _send(db, manager, "stress_a", "创建用户 青衫客")
    await _send(db, manager, "stress_b", "创建用户 白衣客")


def _prepare_resources(db: XiuxianDB) -> int:
    """准备各命令需要的基础资源。"""

    weapon_core = WeaponCore(db)
    weapon_core.ensure_starter_weapon("stress_a")
    weapon = db.fetch_one("SELECT weapon_id FROM player_weapons WHERE holder_id = ? LIMIT 1", ("stress_a",))
    weapon_id = int(weapon["weapon_id"]) if weapon else 1

    with db.transaction() as conn:
        for client_id in ("stress_a", "stress_b"):
            conn.execute(
                "UPDATE players SET source_stones = source_stones + 5000000 WHERE client_id = ?",
                (client_id,),
            )
        conn.execute(
            "UPDATE player_weapons SET level = 90, max_level = 100 WHERE holder_id = ?",
            ("stress_a",),
        )
        _add_common_items(conn, "stress_a")
        _add_common_items(conn, "stress_b")
    return weapon_id


def _add_common_items(conn, client_id: str) -> None:
    """补足压力测试里会消耗的物品。"""

    conn.execute(
        """
        INSERT INTO backpack_items (client_id, item_id, quantity)
        VALUES (?, 'loot_yao_2', 200)
        ON CONFLICT(client_id, item_id)
        DO UPDATE SET quantity = quantity + 200
        """,
        (client_id,),
    )
    conn.execute(
        """
        INSERT INTO backpack_items (client_id, item_id, quantity)
        VALUES (?, 'loot_yao_1', 200)
        ON CONFLICT(client_id, item_id)
        DO UPDATE SET quantity = quantity + 200
        """,
        (client_id,),
    )
    for item_id in ("world_med_xueqidan_1", "world_life_chengshi_1", "world_build_jichu_1", "world_relic_weiyun_1"):
        conn.execute(
            """
            INSERT INTO backpack_items (client_id, item_id, quantity)
            VALUES (?, ?, 200)
            ON CONFLICT(client_id, item_id)
            DO UPDATE SET quantity = quantity + 200
            """,
            (client_id, item_id),
        )
    for item_id in ("fudai", "xueqidan", "kaikongqi", "xisuiye", "fengren_shu", "cuifengdan"):
        conn.execute(
            """
            INSERT INTO ring_items (client_id, ring_item_id, quantity)
            VALUES (?, ?, 30)
            ON CONFLICT(client_id, ring_item_id)
            DO UPDATE SET quantity = quantity + 30
            """,
            (client_id, item_id),
        )
    _add_feathers_conn(conn, client_id, 30)
    conn.execute(
        """
        INSERT INTO gem_items (client_id, gem_id, level, quantity)
        VALUES (?, 'huxinyu', 1, 30)
        ON CONFLICT(client_id, gem_id, level)
        DO UPDATE SET quantity = quantity + 30
        """,
        (client_id,),
    )


def _add_feathers_conn(conn, client_id: str, quantity: int) -> None:
    """补充带文案的铭刻之羽实例。"""

    for index in range(quantity):
        conn.execute(
            """
            INSERT INTO inscription_feathers
            (client_id, source_key, source_name, title, flavor_text, obtained_at)
            VALUES (?, 'test', '压力测试岁时情劫', ?, ?, '2000-01-01T00:00:00')
            """,
            (client_id, f"压力测试遗羽{index + 1}", f"这是一枚用于压力测试的铭刻之羽文案 {index + 1}。"),
        )


def _command_cases(weapon_id: int) -> list[tuple[str, str]]:
    """当前修仙模块所有 WS 命令。"""

    return [
        ("stress_a", "帮助"),
        ("stress_a", "修仙帮助"),
        ("stress_a", "修仙百科 宗门战奖励机制"),
        ("stress_a", "指南"),
        ("stress_a", "创建用户 青衫客"),
        ("stress_a", "改名 云游客"),
        ("stress_a", "修仙信息"),
        ("stress_a", "状态"),
        ("stress_a", "宗门"),
        ("stress_a", "建立宗门 -49 -49 青云宗"),
        ("stress_b", "加入宗门 青云宗"),
        ("stress_a", "宗门战"),
        ("stress_a", "领取宗门战奖励"),
        ("stress_b", "退出宗门"),
        ("stress_a", "修仙日记"),
        ("stress_a", "自动用药 开启"),
        ("stress_a", "战斗日志 开启"),
        ("stress_a", "签到"),
        ("stress_a", "新手礼包"),
        ("stress_a", "休息"),
        ("stress_a", "结束休息"),
        ("stress_a", "休息结束"),
        ("stress_a", "源库"),
        ("stress_a", "源库结息"),
        ("stress_a", "升级源库"),
        ("stress_a", "源库升级"),
        ("stress_a", "存入源石 100"),
        ("stress_a", "源石存入 100"),
        ("stress_a", "取出源石 50"),
        ("stress_a", "源石取出 50"),
        ("stress_a", "纳戒"),
        ("stress_a", "背包"),
        ("stress_a", "保险箱"),
        ("stress_a", "查看保险箱"),
        ("stress_a", "存入保险箱 血契丹 1"),
        ("stress_a", "取出保险箱 血契丹 1"),
        ("stress_a", "存保险箱 血契丹 1"),
        ("stress_a", "取保险箱 血契丹 1"),
        ("stress_a", "放入保险箱 血契丹 1"),
        ("stress_a", "取出保险箱 血契丹 1"),
        ("stress_a", "查看修仙物品 福袋"),
        ("stress_a", "查看修仙物品 星官旧简"),
        ("stress_a", "修仙物品查看 福袋"),
        ("stress_a", "查看 星官旧简"),
        ("stress_a", "使用 血契丹"),
        ("stress_a", "使用 洗髓液"),
        ("stress_a", "使用 风刃书"),
        ("stress_a", "洗髓"),
        ("stress_a", "二手市场"),
        ("stress_a", "小黄鱼"),
        ("stress_a", "二手市场上架 妖脊骨 1 300"),
        ("stress_a", "二手市场下架"),
        ("stress_a", "小黄鱼上架 妖脊骨 1 300"),
        ("stress_a", "小黄鱼下架"),
        ("stress_a", "二手市场上架 血契丹 1 500"),
        ("stress_b", "二手市场购买 云游客"),
        ("stress_a", "小黄鱼上架 血契丹 1 500"),
        ("stress_b", "小黄鱼购买 云游客"),
        ("stress_a", "商场行情 星官旧简"),
        ("stress_a", "商场购买 星官旧简 1"),
        ("stress_a", "商场出售 星官旧简 1"),
        ("stress_a", "自动出售"),
        ("stress_a", "出售 古妖丹 1"),
        ("stress_a", "出售全部 武器"),
        ("stress_a", "出售全部 宝石"),
        ("stress_a", "出售全部 技能书"),
        ("stress_a", "商场推荐"),
        ("stress_a", "跑商记录"),
        ("stress_a", "跑商限制"),
        ("stress_a", "跑商奖励"),
        ("stress_a", "虫洞"),
        ("stress_a", "虫洞状态"),
        ("stress_a", "挑战虫洞"),
        ("stress_a", "虫洞排行"),
        ("stress_a", "虫洞奖励"),
        ("stress_a", "首领"),
        ("stress_a", "首领状态"),
        ("stress_a", "挑战首领"),
        ("stress_a", "首领排行"),
        ("stress_a", "首领奖励"),
        ("stress_a", "岁时情劫"),
        ("stress_a", "岁时情劫状态"),
        ("stress_a", "挑战岁时情劫"),
        ("stress_a", "岁时情劫排行"),
        ("stress_a", "岁时情劫奖励"),
        ("stress_a", "导航 镇妖司"),
        ("stress_a", "去 天枢城"),
        ("stress_a", "来 青岚坊"),
        ("stress_a", "藏宝图"),
        ("stress_a", "藏宝图出价 20000"),
        ("stress_a", "领取藏宝图"),
        ("stress_a", "位置"),
        ("stress_a", "地图"),
        ("stress_a", "探险列表"),
        ("stress_a", "探险"),
        ("stress_a", "探险状态"),
        ("stress_a", "结束探险"),
        ("stress_a", "探险结束"),
        ("stress_a", "探险记录"),
        ("stress_a", "武器"),
        ("stress_a", f"查看武器 {weapon_id}"),
        ("stress_a", f"武器传奇 {weapon_id}"),
        ("stress_a", f"切换武器 {weapon_id}"),
        ("stress_a", f"升级武器 {weapon_id}"),
        ("stress_a", "武器淬锋"),
        ("stress_a", f"附魔武器 {weapon_id} 风刃书"),
        ("stress_a", "铭刻"),
        ("stress_a", "铭刻之羽"),
        ("stress_a", "铭刻装备 头部 青云冠"),
        ("stress_a", f"铭刻武器 {weapon_id} 青云剑"),
        ("stress_a", f"铭刻附魔 {weapon_id} 1 青云破"),
        ("stress_a", f"铭刻技能 {weapon_id} 青云斩"),
        ("stress_a", "装备"),
        ("stress_a", "装备升级 头部"),
        ("stress_a", "升 左手"),
        ("stress_a", "孔位 头部"),
        ("stress_a", "宝石"),
        ("stress_a", "开孔 头部"),
        ("stress_a", "镶嵌 头部 1 护心玉"),
        ("stress_a", "宝石升级 头部 1"),
        ("stress_a", "拆卸 头部 1"),
        ("stress_a", "切磋 白衣客"),
        ("stress_b", "拒绝切磋 云游客"),
        ("stress_a", "切磋 白衣客"),
        ("stress_b", "接受切磋 云游客"),
        ("stress_a", "决斗 白衣客"),
        ("stress_a", "决斗 100 白衣客"),
        ("stress_b", "拒绝决斗 云游客"),
        ("stress_a", "决斗 100 白衣客"),
        ("stress_b", "接受决斗 云游客"),
        ("stress_a", "抢劫 白衣客"),
        ("stress_a", "决斗记录"),
        ("stress_a", "风云榜"),
        ("stress_a", "修仙早报"),
        ("stress_a", "修仙界历史"),
        ("stress_a", "人物史榜"),
        ("stress_a", "宗门史榜"),
        ("stress_a", "城池史榜"),
        ("stress_a", "战斗名局"),
        ("stress_a", "商路奇闻"),
        ("stress_a", "异界虫洞录"),
        ("stress_a", "人物志 云游客"),
    ]


def _assert_all_commands_covered(cases: list[tuple[str, str]]) -> None:
    """确认当前 修仙 注册的命令都在压力测试里。"""

    tested = {message.partition(" ")[0] for _client_id, message in cases}
    registered = {
        cmd
        for cmd, rules in WsMessageHandler.func_dict.items()
        if any(getattr(rule.func, "__module__", "").startswith("修仙") for rule in rules)
    }
    missing = sorted(registered - tested)
    assert not missing, "压力测试缺少命令：" + "、".join(missing)


async def _send(db: XiuxianDB, manager: FakeManager, client_id: str, message: str) -> None:
    """发送一条命令，并确认一定有业务回复。"""

    _prepare_before_send(db, client_id, message)
    await _dispatch(manager, client_id, message)
    assert manager.sent, f"命令没有回复：{client_id}/{message}"
    reply = str(manager.sent[-1][1])
    assert "未命中任何触发器" not in reply, f"命令未命中：{client_id}/{message}"


def _prepare_before_send(db: XiuxianDB, client_id: str, message: str) -> None:
    """让压力测试尽量走到真实业务路径。"""

    command = message.partition(" ")[0]
    with db.transaction() as conn:
        if message in {"休息", "探险"} or message.startswith(("切磋 ", "决斗 ", "抢劫 ")):
            conn.execute("UPDATE players SET status = '空闲', rest_full_at = NULL WHERE client_id IN ('stress_a', 'stress_b')")
        if command == "建立宗门":
            rows = conn.execute(
                "SELECT sect_id FROM sects WHERE master_client_id = ? OR (location_x = -49 AND location_y = -49)",
                (client_id,),
            ).fetchall()
            sect_ids = [int(row["sect_id"]) for row in rows]
            for sect_id in sect_ids:
                conn.execute("DELETE FROM sect_members WHERE sect_id = ?", (sect_id,))
            conn.execute("DELETE FROM sects WHERE master_client_id = ? OR (location_x = -49 AND location_y = -49)", (client_id,))
        if command == "加入宗门":
            sect = conn.execute("SELECT sect_id FROM sects WHERE name = '青云宗'").fetchone()
            if not sect:
                cursor = conn.execute(
                    """
                    INSERT INTO sects
                    (name, location_name, location_x, location_y, founder_id, master_client_id, created_at)
                    VALUES ('青云宗', '青云宗山门', -49, -49, 'stress_a', 'stress_a', ?)
                    """,
                    (ts(),),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO sect_members (client_id, sect_id, role, joined_at) VALUES ('stress_a', ?, '宗主', ?)",
                    (int(cursor.lastrowid), ts()),
                )
            conn.execute("DELETE FROM sect_members WHERE client_id = ? AND role != '宗主'", (client_id,))
            conn.execute(
                "UPDATE players SET location_name = '青云宗山门', x = -49, y = -49 WHERE client_id = ?",
                (client_id,),
            )
        if message in {"结束休息", "休息结束"}:
            conn.execute(
                "UPDATE players SET status = '休息中', rest_full_at = '2000-01-01T00:00:00' WHERE client_id = ?",
                (client_id,),
            )
        trade_commands = {
            "商场行情",
            "商场购买",
            "商场出售",
            "自动出售",
            "出售",
            "出售全部",
            "商场推荐",
            "跑商记录",
            "跑商限制",
            "跑商奖励",
            "导航",
            "去",
            "来",
        }
        exploration_commands = {
            "探险",
            "探险状态",
            "结束探险",
            "探险结束",
            "探险列表",
            "探险记录",
            "位置",
        }
        wormhole_commands = {
            "虫洞",
            "虫洞状态",
            "挑战虫洞",
            "虫洞排行",
            "虫洞奖励",
        }
        seasonal_boss_commands = {
            "首领",
            "首领状态",
            "挑战首领",
            "首领排行",
            "首领奖励",
            "岁时情劫",
            "岁时情劫状态",
            "挑战岁时情劫",
            "岁时情劫排行",
            "岁时情劫奖励",
        }
        if command in trade_commands or command in exploration_commands:
            conn.execute(
                "UPDATE players SET location_name = '天枢城', x = 0, y = 0 WHERE client_id = ?",
                (client_id,),
            )
        if command in wormhole_commands:
            conn.execute(
                "UPDATE players SET location_name = '天枢城', x = 0, y = 0, status = '空闲', hp = max_hp, mp = max_mp WHERE client_id = ?",
                (client_id,),
            )
            active = conn.execute("SELECT wormhole_id FROM wormholes WHERE status = '开启' LIMIT 1").fetchone()
            if not active:
                conn.execute(
                    """
                    INSERT INTO wormholes (
                        boss_name, boss_kind, location_name, x, y, level, max_hp, hp,
                        attack, defense, difficulty, opened_by, source, status, opened_at, closes_at
                    )
                    VALUES ('压力测试魔像', '测试', '天枢城', 0, 0, 10, 5000, 5000, 25, 5, 1.0, ?, 'test', '开启',
                            '2000-01-01T00:00:00', '2999-01-01T00:00:00')
                    """,
                    (client_id,),
                )
        if command in seasonal_boss_commands:
            conn.execute(
                "UPDATE players SET status = '空闲', hp = max_hp, mp = max_mp WHERE client_id = ?",
                (client_id,),
            )
            _ensure_seasonal_boss_conn(conn)
        if command in {"结束探险", "探险结束"}:
            conn.execute(
                "UPDATE exploration_records SET ready_at = '2000-01-01T00:00:00' WHERE client_id = ? AND claimed = 0",
                (client_id,),
            )
        if command in {"二手市场上架", "小黄鱼上架"}:
            _add_common_items(conn, client_id)
        if command in {"二手市场购买", "小黄鱼购买"}:
            _add_common_items(conn, "stress_a")
        if command in {"出售", "自动出售"}:
            conn.execute(
                "UPDATE players SET location_name = '镇妖司', x = 40, y = 40 WHERE client_id = ?",
                (client_id,),
            )
            _add_common_items(conn, client_id)
        if command in {"藏宝图", "藏宝图出价", "领取藏宝图"}:
            conn.execute(
                "UPDATE players SET location_name = '天枢城', x = 0, y = 0, status = '空闲', hp = max_hp, mp = max_mp WHERE client_id = ?",
                (client_id,),
            )
            _add_common_items(conn, client_id)
        if command in {"藏宝图出价", "领取藏宝图"}:
            _ensure_treasure_map_conn(conn)
        if command in {"使用", "洗髓", "存入保险箱", "存保险箱", "放入保险箱"}:
            _add_common_items(conn, client_id)
        if command in {"镶嵌", "附魔武器"} or command.startswith("铭刻"):
            _add_common_items(conn, client_id)
        if command == "附魔武器":
            conn.execute(
                "UPDATE player_weapons SET level = 90, max_level = 100 WHERE holder_id = ?",
                (client_id,),
            )
        if command in {"铭刻附魔", "铭刻技能"}:
            conn.execute(
                "UPDATE player_weapons SET level = 90, max_level = 100, enchant_effects = '[\"fengren_shu\"]' WHERE holder_id = ?",
                (client_id,),
            )
        if command in {
            "装备升级",
            "升",
            "宝石升级",
            "升级武器",
            "升级源库",
            "源库升级",
            "存入源石",
            "源石存入",
            "决斗",
            "源石取出",
        }:
            conn.execute(
                "UPDATE players SET source_stones = source_stones + 5000000 WHERE client_id = ?",
                (client_id,),
            )
        if command in {"切磋", "决斗"}:
            conn.execute("UPDATE duel_requests SET status = '已拒绝' WHERE status = '等待'")


def _ensure_seasonal_boss_conn(conn) -> None:
    """准备一个可挑战的岁时情劫。"""

    day = business_day()
    active = conn.execute("SELECT event_id FROM seasonal_boss_events WHERE business_day = ? LIMIT 1", (day,)).fetchone()
    if active:
        return

    boss = BOSS_DEFS["lichun"]
    opened_at = now()
    conn.execute(
        """
        INSERT INTO seasonal_boss_events (
            business_day, boss_key, event_type, weight_type, boss_name, title,
            scene, story, farewell, feather_text, location_name, atmosphere,
            level, max_hp, hp, attack, defense, difficulty,
            status, opened_at, closes_at
        )
        VALUES (?, ?, '二十四节气', '普通节气', ?, ?, ?, ?, ?, ?, ?, ?,
                5, 500, 1, 20, 5, 1.0, '开启', ?, ?)
        """,
        (
            day,
            boss.key,
            boss.name,
            boss.title,
            boss.scene,
            boss.story,
            boss.farewell,
            boss.feather_text,
            boss.location,
            dump_json(list(boss.atmosphere)),
            ts(opened_at),
            ts(opened_at + timedelta(days=1)),
        ),
    )


def _ensure_treasure_map_conn(conn) -> None:
    """准备一张可竞拍的藏宝图。"""

    active = conn.execute(
        "SELECT map_id FROM treasure_maps WHERE city_name = '天枢城' AND status = '拍卖中' LIMIT 1"
    ).fetchone()
    if active:
        return

    conn.execute(
        """
        INSERT INTO treasure_maps (
            city_name, status, current_price, weapon_def_id, weapon_name,
            weapon_max_level, generated_at, expires_at, result
        )
        VALUES ('天枢城', '拍卖中', 10000, 'liuguang_xijian', '流光细剑',
                65, ?, ?, '{}')
        """,
        (ts(now()), ts(now() + timedelta(days=1))),
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
