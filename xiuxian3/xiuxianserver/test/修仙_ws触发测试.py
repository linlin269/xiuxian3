"""修仙 WS 触发测试。

运行方式：

    python test/修仙_ws触发测试.py
"""

from __future__ import annotations

import asyncio
import json
from base64 import b64decode
import sys
from datetime import date
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import 修仙.贸易服务 as trade_module
import 修仙.异界虫洞 as wormhole_module
import 修仙.修仙物品 as treasure_module
import 修仙.对战 as duel_module
import 修仙.二手市场 as second_hand_module
import 修仙.探险 as exploration_module
import 修仙.武器 as weapon_module
import 修仙.修仙百科 as encyclopedia_module
import 修仙.源库 as vault_module
import 修仙.修仙帮助 as help_module
import 修仙.玩家 as player_module
import 修仙.宗门 as sect_module
import 修仙.纳戒 as ring_module
import 修仙.背包 as backpack_module
import 修仙.保险箱 as insurance_module
import 修仙.装备 as equipment_module
import 修仙.铭刻 as inscription_module
import 修仙.首领 as seasonal_boss_module
import 修仙.修仙界历史 as history_module
from launch.adapter.ws import WsMessageHandler, make_payload
from launch.adapter.ws.message import _loads_message
from 修仙.combat_core import CombatCore
from 修仙.common import business_day, weapon_id_label
from 修仙.item_effects import ItemEffectService
from 修仙.sql import XiuxianDB
from 修仙.weapon_core import WeaponCore
from 修仙.贸易服务.service import TradeService
from 修仙.修仙物品.service import ItemInfoService
from 修仙.对战.service import DuelService
from 修仙.wormhole_service import WormholeService
from 修仙.二手市场.service import SecondHandService
from 修仙.探险.service import ExplorationService
from 修仙.武器.service import WeaponService
from 修仙.修仙百科.service import EncyclopediaService
from 修仙.源库.service import SourceVaultService
from 修仙.修仙帮助.service import HelpService
from 修仙.玩家.service import PlayerService
from 修仙.宗门.service import SectService
from 修仙.纳戒.service import RingService
from 修仙.背包.service import BackpackService
from 修仙.保险箱.service import InsuranceBoxService
from 修仙.装备.service import EquipmentService
from 修仙.铭刻.service import InscriptionService
from 修仙.首领.service import BOSS_DEFS, SeasonalBossService
from 修仙.修仙界历史.service import XiuxianHistoryService

combat_core_module = import_module("修仙.combat_core")
exploration_service_module = import_module("修仙.探险.service")
ring_service_module = import_module("修仙.纳戒.service")
backpack_service_module = import_module("修仙.背包.service")
duel_service_module = import_module("修仙.对战.service")
wormhole_service_module = import_module("修仙.wormhole_service")
seasonal_boss_service_module = import_module("修仙.首领.service")


WS_MODULES = (
    help_module,
    player_module,
    sect_module,
    vault_module,
    ring_module,
    backpack_module,
    insurance_module,
    trade_module,
    wormhole_module,
    treasure_module,
    weapon_module,
    encyclopedia_module,
    exploration_module,
    duel_module,
    equipment_module,
    second_hand_module,
    inscription_module,
    seasonal_boss_module,
    history_module,
)


