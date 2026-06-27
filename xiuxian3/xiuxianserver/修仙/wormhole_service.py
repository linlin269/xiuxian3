"""异界虫洞组件服务。

虫洞是跑商过程中偶发的全服动态 Boss。
贸易服务只负责“可能发现虫洞”，挑战、排行、奖励都由本组件自己处理。
"""

from __future__ import annotations

import math
import random
from datetime import timedelta
from statistics import median
from typing import Any

from . import combat_log_text
from .combat_core import service as combat_service
from .common import CoreService, business_day, dt, dump_json, load_json, money, now, random_quality, ts, weapon_id_label
from .constants import (
    MAX_LEVEL,
    WORMHOLE_ACTIVE_WINDOW_DAYS,
    WORMHOLE_CHALLENGE_COOLDOWN_MINUTES,
    WORMHOLE_DAILY_ACTIVE_PLAYER_STEP,
    WORMHOLE_DAILY_BASE_LIMIT,
    WORMHOLE_DAILY_MIN_LIMIT,
    WORMHOLE_DAILY_MAX_LIMIT,
    WORMHOLE_DURATION_MINUTES,
    WORMHOLE_NOTICE_COOLDOWN_MINUTES,
)
from .format_text import T
from .rules import damage_after_defense, monster_exp
from .sect_war import record_sect_merit_conn, sect_direction_bonus_conn
from .sql import db
from .weapon_core import WeaponCore
from .world_materials import WorldMaterialService
from .异火.service import service as flame_service
from .镇渊诛邪.service import service as zhenyuan_zhuxie_service

BOSS_POOL = (
    ("千刃界游王", "刃影", 0.98),
    ("陨炉泰坦", "泰坦", 1.18),
    ("赤瘟炼狱君", "毒焰", 1.08),
    ("缄魂巫皇", "巫皇", 1.06),
    ("苍根不死王", "不死", 1.10),
    ("回盾镜魔", "镜魔", 1.12),
    ("断星斩王", "斩王", 1.15),
    ("星环仲裁者", "星械", 1.20),
)

BOSS_FLOW_TEXT = {
    "千刃界游王": "高频连击",
    "陨炉泰坦": "重击破防",
    "赤瘟炼狱君": "持续伤害",
    "缄魂巫皇": "压制控制",
    "苍根不死王": "生存续航",
    "回盾镜魔": "反击护身",
    "断星斩王": "斩杀收割",
    "星环仲裁者": "首领协作",
}

WAR_PREP_BOSS_POOL = {
    "镇妖司": (("百臂青妖王", "妖刃", 1.14), ("裂巢迅影后", "妖影", 1.12), ("万爪潮主", "妖潮", 1.20)),
    "伏魔殿": (("黑铠破界魔", "魔铠", 1.20), ("焚契魔侯", "魔焰", 1.18), ("坠岳魔君", "魔君", 1.22)),
    "鬼市": (("无灯冥契主", "鬼契", 1.16), ("缚魂夜巫", "魂巫", 1.14), ("纸城鬼王", "鬼王", 1.18)),
    "龙渊阁": (("逆鳞界龙君", "龙君", 1.22), ("潮骸古蛟", "蛟魂", 1.20), ("星渊断角龙", "龙裔", 1.24)),
    "万兽盟": (("荒骨兽神", "兽神", 1.20), ("苍鬃不死兽", "兽王", 1.18), ("万蹄裂阵王", "兽潮", 1.16)),
    "破军营": (("星甲破阵帅", "星甲", 1.20), ("断旗兵主", "兵主", 1.18), ("铁潮军魂王", "军魂", 1.22)),
}

WAR_PREP_AFFIXES = (
    "残巢已露",
    "旧门半开",
    "战痕回涌",
    "余烬未冷",
    "群敌聚形",
    "裂口不稳",
)


DISCOVERY_CHANCES = {
    "navigate": 0.012,
    "trade_buy": 0.018,
    "trade_sell": 0.02,
    "trade_auto_sell": 0.03,
    "special_sell": 0.025,
    "special_auto_sell": 0.035,
}

WORMHOLE_LOW_CONTRIBUTION_FLOORS = {
    "xisuiye": 0.08,
    "gem": 0.15,
    "book": 0.06,
    "weapon": 0.03,
}

WAR_PREP_REWARD_PROFILES = {
    "镇妖司": {
        "weapon_types": ("匕", "飞刃", "剑", "刀"),
        "book_effects": ("hit_bonus", "dodge_bonus", "pierce_bonus"),
    },
    "伏魔殿": {
        "weapon_types": ("枪", "戟", "斧", "刀"),
        "book_effects": ("pierce_bonus", "defense_suppress", "heavy_bonus"),
    },
    "鬼市": {
        "weapon_types": ("铃", "杖", "匕"),
        "book_effects": ("mp_suppress", "stun_rate", "defense_suppress"),
    },
    "龙渊阁": {
        "weapon_types": ("剑", "枪", "戟", "盘"),
        "book_effects": ("single_hit_bonus", "skill_power_bonus", "pierce_bonus"),
        "max_level_floor_chance": 0.35,
        "max_level_floor": 82,
    },
    "万兽盟": {
        "weapon_types": ("斧", "盾刃", "戟", "杖"),
        "book_effects": ("life_steal", "damage_reduce", "shield_bonus", "heavy_bonus"),
    },
    "破军营": {
        "weapon_types": ("枪", "弩", "戟", "刀", "斧"),
        "book_effects": ("pierce_bonus", "single_hit_bonus", "combo_damage_bonus", "skill_power_bonus"),
    },
}