class FakeManager:
    """收集 ws_manager.send 发出的回复。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send(self, message: Any, client_id: str, **_: Any) -> None:
        self.sent.append((client_id, make_payload(message)))

async def main_async() -> None:
    """按 ws 分发流程跑一轮当前修仙命令。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_ws_test.db")
        manager = FakeManager()
        old_state = _patch_modules(db, manager)
        try:
            await WsMessageHandler.run()
            await _assert_command_plan()
            await _assert_cq_at_split()

            await _dispatch(manager, "player_ws", "帮助")
            _must_reply(manager, "player_ws", "修仙帮助网页")
            _must_reply(manager, "player_ws", "/xiuxian/help")

            await _dispatch(manager, "player_ws", "修仙帮助")
            _must_image_reply(manager, "player_ws")

            await _dispatch(manager, "player_ws", "指南")
            _must_reply(manager, "player_ws", "修行成长")

            await _dispatch(manager, "player_ws", "指南 战斗")
            _must_reply(manager, "player_ws", "指南·探险战斗")
            _must_reply(manager, "player_ws", "探险状态")

            await _dispatch(manager, "player_ws", "创建用户 青衫客")
            _must_reply(manager, "player_ws", "创建成功")
            _must_reply(manager, "player_ws", "【青衫客")

            await _dispatch(manager, "target_ws", "创建用户 安兰")
            _must_reply(manager, "target_ws", "创建成功")

            await _dispatch(manager, "player_ws", "宗门")
            _must_reply(manager, "player_ws", "你还没有宗门")
            await _dispatch(manager, "player_ws", "建立宗门 0 0 青云宗")
            _must_reply(manager, "player_ws", "已有特殊地点")
            await _dispatch(manager, "player_ws", "建立宗门 -49 -49 青云宗")
            _must_reply(manager, "player_ws", "宗门创建成功")
            _must_reply(manager, "player_ws", "山门坐标：(-49,-49)")
            await _dispatch(manager, "player_ws", "宗门")
            _must_reply(manager, "player_ws", "宗主：青衫客")
            _must_reply(manager, "player_ws", "身份：宗主")
            await _dispatch(manager, "target_ws", "加入宗门 青云宗")
            _must_reply(manager, "target_ws", "需要到山门所在地")
            await _dispatch(manager, "target_ws", "导航 -49 -49")
            _must_reply(manager, "target_ws", "已到达")
            await _dispatch(manager, "target_ws", "宗门")
            _must_reply(manager, "target_ws", "这里是宗门：青云宗")
            assert _button_commands(manager.sent[-1][1])[0] == "加入宗门 青云宗"
            await _dispatch(manager, "target_ws", "加入宗门 青云宗")
            _must_reply(manager, "target_ws", "已加入宗门：青云宗")

            await _dispatch(manager, "player_ws", "切磋[CQ:at,qq=target_ws]")
            _must_reply(manager, "player_ws", "接受切磋 青衫客")
            _must_reply(manager, "player_ws", "拒绝切磋 青衫客")
            await _dispatch(manager, "target_ws", "拒绝切磋[CQ:at,qq=player_ws]")
            _must_reply(manager, "target_ws", "已拒绝")

            await _dispatch(manager, "player_ws", "决斗 安兰")
            _must_reply(manager, "player_ws", "决斗格式不正确")

            db.execute(
                "UPDATE players SET source_stones = source_stones + 1000 WHERE client_id = ?",
                ("player_ws",),
            )
            await _dispatch(manager, "player_ws", "决斗[CQ:at,qq=target_ws] 100")
            _must_reply(manager, "player_ws", "接受决斗 青衫客")
            _must_reply(manager, "player_ws", "拒绝决斗 青衫客")
            await _dispatch(manager, "target_ws", "拒绝决斗[CQ:at,qq=player_ws]")
            _must_reply(manager, "target_ws", "已拒绝")

            await _dispatch(manager, "player_ws", "决斗 100[CQ:at,qq=target_ws]")
            _must_reply(manager, "player_ws", "接受决斗 青衫客")
            _must_reply(manager, "player_ws", "拒绝决斗 青衫客")
            await _dispatch(manager, "target_ws", "拒绝决斗 青衫客")
            _must_reply(manager, "target_ws", "已拒绝")

            await _dispatch(manager, "player_ws", "修仙信息")
            _must_reply(manager, "player_ws", "Lv.1")
            _must_reply(manager, "player_ws", "血气")
            _must_reply(manager, "player_ws", "战斗日志：简要")
            _must_reply(manager, "player_ws", "【青衫客")

            await _dispatch(manager, "player_ws", "战斗日志")
            _must_reply(manager, "player_ws", "当前模式：**简要**")
            await _dispatch(manager, "player_ws", "战斗日志 开启")
            _must_reply(manager, "player_ws", "战斗日志已切换为详细")
            await _dispatch(manager, "player_ws", "修仙信息")
            _must_reply(manager, "player_ws", "战斗日志：详细")
            await _dispatch(manager, "player_ws", "战斗日志 关闭")
            _must_reply(manager, "player_ws", "战斗日志已切换为简要")

            await _dispatch(manager, "player_ws", "修仙早报")
            _must_reply(manager, "player_ws", "修仙早报")
            _must_reply(manager, "player_ws", "坊间传闻")

            await _dispatch(manager, "player_ws", "修仙界历史")
            _must_reply(manager, "player_ws", "修仙界历史")

            await _dispatch(manager, "player_ws", "人物志 安兰")
            _must_reply(manager, "player_ws", "安兰人物志")
            _must_reply(manager, "player_ws", "修仙界事迹")

            await _dispatch(manager, "player_ws", "人物志[CQ:at,qq=target_ws]")
            _must_reply(manager, "player_ws", "安兰人物志")
            _must_reply(manager, "player_ws", "修仙界事迹")

            with db.transaction() as conn:
                item = conn.execute(
                    "SELECT item_id FROM item_defs WHERE name = ?",
                    ("星官旧简",),
                ).fetchone()
                assert item is not None
                SecondHandService(db).add_backpack_conn(conn, "target_ws", item["item_id"], 1)
                conn.execute(
                    "UPDATE players SET source_stones = source_stones + 1000 WHERE client_id = ?",
                    ("player_ws",),
                )
            await _dispatch(manager, "target_ws", "二手市场上架 星官旧简 1 100")
            _must_reply(manager, "target_ws", "上架成功")
            await _dispatch(manager, "player_ws", "二手市场购买[CQ:at,qq=target_ws]")
            _must_reply(manager, "player_ws", "购买成功")

            await _dispatch(manager, "player_ws", "背包")
            _must_reply(manager, "player_ws", "背包")

            await _dispatch(manager, "player_ws", "自动用药 关闭")
            _must_reply(manager, "player_ws", "自动用药已关闭")

            await _dispatch(manager, "player_ws", "自动用药 开启")
            _must_reply(manager, "player_ws", "自动用药已开启")

            await _dispatch(manager, "player_ws", "休息")
            _must_reply(manager, "player_ws", "开始休息")
            db.execute(
                "UPDATE players SET status = '休息中', rest_full_at = ? WHERE client_id = ?",
                ("2000-01-01T00:00:00", "player_ws"),
            )
            await _dispatch(manager, "player_ws", "休息结束")
            _must_reply(manager, "player_ws", "休息结束")

            await _dispatch(manager, "player_ws", "新手礼包")
            _must_reply(manager, "player_ws", "新手礼包领取成功")

            await _dispatch(manager, "player_ws", "纳戒")
            _must_reply(manager, "player_ws", "血契丹")

            await _dispatch(manager, "player_ws", "使用 血契丹")
            _must_reply(manager, "player_ws", "血气+")

            with db.transaction() as conn:
                RingService(db).add_ring_conn(conn, "player_ws", "fudai", 2)
            await _dispatch(manager, "player_ws", "使用 福袋 2")
            _must_reply(manager, "player_ws", "使用 福袋 x2 成功")
            _must_reply(manager, "player_ws", "源石+")

            with db.transaction() as conn:
                RingService(db).add_ring_conn(conn, "player_ws", "xisuiye", 1)
                RingService(db).add_ring_conn(conn, "player_ws", "fengren_shu", 1)
            await _dispatch(manager, "player_ws", "使用 洗髓液")
            _must_reply(manager, "player_ws", "不能直接使用")
            await _dispatch(manager, "player_ws", "使用 风刃书")
            _must_reply(manager, "player_ws", "不能直接使用")
            await _dispatch(manager, "player_ws", "洗髓")
            _must_reply(manager, "player_ws", "洗髓")

            await _dispatch(manager, "player_ws", "武器")
            _must_reply(manager, "player_ws", "青岚短剑")
            await _dispatch(manager, "player_ws", "查看武器 1")
            _must_reply(manager, "player_ws", "武器详情")
            _must_reply(manager, "player_ws", "模板：")
            with db.transaction() as conn:
                _add_feathers_conn(conn, "player_ws", 4)
                RingService(db).add_ring_conn(conn, "player_ws", "fengren_shu", 1)
                conn.execute(
                    "UPDATE player_weapons SET level = 20, max_level = 45 WHERE holder_id = ?",
                    ("player_ws",),
                )
            first_weapon = db.fetch_one(
                "SELECT weapon_id FROM player_weapons WHERE holder_id = ? ORDER BY weapon_id LIMIT 1",
                ("player_ws",),
            )
            assert first_weapon is not None
            await _dispatch(manager, "player_ws", "铭刻 装备 头部 青云冠")
            _must_reply(manager, "player_ws", "铭刻成功")
            _must_reply(manager, "player_ws", "青云冠（头部）")
            assert "->" not in _last_reply_text(manager), manager.sent
            await _dispatch(manager, "player_ws", f"铭刻 武器 武器#{first_weapon['weapon_id']} 青云剑")
            _must_reply(manager, "player_ws", "铭刻成功")
            _must_reply(manager, "player_ws", "青云剑（青岚短剑）")
            assert "->" not in _last_reply_text(manager), manager.sent
            await _dispatch(manager, "player_ws", f"铭刻 技能 武器#{first_weapon['weapon_id']} 青云斩")
            _must_reply(manager, "player_ws", "铭刻成功")
            _must_reply(manager, "player_ws", "青云斩（风刃斩）")
            assert "->" not in _last_reply_text(manager), manager.sent
            await _dispatch(manager, "player_ws", f"附魔武器 {first_weapon['weapon_id']} 风刃书")
            _must_reply(manager, "player_ws", "附魔成功")
            await _dispatch(manager, "player_ws", f"铭刻 附魔 武器#{first_weapon['weapon_id']} 1 青云破")
            _must_reply(manager, "player_ws", "铭刻成功")
            _must_reply(manager, "player_ws", "青云破（风刃书）")
            assert "->" not in _last_reply_text(manager), manager.sent
            await _dispatch(manager, "player_ws", "武器")
            _must_reply(manager, "player_ws", "青云斩（风刃斩）")
            _must_reply(manager, "player_ws", "青云破")
            await _dispatch(manager, "player_ws", f"查看武器 {first_weapon['weapon_id']}")
            _must_reply(manager, "player_ws", "自带技能：")
            _must_reply(manager, "player_ws", "青云破（风刃书）")

            seasonal_boss = SeasonalBossService(db)
            with db.transaction() as conn:
                conn.execute("DELETE FROM seasonal_boss_participants")
                conn.execute("DELETE FROM seasonal_boss_events")
                conn.execute("DELETE FROM inscription_feathers WHERE client_id = ?", ("player_ws",))
            event = seasonal_boss._open_event(date(2099, 2, 4), BOSS_DEFS["lichun"], "二十四节气", "普通节气")
            db.execute(
                "UPDATE seasonal_boss_events SET business_day = ?, hp = 1 WHERE event_id = ?",
                (business_day(), event["event_id"]),
            )
            await _dispatch(manager, "player_ws", "首领状态")
            _must_reply(manager, "player_ws", "折柳青郎")
            await _dispatch(manager, "player_ws", "首领排行")
            _must_reply(manager, "player_ws", "暂无挑战记录")
            await _dispatch(manager, "player_ws", "挑战首领")
            _must_reply(manager, "player_ws", "已被送回岁时深处")
            _must_reply(manager, "player_ws", "战斗日志")
            boss_reply = _last_reply_text(manager)
            assert "我方技能" not in boss_reply, manager.sent
            assert "首领技能" not in boss_reply, manager.sent
            assert "行动 **" not in boss_reply, manager.sent
            assert "我方出手" not in boss_reply, manager.sent
            await _dispatch(manager, "player_ws", "首领奖励")
            _must_reply(manager, "player_ws", "岁时情劫奖励")
            with db.transaction() as conn:
                _add_feathers_conn(conn, "player_ws", 1)
            await _dispatch(manager, "player_ws", "铭刻之羽")
            _must_reply(manager, "player_ws", "测试遗羽1")

            recycle_weapon_id = WeaponService(db).create_weapon("player_ws", "qinglan_duanjian", "良品", 45, equipped=False)
            db.execute(
                "UPDATE players SET location_name = '铸剑阁', x = -120, y = 760 WHERE client_id = ?",
                ("player_ws",),
            )
            await _dispatch(manager, "player_ws", f"出售 {recycle_weapon_id} 1")
            _must_reply(manager, "player_ws", "回收成功")
            with db.transaction() as conn:
                EquipmentService(db).add_gem_conn(conn, "player_ws", "huxinyu", 2, 2)
                conn.execute(
                    "UPDATE players SET location_name = '琢玉楼', x = 320, y = 760 WHERE client_id = ?",
                    ("player_ws",),
                )
            await _dispatch(manager, "player_ws", "出售 护心玉 2级 1")
            _must_reply(manager, "player_ws", "回收成功")
            with db.transaction() as conn:
                WeaponService(db).add_ring_conn(conn, "player_ws", "fengren_shu", 2)
                conn.execute(
                    "UPDATE players SET location_name = '藏经阁', x = 120, y = 820 WHERE client_id = ?",
                    ("player_ws",),
                )
            await _dispatch(manager, "player_ws", "出售 风刃书 1")
            _must_reply(manager, "player_ws", "回收成功")
            db.execute(
                "UPDATE players SET location_name = '天枢城', x = 0, y = 0 WHERE client_id = ?",
                ("player_ws",),
            )

            with db.transaction() as conn:
                EquipmentService(db).add_ring_conn(conn, "player_ws", "huxinyu", 1)
                EquipmentService(db).add_ring_conn(conn, "player_ws", "xuangui shi", 1)
                EquipmentService(db).add_ring_conn(conn, "player_ws", "kaikongqi", 1)
            await _dispatch(manager, "player_ws", "装备")
            _must_reply(manager, "player_ws", "头部")
            await _dispatch(manager, "player_ws", "装备升级 头部")
            _must_reply(manager, "player_ws", "升级成功")
            await _dispatch(manager, "player_ws", "开孔 头部")
            _must_reply(manager, "player_ws", "开孔成功")
            await _dispatch(manager, "player_ws", "镶嵌 头部 1 护心玉 1级")
            _must_reply(manager, "player_ws", "镶嵌成功")
            await _dispatch(manager, "player_ws", "镶嵌 头部 4 玄龟石 1级")
            _must_reply(manager, "player_ws", "镶嵌成功")

            await _dispatch(manager, "player_ws", "探险列表")
            _must_reply(manager, "player_ws", "天枢城")
            await _dispatch(manager, "player_ws", "跑商奖励")
            _must_reply(manager, "player_ws", "普通跑商")

            await _dispatch(manager, "player_ws", "虫洞")
            _must_reply(manager, "player_ws", "当前没有开启")
            event = wormhole_module.service._open_event("player_ws", "test", "天枢城")
            db.execute(
                "UPDATE players SET location_name = ?, x = ?, y = ? WHERE client_id = ?",
                (event["location_name"], event["x"], event["y"], "player_ws"),
            )
            await _dispatch(manager, "player_ws", "虫洞状态")
            _must_reply(manager, "player_ws", "异界虫洞")
            await _dispatch(manager, "player_ws", "虫洞排行")
            _must_reply(manager, "player_ws", "暂无挑战记录")
            await _dispatch(manager, "player_ws", "挑战虫洞")
            _must_reply(manager, "player_ws", "挑战虫洞")
            _must_reply(manager, "player_ws", "战斗日志")
            wormhole_reply = _last_reply_text(manager)
            assert "我方技能" not in wormhole_reply, manager.sent
            assert "Boss技能" not in wormhole_reply, manager.sent
            assert "行动 **" not in wormhole_reply, manager.sent
            assert "我方出手" not in wormhole_reply, manager.sent
            await _dispatch(manager, "player_ws", "虫洞奖励")
            _must_reply(manager, "player_ws", "还没有结束")

            await _dispatch(manager, "player_ws", "查看修仙物品 星官旧简")
            _must_reply(manager, "player_ws", "星官旧简")
            await _dispatch(manager, "player_ws", "查看修仙物品 福袋")
            _must_reply(manager, "player_ws", "存放：纳戒")

            await _dispatch(manager, "player_ws", "修仙百科 宗门战奖励机制")
            _must_reply(manager, "player_ws", "参考：")
            _must_reply(manager, "player_ws", "宗门")

            db.execute(
                "UPDATE players SET location_name = '天枢城', x = 0, y = 0, status = '空闲', hp = max_hp, mp = max_mp WHERE client_id = ?",
                ("player_ws",),
            )
            await _dispatch(manager, "player_ws", "探险 青岚坊")
            _must_reply(manager, "player_ws", "开始探险：青岚坊")

            await _dispatch(manager, "player_ws", "探险状态")
            _must_reply(manager, "player_ws", "探险状态")

            await _dispatch(manager, "player_ws", "结束探险")
            _must_reply(manager, "player_ws", "30 分钟冷却")

            db.execute(
                "UPDATE exploration_records SET ready_at = ? WHERE client_id = ?",
                ("2000-01-01T00:00:00", "player_ws"),
            )
            await _dispatch(manager, "player_ws", "战斗日志 开启")
            _must_reply(manager, "player_ws", "战斗日志已切换为详细")
            await _dispatch(manager, "player_ws", "结束探险")
            _must_reply(manager, "player_ws", "探险结束")
            payload = manager.sent[-1][1]
            assert isinstance(payload, dict), payload
            assert payload.get("type") == "markdown", payload
            body = _message_text(payload)
            assert "领取动作" not in body, payload
            assert "战斗日志" in body, payload
            assert "zhandou-rizhi/explore" in body, payload
            assert "detail=1" in body, payload
            assert "武器经验" in body, payload
        finally:
            _restore_modules(old_state)
            db.close()


def _patch_modules(db: XiuxianDB, manager: FakeManager) -> dict[str, Any]:
    """把模块级 service 和 ws_manager 临时换成测试对象。"""

    item_effects = ItemEffectService(db)
    weapon_core = WeaponCore(db)
    combat_core_module.weapon_core = weapon_core
    combat_core = CombatCore(db)

    ring_service_module.item_effects = item_effects
    backpack_service_module.item_effects = item_effects
    exploration_service_module.weapon_service = weapon_core
    exploration_service_module.combat_service = combat_core
    duel_service_module.combat_service = combat_core
    wormhole_service_module.weapon_service = weapon_core
    wormhole_service_module.combat_service = combat_core
    seasonal_boss_service_module.weapon_service = weapon_core
    seasonal_boss_service_module.combat_service = combat_core

    old_state: dict[str, Any] = {
        "services": {module: module.service for module in WS_MODULES},
        "managers": {module: module.ws_manager for module in WS_MODULES},
    }

    replacements = {
        help_module: HelpService(db),
        player_module: PlayerService(db),
        sect_module: SectService(db),
        vault_module: SourceVaultService(db),
        ring_module: RingService(db),
        backpack_module: BackpackService(db),
        insurance_module: InsuranceBoxService(db),
        trade_module: TradeService(db),
        wormhole_module: WormholeService(db),
        treasure_module: ItemInfoService(db),
        weapon_module: WeaponService(db),
        encyclopedia_module: EncyclopediaService(db),
        exploration_module: ExplorationService(db),
        duel_module: DuelService(db),
        equipment_module: EquipmentService(db),
        second_hand_module: SecondHandService(db),
        inscription_module: InscriptionService(db),
        seasonal_boss_module: SeasonalBossService(db),
        history_module: XiuxianHistoryService(db),
    }
    replacements[sect_module]._is_member_locked = lambda value=None: False  # type: ignore[attr-defined, method-assign]
    for module, service in replacements.items():
        module.service = service
        module.ws_manager = manager
    return old_state