class WormholeService(CoreService):
    """异界虫洞的开启、挑战、排行和奖励。"""

    def __init__(self, database) -> None:
        super().__init__(database)
        self.world_material = WorldMaterialService(database)
        self.weapon_core = WeaponCore(database)

    def status(self, client_id: str) -> str:
        """查看当前异界虫洞。"""


        _, error = self.require_player(client_id)
        if error:
            return error
        event = self._active_event()
        if not event:
            pending = self._latest_rewardable(client_id)
            if pending:
                return T.hint("当前没有开启的异界虫洞，但你有虫洞奖励待领取。", "发送：虫洞奖励<虫洞奖励>")
            snapshot = self._world_snapshot()
            opened_today = self._today_opened_count()
            daily_limit = self._daily_event_limit(snapshot["active_count"])
            if opened_today >= daily_limit:
                return T.hint(
                    f"今日异界虫洞次数已满：{opened_today}/{daily_limit}。",
                    "明日 04:00 后会重新计算；活跃人数越多，每日虫洞上限越高。",
                )
            return T.hint(
                f"当前没有开启的异界虫洞，今日已出现 {opened_today}/{daily_limit}。",
                    "跑商、导航或战利品出售时有概率发现虫洞。",
            )
        return self._format_status(event)

    def challenge(self, client_id: str) -> str | dict:
        """挑战当前虫洞 Boss。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()
        assert player is not None

        event = self._active_event()
        if not event:
            return T.hint("当前没有开启的异界虫洞。", "跑商、导航或战利品出售时有概率发现虫洞。")
        if event["status"] != "开启":
            return T.hint(f"{event['boss_name']} 已经{event['status']}，不能继续挑战。", "发送：虫洞奖励 查看是否可以领取奖励。<虫洞奖励>")
        if player["status"] != "空闲":
            return self._busy_challenge_hint(player["status"])
        if player["location_name"] != event["location_name"]:
            return T.hint(
                f"虫洞位于 {event['location_name']}，你当前在 {player['location_name']}。",
                f"发送：导航 {event['location_name']}，到达后发送：挑战虫洞"+ f"<导航 {event['location_name']}><挑战虫洞><虫洞奖励>",
            )
        if int(player["hp"]) <= 0:
            return T.hint("血气不足，无法挑战虫洞。", "发送：休息，时间到后发送：结束休息")

        check = self._challenge_check(event["wormhole_id"], client_id)
        if check:
            return check

        result = self._fight_boss(player, event)
        killed = False
        challenge_record_id = 0
        with self.db.transaction() as conn:
            fresh = conn.execute(
                "SELECT * FROM wormholes WHERE wormhole_id = ? AND status = '开启'",
                (event["wormhole_id"],),
            ).fetchone()
            if not fresh:
                return T.hint("异界虫洞已经关闭。", "发送：虫洞奖励 查看是否可以领取奖励。<虫洞奖励>")
            fresh_player = conn.execute(
                "SELECT status, location_name, hp FROM players WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not fresh_player:
                return T.hint("你还没有创建用户。", "发送：创建用户 名称，例如：创建用户 青衫客")
            if str(fresh_player["status"]) != "空闲":
                return self._busy_challenge_hint(str(fresh_player["status"]))
            if str(fresh_player["location_name"]) != str(fresh["location_name"]):
                return T.hint(
                    f"虫洞位于 {fresh['location_name']}，你当前在 {fresh_player['location_name']}。",
                    f"发送：导航 {fresh['location_name']}，到达后发送：挑战虫洞"
                    + f"<导航 {fresh['location_name']}><挑战虫洞><虫洞奖励>",
                )
            if int(fresh_player["hp"]) <= 0:
                return T.hint("血气不足，无法挑战虫洞。", "发送：休息，时间到后发送：结束休息")

            current = conn.execute(
                """
                SELECT last_challenge_at
                FROM wormhole_participants
                WHERE wormhole_id = ? AND client_id = ?
                """,
                (event["wormhole_id"], client_id),
            ).fetchone()
            if current:
                last = dt(current["last_challenge_at"])
                left = timedelta(minutes=WORMHOLE_CHALLENGE_COOLDOWN_MINUTES) - (now() - last) if last else timedelta()
                if left > timedelta():
                    seconds = max(1, int(left.total_seconds()))
                    return T.hint(f"挑战虫洞冷却中，还需 {seconds // 60}分{seconds % 60}秒。", "<挑战虫洞>")

            damage = min(max(1, int(result["damage"])), int(fresh["hp"]))
            left_hp = max(0, int(fresh["hp"]) - damage)
            killed = left_hp <= 0
            result["wormhole_id"] = int(event["wormhole_id"])
            result["boss_name"] = str(event["boss_name"])
            result["boss_label"] = "Boss"
            result["damage"] = damage
            result["hp_before"] = int(fresh["hp"])
            result["hp_after"] = left_hp
            result["killed"] = killed
            result["player_max_hp"] = int(player["max_hp"])
            result["player_max_mp"] = int(player["max_mp"])
            conn.execute(
                "UPDATE players SET hp = ?, mp = ? WHERE client_id = ?",
                (result["hp_left"], result["mp_left"], client_id),
            )
            self.reset_rest_window_conn(conn, client_id, int(result["hp_left"]), int(result["mp_left"]))
            conn.execute(
                """
                INSERT INTO wormhole_participants
                (wormhole_id, client_id, damage, challenge_count, last_challenge_at, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(wormhole_id, client_id)
                DO UPDATE SET
                    damage = damage + excluded.damage,
                    challenge_count = challenge_count + 1,
                    last_challenge_at = excluded.last_challenge_at,
                    updated_at = excluded.updated_at
                """,
                (event["wormhole_id"], client_id, damage, ts(), ts(), ts()),
            )
            cursor = conn.execute(
                """
                INSERT INTO wormhole_challenge_records
                (wormhole_id, client_id, damage, hp_before, hp_after, killed, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["wormhole_id"],
                    client_id,
                    damage,
                    int(fresh["hp"]),
                    left_hp,
                    1 if killed else 0,
                    dump_json(result),
                    ts(),
                ),
            )
            challenge_record_id = int(cursor.lastrowid)
            if killed:
                result_meta = self._event_metadata(fresh)
                result_meta["killer"] = client_id
                conn.execute(
                    """
                    UPDATE wormholes
                    SET hp = 0, status = '已击杀', killed_at = ?, result = ?
                    WHERE wormhole_id = ?
                    """,
                    (ts(), dump_json(result_meta), event["wormhole_id"]),
                )
            else:
                conn.execute(
                    "UPDATE wormholes SET hp = ? WHERE wormhole_id = ?",
                    (left_hp, event["wormhole_id"]),
                )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '挑战虫洞', ?, ?)",
                (
                    client_id,
                    (
                        f"wormhole={event['wormhole_id']}, damage={damage}, "
                        f"weapon_exp={int(result.get('weapon_exp', 0)) if int(result.get('weapon_id', 0)) > 0 else 0}"
                    ),
                    ts(),
                ),
            )
            self.record_weapon_combat_conn(
                conn,
                client_id,
                int(result.get("weapon_id", 0)),
                boss_challenge=True,
                damage=int(result.get("highest_damage", damage)),
                weapon_exp=int(result.get("weapon_exp", 0)),
            )
            if killed:
                zhenyuan_zhuxie_service.grant_boss_points_conn(
                    conn,
                    client_id,
                    1,
                    source_name=str(event["boss_name"]),
                    source_key=str(event["wormhole_id"]),
                    source_module="虫洞",
                    extra={"location_name": str(event["location_name"]), "boss_kind": str(event.get("boss_kind") or "")},
                )

        return self._challenge_log_block(
            title=f"挑战虫洞：{event['boss_name']}",
            boss_name=event["boss_name"],
            player=player,
            result=result,
            damage=damage,
            left_hp=left_hp,
            max_hp=int(event["max_hp"]),
            killed=killed,
            killed_text="Boss 已被击杀，可以领取虫洞奖励。",
            alive_text=f"再次挑战需等待 {WORMHOLE_CHALLENGE_COOLDOWN_MINUTES} 分钟。",
            hurt_text="你被虫洞反震重伤，建议先休息。",
            reward_command="虫洞奖励",
            challenge_command="挑战虫洞",
            log_kind="wormhole",
            record_id=challenge_record_id,
        )

    def ranking(self, client_id: str) -> str:
        """查看当前或最近虫洞排行。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        event = self._active_event() or self._latest_event()
        if not event:
            return T.hint("暂无异界虫洞记录。", "跑商、导航或战利品出售时有概率发现虫洞。")
        rows = self._participants(event["wormhole_id"])
        if not rows:
            return T.hint(f"{event['boss_name']} 暂无挑战记录。", "发送：挑战虫洞 参与本次虫洞。<挑战虫洞>")
        panel = T.panel()
        panel.section(f"异界虫洞排行·{event['boss_name']}")
        for index, row in enumerate(rows[:10], start=1):
            panel.line(
                f"{index}. {self.format_player_name(row['client_id'])} "
                f"伤害 **{row['damage']}**｜贡献 {self._contribution(row['damage'], event):.1%}｜"
                f"挑战{row['challenge_count']}次"
            )
        return panel.render()

    def reward(self, client_id: str) -> str:
        """领取最近一次可领取的虫洞奖励。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        self._close_expired_events()
        event = self._latest_rewardable(client_id)
        if not event:
            active = self._active_event()
            if active:
                return T.hint("当前异界虫洞还没有结束。", "继续挑战，或等 Boss 被击杀/虫洞退去后再发送：虫洞奖励")
            return T.hint("没有可领取的虫洞奖励。", "发送：虫洞 查看当前是否有异界虫洞。<虫洞>")

        participant = self.db.fetch_one(
            "SELECT * FROM wormhole_participants WHERE wormhole_id = ? AND client_id = ?",
            (event["wormhole_id"], client_id),
        )
        if not participant:
            return T.hint("你没有参与这次虫洞。", "下一次发现虫洞后发送：挑战虫洞")
        if int(participant["reward_claimed"]):
            return participant["reward_text"] or T.hint("虫洞奖励已经领取。", "发送：虫洞 查看当前虫洞状态。<虫洞>")

        reward = self._roll_reward(event, participant, player)
        with self.db.transaction() as conn:
            fresh = conn.execute(
                "SELECT reward_claimed FROM wormhole_participants WHERE wormhole_id = ? AND client_id = ?",
                (event["wormhole_id"], client_id),
            ).fetchone()
            if not fresh or int(fresh["reward_claimed"]):
                return T.hint("虫洞奖励已经领取。", "发送：虫洞 查看当前虫洞状态。<虫洞>")

            old_level, new_level = self.add_exp_conn(conn, client_id, reward["exp"])
            conn.execute(
                "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                (reward["stones"], client_id),
            )
            for item_id, quantity in reward["ring_items"]:
                self.add_ring_conn(conn, client_id, item_id, quantity)
            for item_id, quantity in reward.get("backpack_items", []):
                ok, reason = self.can_add_backpack_conn(conn, client_id, item_id, quantity)
                if ok:
                    self.add_backpack_conn(conn, client_id, item_id, quantity)
                else:
                    reward["item_texts"].append("背包空间不足，战备战利品未能带走：" + str(reason).splitlines()[0])
            for gem_id, level, quantity in reward["gems"]:
                self.add_gem_conn(conn, client_id, gem_id, level, quantity)

            weapon_text = ""
            if reward["weapon"]:
                drop = reward["weapon"]
                weapon_id = self.weapon_core.create_weapon_conn(
                    conn,
                    client_id,
                    drop["weapon_def_id"],
                    drop["quality"],
                    drop["max_level"],
                    equipped=False,
                )
                weapon_text = f"获得武器 {weapon_id_label(weapon_id)} {drop['name']}[{drop['quality']}] 上限{drop['max_level']}"

            # --- 异火掉落判定 ---
            flame_result = flame_service.roll_boss_wormhole_flame(conn, client_id)
            flame_lines = []
            if flame_result.get("granted"):
                flame_lines.append(f"🔥 {flame_result['text']}")
            elif flame_result.get("compensation"):
                comp = flame_result["compensation"]
                flame_lines.append(f"🔥 {flame_service.compensation_text(comp['flame_name'], comp.get('reason', ''))}")

            lines = [
                f"虫洞奖励：{event['boss_name']}",
                f"结果：{event['status']}｜贡献：{reward['contribution']:.1%}，排名：{reward['rank']}",
                f"源石+{money(reward['stones'])}，经验+{reward['exp']}",
            ]
            meta = reward.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("event_type") == "war_prep":
                affixes = "、".join(meta.get("affixes") or []) or "无"
                lines.append(
                    f"战备来源：{meta.get('war_prep_name', '战备')}｜势力：{meta.get('force', '未知')}｜词条：{affixes}｜奖励倍率：{float(meta.get('reward_multiplier') or 1.0):.2f}x"
                )
                lines.append(f"异界法则：{meta.get('boss_flow', '异界法则')}")
                lines.append(f"定向奖励：{meta.get('reward_tendency', '战备定向奖励')}")
            elif isinstance(meta, dict) and meta.get("boss_flow"):
                lines.append(f"异界法则：{meta.get('boss_flow')}")
            if new_level > old_level:
                lines.append(f"等级提升：{old_level} → {new_level}")
            lines.extend(reward["item_texts"])
            if weapon_text:
                lines.append(weapon_text)
            lines.extend(flame_lines)
            text = "\n".join(lines)
            conn.execute(
                """
                UPDATE wormhole_participants
                SET reward_claimed = 1, reward_text = ?, updated_at = ?
                WHERE wormhole_id = ? AND client_id = ?
                """,
                (text, ts(), event["wormhole_id"], client_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '领取虫洞奖励', ?, ?)",
                (
                    client_id,
                    (
                        f"wormhole_id={event['wormhole_id']}, boss={event['boss_name']}, "
                        f"rank={reward['rank']}, exp={reward['exp']}, stones={reward['stones']}"
                    ),
                    ts(),
                ),
            )
            record_sect_merit_conn(
                conn,
                client_id,
                "influence",
                max(20, int(reward["contribution"] * 1000) + (80 if int(reward["rank"]) <= 3 else 0)),
                source="领取虫洞奖励",
                detail=f"wormhole_id={event['wormhole_id']}, boss={event['boss_name']}, rank={reward['rank']}",
            )
        return text

    def try_discover(self, client_id: str, source: str, location_name: str) -> str:
        """跑商动作后尝试发现虫洞；没有触发时返回空文本。"""

        self._close_expired_events()
        active = self._active_event()
        if active:
            return self.notice(client_id, active)

        snapshot = self._world_snapshot()
        opened_today = self._today_opened_count()
        daily_limit = self._daily_event_limit(snapshot["active_count"])
        if opened_today >= daily_limit:
            return ""

        war_prep = self.world_material.pending_war_prep()
        if war_prep:
            event = self._open_war_prep_event(client_id, source, war_prep)
            return self.notice(client_id, event, force=True)

        chance = self._discovery_chance(source, snapshot["active_count"], opened_today, daily_limit)
        if random.random() >= chance:
            return ""
        event = self._open_event(client_id, source, location_name)
        return self.notice(client_id, event, force=True)

    def notice(self, client_id: str, event: dict[str, Any] | None = None, force: bool = False) -> str:
        """给玩家追加虫洞提示；同一玩家有提示冷却，避免刷屏。"""

        event = event or self._active_event()
        if not event:
            return ""
        if not force:
            row = self.db.fetch_one(
                """
                SELECT last_notice_at
                FROM wormhole_notices
                WHERE wormhole_id = ? AND client_id = ?
                """,
                (event["wormhole_id"], client_id),
            )
            last = dt(row["last_notice_at"]) if row else None
            if last and now() - last < timedelta(minutes=WORMHOLE_NOTICE_COOLDOWN_MINUTES):
                return ""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO wormhole_notices (wormhole_id, client_id, last_notice_at)
                VALUES (?, ?, ?)
                ON CONFLICT(wormhole_id, client_id)
                DO UPDATE SET last_notice_at = excluded.last_notice_at
                """,
                (event["wormhole_id"], client_id, ts()),
            )
        meta = self._event_metadata(event)
        if meta.get("event_type") == "war_prep":
            text = (
                f"\n{meta.get('war_prep_name', '战备')}牵引虫洞："
                f"{event['boss_name']} 出现在 {event['location_name']}。"
            )
        else:
            text = f"\n异界虫洞撕开：{event['boss_name']} 出现在 {event['location_name']}。"
        return T.attach(text, f"发送：导航 {event['location_name']}，再发送：挑战虫洞" + f"<导航 {event['location_name']}><挑战虫洞>")

    def _open_event(self, opened_by: str, source: str, location_name: str) -> dict[str, Any]:
        """按当前服务器生态生成一只动态 Boss。"""

        point = self._location_point(location_name)
        snapshot = self._world_snapshot()
        boss_name, boss_kind, boss_factor = random.choice(BOSS_POOL)
        level = max(3, min(MAX_LEVEL, snapshot["median_level"] + random.randint(-3, 8)))
        median_attack = max(10, snapshot["median_attack"])
        median_hp = max(120, snapshot["median_hp"])
        defense = max(1, int(median_attack * random.uniform(0.3, 0.5) * boss_factor))
        attack = max(1, int((median_hp / 22 + level * 1.4) * boss_factor))
        one_damage = self._estimate_challenge_damage(median_attack, level, defense)
        expected_players = max(1, min(20, round(snapshot["active_count"] * 0.28)))
        expected_attempts = 4
        difficulty = random.uniform(0.9, 1.15) * boss_factor
        max_hp = max(320, int(one_damage * expected_players * expected_attempts * difficulty))
        opened_at = now()
        closes_at = opened_at + timedelta(minutes=WORMHOLE_DURATION_MINUTES)
        metadata = {"event_type": "normal", "source": source, "boss_flow": BOSS_FLOW_TEXT.get(boss_name, "异界法则")}

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO wormholes (
                    boss_name, boss_kind, location_name, x, y,
                    level, max_hp, hp, attack, defense, difficulty,
                    opened_by, source, status, opened_at, closes_at, result
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '开启', ?, ?, ?)
                """,
                (
                    boss_name,
                    boss_kind,
                    point["name"],
                    point["x"],
                    point["y"],
                    level,
                    max_hp,
                    max_hp,
                    attack,
                    defense,
                    difficulty,
                    opened_by,
                    source,
                    ts(opened_at),
                    ts(closes_at),
                    dump_json(metadata),
                ),
            )
            wormhole_id = int(cursor.lastrowid)
        event = self.db.fetch_one("SELECT * FROM wormholes WHERE wormhole_id = ?", (wormhole_id,))
        assert event is not None
        return event

    def _open_war_prep_event(self, opened_by: str, source: str, war_prep: dict[str, Any]) -> dict[str, Any]:
        """用特殊收购战备牵引一个定向虫洞。"""

        buyer_name = str(war_prep["buyer_name"])
        point = self._location_point(buyer_name)
        snapshot = self._world_snapshot()
        boss_name, boss_kind, boss_factor = random.choice(WAR_PREP_BOSS_POOL.get(buyer_name, BOSS_POOL))
        affix_count = random.choice((1, 1, 2))
        affixes = random.sample(WAR_PREP_AFFIXES, k=affix_count)
        level = max(5, min(MAX_LEVEL, snapshot["median_level"] + random.randint(0, 10)))
        median_attack = max(10, snapshot["median_attack"])
        median_hp = max(120, snapshot["median_hp"])
        defense = max(1, int(median_attack * random.uniform(0.35, 0.55) * boss_factor))
        attack = max(1, int((median_hp / 22 + level * 1.4) * boss_factor * 1.08))
        one_damage = self._estimate_challenge_damage(median_attack, level, defense)
        expected_players = max(1, min(24, round(snapshot["active_count"] * 0.32)))
        expected_attempts = 4
        difficulty = random.uniform(1.0, 1.18) * boss_factor
        hp_factor = 1.35 if "群敌聚形" in affixes else 1.15
        max_hp = max(420, int(one_damage * expected_players * expected_attempts * difficulty * hp_factor))
        reward_multiplier = 1.20
        if "群敌聚形" in affixes:
            reward_multiplier += 0.15
        if "裂口不稳" in affixes:
            reward_multiplier += 0.10
        reward_multiplier = min(1.50, reward_multiplier)
        duration_minutes = WORMHOLE_DURATION_MINUTES
        if "裂口不稳" in affixes:
            duration_minutes = max(20, int(duration_minutes * 0.7))
        opened_at = now()
        closes_at = opened_at + timedelta(minutes=duration_minutes)
        threshold = int(war_prep["threshold"])
        metadata = {
            "event_type": "war_prep",
            "force": buyer_name,
            "war_prep_name": str(war_prep["prep_name"]),
            "loot_subtype": str(war_prep["loot_subtype"]),
            "affixes": affixes,
            "boss_flow": self._war_prep_force_flow(buyer_name),
            "reward_multiplier": reward_multiplier,
            "reward_tendency": self._war_prep_reward_tendency(buyer_name),
            "war_prep_cost": threshold,
            "source": source,
        }

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO wormholes (
                    boss_name, boss_kind, location_name, x, y,
                    level, max_hp, hp, attack, defense, difficulty,
                    opened_by, source, status, opened_at, closes_at, result
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '开启', ?, ?, ?)
                """,
                (
                    boss_name,
                    boss_kind,
                    point["name"],
                    point["x"],
                    point["y"],
                    level,
                    max_hp,
                    max_hp,
                    attack,
                    defense,
                    difficulty,
                    opened_by,
                    "war_prep",
                    ts(opened_at),
                    ts(closes_at),
                    dump_json(metadata),
                ),
            )
            wormhole_id = int(cursor.lastrowid)
            self.world_material.consume_war_prep_conn(conn, buyer_name, threshold)
        event = self.db.fetch_one("SELECT * FROM wormholes WHERE wormhole_id = ?", (wormhole_id,))
        assert event is not None
        return event

    def _active_event(self) -> dict[str, Any] | None:
        """读取当前仍开启的虫洞。"""

        self._close_expired_events()
        row = self.db.fetch_one("""
            SELECT *
            FROM wormholes
            WHERE status = '开启'
            ORDER BY opened_at DESC
            LIMIT 1
            """)
        return row

    def _latest_event(self) -> dict[str, Any] | None:
        """读取最近一条虫洞记录。"""

        return self.db.fetch_one("SELECT * FROM wormholes ORDER BY opened_at DESC LIMIT 1")

    def _latest_rewardable(self, client_id: str) -> dict[str, Any] | None:
        """读取玩家最近可领奖的虫洞。"""

        return self.db.fetch_one(
            """
            SELECT w.*
            FROM wormholes w
            JOIN wormhole_participants p ON p.wormhole_id = w.wormhole_id
            WHERE p.client_id = ?
              AND p.reward_claimed = 0
              AND w.status IN ('已击杀', '已退去')
            ORDER BY w.opened_at DESC
            LIMIT 1
            """,
            (client_id,),
        )

    def _close_expired_events(self) -> None:
        """超过持续时间后，未击杀的虫洞会退去。"""

        rows = self.db.fetch_all(
            """
            SELECT *
            FROM wormholes
            WHERE status = '开启' AND closes_at <= ?
            """,
            (ts(),),
        )
        if not rows:
            return
        with self.db.transaction() as conn:
            for row in rows:
                result_meta = self._event_metadata(row)
                result_meta["reason"] = "timeout"
                conn.execute(
                    """
                    UPDATE wormholes
                    SET status = '已退去', result = ?
                    WHERE wormhole_id = ?
                    """,
                    (dump_json(result_meta), row["wormhole_id"]),
                )

    def _challenge_check(self, wormhole_id: int, client_id: str) -> str:
        """检查挑战冷却。"""

        row = self.db.fetch_one(
            """
            SELECT last_challenge_at
            FROM wormhole_participants
            WHERE wormhole_id = ? AND client_id = ?
            """,
            (wormhole_id, client_id),
        )
        if not row:
            return ""
        last = dt(row["last_challenge_at"])
        if not last:
            return ""
        left = timedelta(minutes=WORMHOLE_CHALLENGE_COOLDOWN_MINUTES) - (now() - last)
        if left <= timedelta():
            return ""
        seconds = max(1, int(left.total_seconds()))
        return T.hint(f"挑战虫洞冷却中，还需 {seconds // 60}分{seconds % 60}秒。", "<挑战虫洞>")

    @staticmethod
    def _busy_challenge_hint(status: str) -> str:
        """玩家本体忙碌时，解释为什么不能挑战虫洞。"""

        if status == "探险中":
            return T.hint(
                "本体正在探险，不能挑战虫洞。",
                "行商化身仍可跑商；先发送：探险状态，30 分钟后发送：结束探险，再发送：挑战虫洞",
            )
        return T.hint(f"当前状态为 {status}，不能挑战虫洞。", "先结束当前状态，再发送：挑战虫洞")

    def _fight_boss(self, player: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        """结算一次虫洞挑战；只算数值和逐次出手日志，不写数据库。"""

        action_limit = 10 + min(4, int(player["level"]) // 25)
        return combat_service.fight_boss(
            player,
            event,
            boss_kind=str(event["boss_kind"]),
            action_limit=action_limit,
        )

    def _challenge_log_block(
        self,
        *,
        title: str,
        boss_name: str,
        player: dict[str, Any],
        result: dict[str, Any],
        damage: int,
        left_hp: int,
        max_hp: int,
        killed: bool,
        killed_text: str,
        alive_text: str,
        hurt_text: str,
        reward_command: str,
        challenge_command: str,
        log_kind: str = "",
        record_id: int = 0,
    ) -> str | dict:
        """按玩家设置返回 Boss 挑战短摘要，并附带战斗日志链接。"""

        return combat_log_text.boss_brief(
            title=title,
            boss_name=boss_name,
            boss_label="Boss",
            player=player,
            result=result,
            damage=damage,
            left_hp=left_hp,
            max_hp=max_hp,
            killed=killed,
            killed_text=killed_text,
            alive_text=alive_text,
            hurt_text=hurt_text,
            log_kind=log_kind,
            record_id=record_id,
            client_id=str(player["client_id"]),
            detail=combat_log_text.wants_detail(player),
        )

    def _roll_reward(self, event: dict[str, Any], participant: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
        """按贡献、排名和是否击杀生成奖励。"""

        rows = self._participants(event["wormhole_id"])
        rank = next((index for index, row in enumerate(rows, start=1) if row["client_id"] == player["client_id"]), len(rows))
        contribution = self._contribution(int(participant["damage"]), event)
        killed_factor = 1.0 if event["status"] == "已击杀" else 0.55
        rank_factor = {1: 1.15, 2: 1.06, 3: 1.0}.get(rank, 0.9)
        meta = self._event_metadata(event)
        reward_multiplier = float(meta.get("reward_multiplier") or 1.0)
        influence_bonus = self._public_battle_reward_bonus(str(player["client_id"]))
        stones = max(
            1,
            int(
                (int(event["level"]) * 650 + int(event["max_hp"]) * 0.012)
                * killed_factor
                * (0.45 + contribution * 2.2)
                * rank_factor
                * reward_multiplier
            ),
        )
        exp = max(1, int(monster_exp(event["level"], 1.45, player["level"]) * killed_factor * (0.55 + contribution * 1.6) * reward_multiplier))
        ring_items: list[tuple[str, int]] = []
        backpack_items: list[tuple[str, int]] = []
        gems: list[tuple[str, int, int]] = []
        item_texts: list[str] = []

        recover = self._random_equipment_item("恢复类")
        if recover:
            ring_items.append((recover["ring_item_id"], 1))
            item_texts.append(f"纳戒获得 {recover['name']} x1")

        affixes = set(meta.get("affixes") if isinstance(meta.get("affixes"), list) else [])
        if random.random() < WORMHOLE_LOW_CONTRIBUTION_FLOORS["xisuiye"] + contribution * 0.25 + influence_bonus * 0.4 + (0.04 if "旧门半开" in affixes else 0):
            special = self.ring_item_def("xisuiye")
            if special:
                ring_items.append((special["ring_item_id"], 1))
                item_texts.append(f"纳戒获得 {special['name']} x1")

        gem = self._random_equipment_item("宝石")
        if gem and random.random() < WORMHOLE_LOW_CONTRIBUTION_FLOORS["gem"] + contribution * 0.3:
            level = 1 + (1 if random.random() < min(0.22, contribution) else 0)
            gems.append((gem["ring_item_id"], level, 1))
            item_texts.append(f"宝石获得 {gem['name']} {level}级 x1")

        book = self._random_war_prep_book(meta) if meta.get("event_type") == "war_prep" else self._random_equipment_item("技能书")
        if book and random.random() < WORMHOLE_LOW_CONTRIBUTION_FLOORS["book"] + contribution * 0.22 + influence_bonus * 0.6 + (0.04 if "余烬未冷" in affixes else 0):
            book_bonus = 0.010 if meta.get("event_type") == "war_prep" else 0.008
            book = self.maybe_upgrade_extreme_book_item(book, str(event["location_name"]), book_bonus)
            ring_items.append((book["ring_item_id"], 1))
            item_texts.append(f"纳戒获得 {book['name']} x1")

        weapon = None
        if random.random() < WORMHOLE_LOW_CONTRIBUTION_FLOORS["weapon"] + contribution * 0.16 + influence_bonus * 0.4 + (0.04 if "战痕回涌" in affixes else 0):
            weapon = self._random_war_prep_weapon(meta) if meta.get("event_type") == "war_prep" else self.weapon_core.roll_weapon_drop()

        loot_subtype = str(meta.get("loot_subtype") or "")
        loot_floor = 0.18 if meta.get("event_type") == "war_prep" else 0.12
        if loot_subtype and random.random() < loot_floor + contribution * 0.18 + influence_bonus * 0.5 + (0.08 if "残巢已露" in affixes else 0):
            loot = self._random_world_loot(loot_subtype)
            if loot:
                backpack_items.append((loot["item_id"], 1))
                item_texts.append(f"背包获得 {loot['name']} x1")

        return {
            "rank": rank,
            "contribution": contribution,
            "stones": stones,
            "exp": exp,
            "ring_items": ring_items,
            "backpack_items": backpack_items,
            "gems": gems,
            "weapon": weapon,
            "item_texts": item_texts,
            "metadata": meta,
            "influence_bonus": influence_bonus,
        }

    def _random_equipment_item(self, category: str) -> dict[str, Any] | None:
        """随机一个纳戒物品；排除只属于岁时情劫的产物。"""

        rows = self.db.fetch_all(
            """
            SELECT *
            FROM ring_item_defs
            WHERE category = ?
              AND ring_item_id != 'kaikongqi'
              AND ring_item_id != 'cuifengdan'
              AND ring_item_id NOT LIKE 'extreme_%'
            """,
            (category,),
        )
        return random.choice(rows) if rows else None

    def _public_battle_reward_bonus(self, client_id: str) -> float:
        """宗门影响力给虫洞珍贵掉落小幅修正，不影响源石和经验。"""

        with self.db.transaction() as conn:
            return min(0.08, max(0.0, sect_direction_bonus_conn(conn, client_id, "influence") * 0.12))

    def _random_war_prep_book(self, meta: dict[str, Any]) -> dict[str, Any] | None:
        """按战备势力偏向抽技能书；无匹配时退回普通技能书池。"""

        force = str(meta.get("force") or "")
        profile = WAR_PREP_REWARD_PROFILES.get(force, {})
        preferred_effects = set(profile.get("book_effects") or ())
        rows = self.db.fetch_all(
            """
            SELECT *
            FROM ring_item_defs
            WHERE category = '技能书'
              AND ring_item_id NOT LIKE 'extreme_%'
            """
        )
        if not rows:
            return None
        if not preferred_effects:
            return random.choice(rows)
        preferred = []
        for row in rows:
            enchant_effect = load_json(row.get("effect"), {})
            enchant = self.db.fetch_one(
                "SELECT effect FROM weapon_enchants WHERE enchant_id = ?",
                (str(enchant_effect.get("enchant_id") or ""),),
            )
            effects = load_json(enchant.get("effect") if enchant else "{}", {})
            if preferred_effects.intersection(effects.keys()):
                preferred.append(row)
        pool = preferred or rows
        return random.choice(pool)

    def _random_war_prep_weapon(self, meta: dict[str, Any]) -> dict[str, Any]:
        """按战备势力偏向抽武器，并给特定势力小概率高上限。"""

        force = str(meta.get("force") or "")
        profile = WAR_PREP_REWARD_PROFILES.get(force, {})
        preferred_types = set(profile.get("weapon_types") or ())
        rows = self.db.fetch_all("SELECT * FROM weapon_defs")
        if preferred_types:
            preferred = [row for row in rows if str(row.get("weapon_type") or "") in preferred_types]
            rows = preferred or rows
        weapon_def = random.choice(rows)
        max_level = self.weapon_core.random_max_level()
        floor_chance = float(profile.get("max_level_floor_chance") or 0.0)
        if floor_chance > 0 and random.random() < floor_chance:
            max_level = max(max_level, int(profile.get("max_level_floor") or 80))
        return {
            "weapon_def_id": weapon_def["weapon_def_id"],
            "name": weapon_def["name"],
            "quality": random_quality(),
            "max_level": max_level,
        }

    def _participants(self, wormhole_id: int) -> list[dict[str, Any]]:
        """读取虫洞贡献排行。"""

        return self.db.fetch_all(
            """
            SELECT *
            FROM wormhole_participants
            WHERE wormhole_id = ?
            ORDER BY damage DESC, updated_at ASC
            """,
            (wormhole_id,),
        )

    def _contribution(self, damage: int, event: dict[str, Any]) -> float:
        """计算个人伤害占 Boss 总血量的比例。"""

        max_hp = int(event.get("max_hp") or 0)
        if max_hp <= 0:
            return 0.0
        return max(0.0, min(1.0, int(damage) / max_hp))

    def _location_point(self, name: str) -> dict[str, Any]:
        """把命名世界点位转成坐标。"""

        clean = name.strip()
        row = self.db.fetch_one("SELECT name, x, y FROM world_locations WHERE name = ?", (clean,))
        if row:
            return row
        return {"name": clean or "天枢城", "x": 0, "y": 0}

    def _world_snapshot(self) -> dict[str, int]:
        """读取玩家生态，用来动态缩放虫洞强度。"""

        active_players = self._active_players()
        players = active_players or self.db.fetch_all("SELECT * FROM players")
        if not players:
            return {"active_count": 1, "median_level": 1, "median_attack": 12, "median_hp": 120}
        levels = sorted(max(1, int(row["level"])) for row in players)
        attacks = []
        hps = []
        for row in players:
            weapon = self.equipped_weapon_row(str(row["client_id"]))
            flame_mult = self.equipped_flame_multiplier(str(row["client_id"]))
            attacks.append(max(1, math.floor((int(row["base_attack"]) + self.weapon_attack(weapon)) * flame_mult)))
            hps.append(max(1, int(row["max_hp"])))
        return {
            "active_count": max(1, len(active_players)),
            "median_level": int(median(levels)),
            "median_attack": int(median(attacks)),
            "median_hp": int(median(hps)),
        }

    def _active_players(self) -> list[dict[str, Any]]:
        """读取近期活跃玩家。

        近 7 天内创建、跑商、探险、挑战或对战，都算活跃。
        这个人数只用于虫洞刷新和动态难度，不改变玩家数据。
        """

        cutoff = ts(now() - timedelta(days=WORMHOLE_ACTIVE_WINDOW_DAYS))
        return self.db.fetch_all(
            """
            SELECT *
            FROM players p
            WHERE p.created_at >= ?
               OR EXISTS (SELECT 1 FROM game_logs g WHERE g.client_id = p.client_id AND g.created_at >= ?)
               OR EXISTS (SELECT 1 FROM trade_records t WHERE t.client_id = p.client_id AND t.created_at >= ?)
               OR EXISTS (SELECT 1 FROM exploration_records e WHERE e.client_id = p.client_id AND e.started_at >= ?)
               OR EXISTS (SELECT 1 FROM wormhole_participants wp WHERE wp.client_id = p.client_id AND wp.updated_at >= ?)
               OR EXISTS (SELECT 1 FROM seasonal_boss_participants sp WHERE sp.client_id = p.client_id AND sp.updated_at >= ?)
               OR EXISTS (SELECT 1 FROM duel_records d WHERE (d.from_client_id = p.client_id OR d.to_client_id = p.client_id) AND d.created_at >= ?)
               OR EXISTS (SELECT 1 FROM combat_logs c WHERE c.client_id = p.client_id AND c.created_at >= ?)
            """,
            (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff),
        )

    def _today_opened_count(self) -> int:
        """统计今日已经生成过多少次虫洞。

        这里按修仙统一的 04:00 业务日计算，已击杀和已退去也计入当天次数。
        """

        rows = self.db.fetch_all(
            "SELECT opened_at FROM wormholes WHERE opened_at >= ?",
            (ts(now() - timedelta(days=2)),),
        )
        today = business_day()
        count = 0
        for row in rows:
            opened = dt(row["opened_at"])
            if opened and business_day(opened) == today:
                count += 1
        return count

    @staticmethod
    def _daily_event_limit(active_count: int) -> int:
        """按活跃人数计算每日虫洞生成上限。

        活跃人数只负责把上限往上抬；最低下限始终保留，
        避免低活跃日完全没有虫洞可打。
        """

        active = max(1, int(active_count))
        extra = (active - 1) // WORMHOLE_DAILY_ACTIVE_PLAYER_STEP
        dynamic_limit = WORMHOLE_DAILY_BASE_LIMIT + extra
        capped_limit = min(WORMHOLE_DAILY_MAX_LIMIT, dynamic_limit)
        return max(WORMHOLE_DAILY_MIN_LIMIT, capped_limit)

    @staticmethod
    def _discovery_chance(source: str, active_count: int, opened_today: int, daily_limit: int) -> float:
        """按活跃人数和今日剩余名额微调发现概率。"""

        base = DISCOVERY_CHANCES.get(source, 0.0)
        if base <= 0:
            return 0.0
        active_factor = 1.0 + min(1.2, max(0, int(active_count) - 1) * 0.08)
        remaining = max(0, int(daily_limit) - int(opened_today))
        remaining_factor = 1.0 + min(1.0, remaining / max(1, int(daily_limit)))
        return min(0.18, base * active_factor * remaining_factor)

    @staticmethod
    def _estimate_challenge_damage(median_attack: int, level: int, boss_defense: int) -> int:
        """估算中位玩家单次挑战伤害。"""

        per_round = damage_after_defense(int(median_attack * 1.25 + level * 2.0), boss_defense)
        return max(25, per_round * 10)

    def _format_status(self, event: dict[str, Any]) -> str:
        """格式化虫洞状态。"""

        closes = dt(event["closes_at"])
        left = max(0, int((closes - now()).total_seconds() // 60) + 1) if closes else 0
        snapshot = self._world_snapshot()
        opened_today = self._today_opened_count()
        daily_limit = self._daily_event_limit(snapshot["active_count"])
        meta = self._event_metadata(event)
        panel = T.panel()
        title = "异界虫洞"
        if meta.get("event_type") == "war_prep":
            title = f"战备虫洞·{meta.get('war_prep_name', '战备')}"
        panel.section(f"{title}·{event['boss_name']}")
        panel.line(f"位置：{event['location_name']} ({event['x']},{event['y']})")
        panel.line(f"等级：**{event['level']}**｜血量：**{event['hp']}/{event['max_hp']}**｜状态：{event['status']}")
        if meta.get("event_type") == "war_prep":
            affixes = "、".join(meta.get("affixes") or []) or "无"
            panel.line(f"势力：{meta.get('force')}｜词条：{affixes}｜奖励倍率：{float(meta.get('reward_multiplier') or 1.0):.2f}x")
            panel.line(f"异界法则：{meta.get('boss_flow', '异界法则')}")
            panel.line(f"奖励倾向：{meta.get('reward_tendency', '战备定向奖励')}")
        elif meta.get("boss_flow"):
            panel.line(f"异界法则：{meta.get('boss_flow')}")
        panel.line(f"今日出现：**{opened_today}/{daily_limit}**｜近{WORMHOLE_ACTIVE_WINDOW_DAYS}天活跃：**{snapshot['active_count']}**人")
        panel.line(f"剩余约 **{left}** 分钟｜挑战冷却 {WORMHOLE_CHALLENGE_COOLDOWN_MINUTES} 分钟")
        text = panel.render()
        return T.attach(text, f"发送：导航 {event['location_name']}，到达后发送：挑战虫洞")

    @staticmethod
    def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
        """读取虫洞 result 中的结构化 metadata。"""

        try:
            raw_result = event.get("result")  # type: ignore[attr-defined]
        except AttributeError:
            raw_result = event["result"]
        meta = load_json(raw_result, {})
        return meta if isinstance(meta, dict) else {}

    @staticmethod
    def _war_prep_reward_tendency(buyer_name: str) -> str:
        """战备虫洞奖励倾向说明。"""

        return {
            "镇妖司": "妖类战利品、轻灵或斩妖风格武器",
            "伏魔殿": "魔类战利品、破防或镇魔技能书",
            "鬼市": "鬼类战利品、精神压制或扰乱技能书",
            "龙渊阁": "龙类战利品、高上限武器小概率",
            "万兽盟": "兽类战利品、体修或重型武器",
            "破军营": "兵戈类战利品、战场武器、攻击型技能书",
        }.get(buyer_name, "战备定向奖励")

    @staticmethod
    def _war_prep_force_flow(buyer_name: str) -> str:
        """战备势力牵引出的异界 Boss 必须落回本世界战斗流派。"""

        return {
            "镇妖司": "高频连击",
            "伏魔殿": "重击破防",
            "鬼市": "压制控制",
            "龙渊阁": "首领协作",
            "万兽盟": "生存续航",
            "破军营": "斩杀收割",
        }.get(buyer_name, "异界法则")

    def _random_world_loot(self, loot_subtype: str) -> dict[str, Any] | None:
        """按战利品小类抽一个背包掉落。"""

        rows = self.db.fetch_all(
            """
            SELECT *
            FROM item_defs
            WHERE category = '战利品'
            """
        )
        filtered = []
        for row in rows:
            effect = load_json(row.get("effect"), {})
            if str(effect.get("world_subtype") or "") == loot_subtype:
                filtered.append(row)
        pool = filtered or rows
        return random.choice(pool) if pool else None


service = WormholeService(db)

__all__ = ["BOSS_POOL", "DISCOVERY_CHANCES", "WormholeService", "service"]