def _restore_modules(old_state: dict[str, Any]) -> None:
    """恢复被测试替换的模块级对象。"""

    for module, service in old_state["services"].items():
        module.service = service
    for module, manager in old_state["managers"].items():
        module.ws_manager = manager


_request_no = 0


async def _dispatch(manager: FakeManager, client_id: str, message: str) -> None:
    """构造标准 ws 消息，并交给 ws 驱动分发。"""

    global _request_no
    _request_no += 1
    manager.sent.clear()
    message_data = _loads_message(
        json.dumps(
            {
                "code": 202,
                "type": "text",
                "message": message,
                "request_id": f"xiuxian-ws-test-{_request_no}",
            },
            ensure_ascii=False,
        )
    )
    assert message_data is not None
    await WsMessageHandler.dispatch(
        client_id=client_id,
        message_data=message_data,
        manager=manager,
    )


async def _assert_cq_at_split() -> None:
    """确认接收层先把 CQ/at 转成内部标识，再按第一个空格拆命令。"""

    long_id = "4FFECC65975CF472481FBF363A669B20"
    cases = (
        (
            f"切磋[CQ:at,qq={long_id}]",
            "切磋",
            long_id,
        ),
        (
            f"决斗[CQ:at,qq={long_id}] 100",
            "决斗",
            f"{long_id} 100",
        ),
        (
            f"决斗 100[CQ:at,qq={long_id}]",
            "决斗",
            f"100 {long_id}",
        ),
        (
            f"人物志[CQ:at,qq={long_id}]",
            "人物志",
            long_id,
        ),
        (
            f"二手市场购买[CQ:at,qq={long_id}]",
            "二手市场购买",
            long_id,
        ),
        (
            f"拒绝切磋[CQ:at,qq={long_id}]",
            "拒绝切磋",
            long_id,
        ),
    )
    for raw_message, expected_cmd, expected_message in cases:
        message_data = _loads_message(json.dumps({"message": raw_message}, ensure_ascii=False))
        assert message_data is not None
        cmd, message = await WsMessageHandler._split_message(str(message_data["message"]))
        assert cmd == expected_cmd, raw_message
        assert message == expected_message, raw_message


async def _assert_command_plan() -> None:
    """确认修仙查看类入口已经收窄，不再被旧别名误触发。"""

    old_view_commands = (
        "用户创建",
        "礼包",
        "获取源库",
        "源库获取",
        "结息源库",
        "地点",
        "探索",
        "状态探险",
        "探索状态",
        "结束探索",
        "探索结束",
        "记录掉落",
        "跑商",
        "列表商场",
        "跑商列表",
        "详情商场",
        "跑商详情",
        "市价商场",
        "跑商市价",
        "购买商场",
        "跑商购买",
        "出售商场",
        "跑商出售",
        "自动出售商场",
        "跑商自动出售",
        "推荐商场",
        "跑商推荐",
        "记录商场",
        "限制商场",
        "奖励商场",
        "奖励跑商",
        "收购特殊",
        "收购",
        "出售特殊",
        "战利品出售",
        "自动出售特殊",
        "前往",
        "二手",
        "上架二手市场",
        "二手上架",
        "下架二手市场",
        "二手下架",
        "购买二手市场",
        "二手购买",
        "武器切换",
        "换武器",
        "武器升级",
        "武器回收",
        "技能书回收",
        "武器附魔",
        "升级装备",
        "升级宝石",
        "宝石回收",
        "装备铭刻",
        "武器铭刻",
        "附魔铭刻",
        "技能铭刻",
        "铭刻武器技能",
        "武器技能铭刻",
        "铭刻自带技能",
        "自带技能铭刻",
        "切磋接受",
        "切磋拒绝",
        "决斗接受",
        "决斗拒绝",
        "记录决斗",
        "异界虫洞",
        "状态虫洞",
        "虫洞挑战",
        "排行虫洞",
        "奖励虫洞",
        "状态首领",
        "首领挑战",
        "排行首领",
        "奖励首领",
        "查看背包",
        "背包查看",
        "查看纳戒",
        "纳戒查看",
        "查看装备库",
        "装备库查看",
        "装备库",
        "使用装备库",
        "装备库使用",
        "物品库",
        "武器查看",
        "查看装备",
        "装备查看",
        "查看孔位",
        "孔位查看",
        "我的宝石",
        "查看宝石",
        "宝石查看",
        "新手指引",
        "修行札记",
        "札记",
        "今日风云榜",
        "修仙小报",
        "今日小报",
        "今日气运",
        "气运",
        "称号",
        "佩戴称号",
    )
    for command in old_view_commands:
        assert not await WsMessageHandler.has_match(_message_data(command)), f"旧查看入口不应命中：{command}"

    main_view_commands = (
        "帮助",
        "修仙帮助",
        "指南",
        "状态",
        "宗门",
        "建立宗门 -49 -49 青云宗",
        "加入宗门 青云宗",
        "背包",
        "纳戒",
        "保险箱",
        "查看保险箱",
        "源库",
        "源库结息",
        "升级源库",
        "源库升级",
        "存入源石 100",
        "源石存入 100",
        "取出源石 50",
        "源石取出 50",
        "修仙日记",
        "查看修仙物品",
        "武器",
        "查看武器 1",
        "武器传奇 1",
        "装备",
        "孔位",
        "宝石",
        "风云榜",
        "修仙早报",
        "修仙界历史",
        "人物史榜",
        "宗门史榜",
        "城池史榜",
        "战斗名局",
        "商路奇闻",
        "异界虫洞录",
        "人物志 青衫客",
        "人物志[CQ:at,qq=target_ws]",
        "二手市场购买[CQ:at,qq=target_ws]",
    )
    for command in main_view_commands:
        assert await WsMessageHandler.has_match(_message_data(command)), f"主查看入口应可命中：{command}"


def _message_data(message: str) -> dict[str, Any]:
    """生成最小 ws 文本消息，并走真实 WS 入口的消息标准化。"""

    message_data = _loads_message(json.dumps({
        "code": 202,
        "type": "text",
        "message": message,
        "request_id": f"xiuxian-command-plan-{message}",
    }, ensure_ascii=False))
    assert message_data is not None
    return message_data


def _must_reply(manager: FakeManager, client_id: str, text: str) -> None:
    """断言当前 client_id 收到包含指定文本的回复。"""

    assert manager.sent, "期望有 ws 回复，实际没有"
    assert len(manager.sent) == 1, f"一条命令只能产生一条回复，实际：{manager.sent}"
    last_client_id, message = manager.sent[-1]
    assert last_client_id == client_id, manager.sent
    assert text in _message_search_text(message), message


def _last_reply_text(manager: FakeManager) -> str:
    """读取最后一条回复的正文。"""

    assert manager.sent, "期望有 ws 回复，实际没有"
    return _message_text(manager.sent[-1][1])


def _message_text(message: Any) -> str:
    """把 text/markdown 回复统一还原成可断言的正文。"""

    if not isinstance(message, dict):
        return str(message)
    payload = message.get("message", "")
    if isinstance(payload, dict):
        return str(payload.get("content", ""))
    return str(payload)


def _message_search_text(message: Any) -> str:
    """把正文和按钮命令拼起来，方便断言纯按钮导航回复。"""

    text = _message_text(message)
    return "\n".join([text, *_button_commands(message)])


def _button_commands(message: Any) -> list[str]:
    """读取 markdown 回复里的按钮命令。"""

    if not isinstance(message, dict):
        return []
    payload = message.get("message", "")
    if not isinstance(payload, dict):
        return []

    commands: list[str] = []
    rows = payload.get("keyboard", {}).get("content", {}).get("rows", [])
    if isinstance(rows, list):
        for row in rows:
            buttons = row.get("buttons", []) if isinstance(row, dict) else []
            if not isinstance(buttons, list):
                continue
            for item in buttons:
                if not isinstance(item, dict):
                    continue
                action = item.get("action", {})
                command = action.get("data", "") if isinstance(action, dict) else ""
                if command:
                    commands.append(str(command))
    return commands


def _must_image_reply(manager: FakeManager, client_id: str) -> None:
    """断言当前 client_id 收到图片回复。"""

    assert manager.sent, "期望有 ws 回复，实际没有"
    assert len(manager.sent) == 1, f"一条命令只能产生一条回复，实际：{manager.sent}"
    last_client_id, message = manager.sent[-1]
    assert last_client_id == client_id, manager.sent
    assert isinstance(message, dict), message
    assert message.get("type") == "image", message
    assert not message.get("message", "").startswith("data:image/"), "图片回复不能带 data URI 头"
    image_bytes = b64decode(message.get("message", ""))
    assert image_bytes.startswith(b"\x89PNG"), "图片回复不是 PNG base64"


def _add_feathers_conn(conn, client_id: str, quantity: int) -> None:
    """给测试玩家补充带文案的铭刻之羽实例。"""

    for index in range(quantity):
        conn.execute(
            """
            INSERT INTO inscription_feathers
            (client_id, source_key, source_name, title, flavor_text, obtained_at)
            VALUES (?, 'test', '测试岁时情劫', ?, ?, '2000-01-01T00:00:00')
            """,
            (client_id, f"测试遗羽{index + 1}", f"这是一枚用于测试的铭刻之羽文案 {index + 1}。"),
        )


def main() -> None:
    asyncio.run(main_async())
    print("修仙 ws 触发测试通过")


if __name__ == "__main__":
    main()
