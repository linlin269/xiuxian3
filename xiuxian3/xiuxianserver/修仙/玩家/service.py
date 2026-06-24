"""玩家组件服务。"""

from __future__ import annotations

from ..combat_log_text import mode_text
from ..format_text import T

from ..common import (
    CoreService,
    business_day,
    dt,
    enchant_label_name,
    fixed_equipment_label,
    format_effect,
    load_json,
    money,
    now,
    timedelta,
    ts,
    weapon_id_label,
    weapon_label_name,
    world_state_for_day,
)
from ..constants import EQUIPMENT_SLOTS, NEWBIE_GIFT_STONES, REST_FAST_SECONDS, REST_FULL_MINUTES
from ..rules import rest_recovery_rate, sign_reward
from ..sect_war import sect_direction_bonus_conn
from ..sql import db


class PlayerService(CoreService):
    """玩家创建、资料、签到和休息。"""

    def create(self, client_id: str, message: str) -> str:
        """创建用户。"""

        name = message.strip()
        if not name:
            return T.hint("缺少用户名称。", "发送：创建用户 青衫客")
        return self.create_player(client_id, name)

    def rename(self, client_id: str, message: str) -> str:
        """修改展示名称。"""

        name = message.strip()
        if not name:
            return T.hint("缺少新名称。", "发送：改名 云游客")
        return self.rename_player(client_id, name)

    def profile(self, client_id: str) -> str:
        """查看玩家信息。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        self.ensure_player_weapon(client_id)
        player = self.recalc_player(client_id)
        weapon = self.equipped_weapon_row(client_id)
        weapon_attack = self.weapon_attack(weapon)
        total_attack = int(player["base_attack"]) + weapon_attack
        combat_info = self._current_combat_info(client_id, player, weapon)
        self.refresh_titles(client_id, player)

        panel = T.panel()
        panel.section("状态")
        panel.line(f"经验：**{self.next_level_text(player)}**")
        panel.line(f"血气：**{player['hp']}/{player['max_hp']}**｜精神：**{player['mp']}/{player['max_mp']}**")
        panel.line(f"源石：**{money(player['source_stones'])}**｜状态：{player['status']}｜自动用药：{'开启' if player['auto_use_medicine'] else '关闭'}")
        panel.line(f"战斗日志：{mode_text(player)}")
        nemesis_text = self._nemesis_text(client_id)
        if nemesis_text:
            panel.line(f"死敌：{nemesis_text}")
        panel.line(f"地点：{player['location_name']} ({player['x']},{player['y']})")
        panel.hr()
        panel.section("战力")
        panel.line(f"攻击：**{total_attack}**（基础 {player['base_attack']} + 武器 {weapon_attack}）")
        flame_multiplier = self.equipped_flame_multiplier(client_id)
        flame_name = self.equipped_flame_name(client_id)
        if flame_multiplier > 1.0:
            import math
            final = max(1, math.floor(total_attack * flame_multiplier))
            panel.line(f"异火：{flame_name} x{flame_multiplier:.3f}｜最终攻击 **{final}**")
        else:
            panel.line("异火：未装备")
        panel.line(f"防御：**{player['defense']}**")
        panel.line(f"速度：**{combat_info['speed']}**（{combat_info['speed_grade']}）")
        panel.line(f"技能节奏：{combat_info['skill_tempo']}")
        panel.line(f"蓄势基准：{combat_info['skill_interval']}（越小越快）")
        panel.hr()
        panel.section("体质")
        panel.lines(self._physique_profile_lines(player))
        panel.hr()
        panel.section("武器")
        panel.lines(self._weapon_profile_lines(weapon, combat_info))
        panel.hr()
        panel.section("装备")
        panel.lines(self._fixed_equipment_lines(client_id))
        panel.hr()
        panel.section("坐骑")
        panel.lines(self._mount_profile_lines(client_id, player))
        panel.hr()
        panel.section("今日加成")
        panel.lines(self._daily_bonus_lines(client_id))
        return panel.render() + T.buttons("休息", "结束休息", "地图")

    def status(self, client_id: str) -> str:
        """查看玩家关键状态。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        self.ensure_player_weapon(client_id)
        player = self.recalc_player(client_id)
        weapon = self.equipped_weapon_row(client_id)
        weapon_attack = self.weapon_attack(weapon)
        total_attack = int(player["base_attack"]) + weapon_attack
        combat_info = self._current_combat_info(client_id, player, weapon)
        self.refresh_titles(client_id, player)

        weapon_text = "未装备"
        if weapon:
            weapon_text = f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)} [{weapon['quality']}] 攻击 +{weapon_attack}"

        panel = T.panel()
        panel.section("关键状态")
        panel.line(f"等级：{player['level']}｜经验：{self.next_level_text(player)}")
        panel.line(f"血气：{player['hp']}/{player['max_hp']}｜精神：{player['mp']}/{player['max_mp']}")
        panel.line(f"状态：{player['status']}｜地点：{player['location_name']} ({player['x']},{player['y']})")
        panel.line(f"源石：{money(player['source_stones'])}")
        panel.hr()
        panel.line(f"攻击：{total_attack}｜防御：{player['defense']}｜速度：{combat_info['speed']}")
        panel.line(f"技能节奏：{combat_info['skill_tempo']}")
        panel.line(f"当前武器：{weapon_text}")
        panel.hr()
        panel.line(f"自动用药：{'开启' if player['auto_use_medicine'] else '关闭'}｜战斗日志：{mode_text(player)}")
        panel.line(f"今日加成：{self._daily_bonus_total_text(client_id)}")
        nemesis_text = self._nemesis_text(client_id)
        if nemesis_text:
            panel.line(f"死敌：{nemesis_text}")
        return panel.render() + T.buttons("修仙信息", "休息", "地图")

    def diary(self, client_id: str) -> str:
        """查看个人修仙日记。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        self._refresh_diary(client_id, player)
        rows = self.db.fetch_all(
            """
            SELECT text
            FROM player_journals
            WHERE client_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (client_id,),
        )
        if not rows:
            return T.hint("修仙日记还没有内容。", "先签到、探险、跑商或挑战虫洞。<签到><探险><虫洞><商场推荐>")
        panel = T.panel()
        panel.section("修仙日记")
        for row in rows:
            panel.line(f"- {row['text']}")
        return panel.render()

    def auto_medicine(self, client_id: str, message: str) -> str:
        """查看或修改探险自动用药开关。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        text = message.strip()
        if not text:
            state = "开启" if player["auto_use_medicine"] else "关闭"
            panel = T.panel()
            panel.section("自动用药")
            panel.line(f"当前状态：**{state}**")
            panel.line("开启后，探险预计算会自动消耗纳戒恢复类药物。")
            return panel.render()

        on_words = {"开启", "打开", "启用", "开", "on", "ON", "1"}
        off_words = {"关闭", "关掉", "停用", "关", "off", "OFF", "0"}
        if text in on_words:
            value = 1
            state = "开启"
        elif text in off_words:
            value = 0
            state = "关闭"
        else:
            return T.hint("自动用药参数不正确。", "发送：自动用药 开启 或 自动用药 关闭")

        with self.db.transaction() as conn:
            conn.execute("UPDATE players SET auto_use_medicine = ? WHERE client_id = ?", (value, client_id))
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '自动用药', ?, ?)",
                (client_id, state, ts()),
            )
        return f"自动用药已{state}。探险预计算时会按这个开关决定是否消耗纳戒恢复类药物。"

    def battle_log(self, client_id: str, message: str) -> str:
        """查看或修改战斗日志展示模式。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        text = message.strip()
        if not text:
            panel = T.panel()
            panel.section("战斗日志")
            panel.line(f"当前模式：**{mode_text(player)}**")
            panel.line("开启后网页战斗日志默认展开逐回合明细，关闭后默认只看摘要；群消息始终保持短结算。")
            return panel.render()

        on_words = {"开启", "打开", "启用", "开", "on", "ON", "1"}
        off_words = {"关闭", "关掉", "停用", "关", "off", "OFF", "0"}
        if text in on_words:
            value = 1
            state = "详细"
        elif text in off_words:
            value = 0
            state = "简要"
        else:
            return T.hint("战斗日志参数不正确。", "发送：战斗日志 开启 或 战斗日志 关闭")

        with self.db.transaction() as conn:
            conn.execute("UPDATE players SET battle_log_detail = ? WHERE client_id = ?", (value, client_id))
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '战斗日志', ?, ?)",
                (client_id, state, ts()),
            )
        return f"战斗日志已切换为{state}；后续群消息仍保持短结算，网页战斗日志按当前模式默认展开。"

    def sign(self, client_id: str) -> str:
        """每日签到。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        today = business_day()
        reward = sign_reward(player["level"])
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE players
                SET source_stones = source_stones + ?, last_sign_date = ?
                WHERE client_id = ?
                  AND (last_sign_date IS NULL OR last_sign_date != ?)
                """,
                (reward, today, client_id, today),
            )
            if cursor.rowcount <= 0:
                fortune = self.ensure_daily_fortune_conn(conn, client_id)
                suggestion = (
                    f"今日气运：{fortune['fortune']}，{fortune['flavor']}"
                    f"（{format_effect(fortune['effect'])}）\n"
                    "每日 04:00 后可再次发送：签到"
                )
                return T.hint("今日已经签到过了。", suggestion)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '签到', ?, ?)",
                (client_id, f"stones={reward}, day={today}", ts()),
            )
            fortune = self.ensure_daily_fortune_conn(conn, client_id)
        return f"签到成功，获得源石 {money(reward)}。\n" f"今日气运：{fortune['fortune']}，{fortune['flavor']}" f"（{format_effect(fortune['effect'])}）"

    def newbie_gift(self, client_id: str) -> str:
        """领取新手礼包。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE players
                SET newbie_claimed = 1, source_stones = source_stones + ?
                WHERE client_id = ? AND newbie_claimed = 0
                """,
                (NEWBIE_GIFT_STONES, client_id),
            )
            if cursor.rowcount <= 0:
                return T.hint("新手礼包已经领取过了。", "发送：纳戒 查看礼包物品，或发送：探险 开始升级。<纳戒><探险>")
            self.add_ring_conn(conn, client_id, "xueqidan", 2)
            self.add_ring_conn(conn, client_id, "yinmingcao", 2)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '新手礼包', ?, datetime('now', 'localtime'))",
                (client_id, "领取"),
            )
        return "新手礼包领取成功：源石 10000、血契丹 2、阴冥草 2。"

    def rest(self, client_id: str) -> str:
        """进入休息状态。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        if player["status"] != "空闲":
            return T.hint(f"当前状态为 {player['status']}，不能休息。", "先处理当前状态<休息>")

        current = now()
        window = self._rest_window_state(player)
        until = current + timedelta(seconds=window["remaining_seconds"])
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE players
                SET status = '休息中',
                    rest_full_at = ?,
                    rest_window_started_at = ?,
                    rest_window_hp = ?,
                    rest_window_mp = ?,
                    rest_window_elapsed_seconds = ?
                WHERE client_id = ? AND status = '空闲'
                """,
                (
                    ts(until),
                    window["started_at"],
                    window["base_hp"],
                    window["base_mp"],
                    window["elapsed_seconds"],
                    client_id,
                ),
            )
            if cursor.rowcount <= 0:
                return T.hint("当前状态已变化，不能休息。", "发送：修仙信息 查看当前状态后再操作。<修仙信息><休息>")
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '开始休息', ?, ?)",
                (client_id, f"full_at={ts(until)}, window_elapsed={window['elapsed_seconds']}", ts()),
            )
        return f"开始休息，满 1 分钟可结算约一半，{REST_FULL_MINUTES} 分钟恢复满；到时发送：结束休息。<结束休息>"

    def end_rest(self, client_id: str) -> str:
        """按已休息时长恢复并退出。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        if player["status"] != "休息中":
            return T.hint("你当前不在休息中。", "血气不足可发送：休息；想查看状态可发送：修仙信息<修仙信息><休息>")

        current = now()
        with self.db.transaction() as conn:
            fresh = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
            if not fresh:
                return T.hint("你还没有创建用户。", "发送：创建用户 你的名字")
            if fresh["status"] != "休息中":
                return T.hint("当前状态已变化，不能结束休息。", "发送：修仙信息 查看当前状态。<修仙信息><休息>")

            full_at = dt(fresh["rest_full_at"]) or current
            window = self._rest_window_state(dict(fresh))
            active_started_at = full_at - timedelta(seconds=window["remaining_seconds"])
            active_seconds = max(0, int((current - active_started_at).total_seconds()))
            if active_seconds < REST_FAST_SECONDS:
                left = max(1, REST_FAST_SECONDS - active_seconds)
                return T.hint(f"至少需要休息 {REST_FAST_SECONDS} 秒，还差 {left} 秒。", "满 1 分钟后再发送：结束休息")

            elapsed_seconds = min(REST_FULL_MINUTES * 60, window["elapsed_seconds"] + active_seconds)
            base_rate = rest_recovery_rate(elapsed_seconds)
            recover_bonus = float(self.equipment_bonuses_conn(conn, client_id).get("recover_bonus", 0))
            sect_recover_bonus = min(0.12, sect_direction_bonus_conn(conn, client_id, "support") * 0.15)
            recover_bonus += sect_recover_bonus
            recover_multiplier = max(0.0, 1 + recover_bonus)
            recover_rate = max(0.0, min(1.0, base_rate * recover_multiplier))
            current_hp = int(fresh["hp"])
            current_mp = int(fresh["mp"])
            max_hp = int(fresh["max_hp"])
            max_mp = int(fresh["max_mp"])
            base_hp = self._rest_recover_value(window["base_hp"], current_hp, max_hp, base_rate)
            base_mp = self._rest_recover_value(window["base_mp"], current_mp, max_mp, base_rate)
            hp = self._rest_recover_value(window["base_hp"], current_hp, max_hp, recover_rate)
            mp = self._rest_recover_value(window["base_mp"], current_mp, max_mp, recover_rate)
            actual_rate = self._actual_rest_recovery_rate(current_hp, current_mp, hp, mp, max_hp, max_mp)
            base_actual_rate = self._actual_rest_recovery_rate(current_hp, current_mp, base_hp, base_mp, max_hp, max_mp)
            cursor = conn.execute(
                """
                UPDATE players
                SET hp = ?,
                    mp = ?,
                    status = '空闲',
                    rest_full_at = NULL,
                    rest_window_hp = ?,
                    rest_window_mp = ?,
                    rest_window_elapsed_seconds = ?
                WHERE client_id = ? AND status = '休息中'
                """,
                (hp, mp, window["base_hp"], window["base_mp"], elapsed_seconds, client_id),
            )
            if cursor.rowcount <= 0:
                return T.hint("当前状态已变化，不能结束休息。", "发送：修仙信息 查看当前状态。<修仙信息><休息>")
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '结束休息', ?, ?)",
                (
                    client_id,
                    (
                        f"active={active_seconds}, elapsed={elapsed_seconds}, "
                        f"base_rate={base_rate:.3f}, recover_bonus={recover_bonus:.3f}, sect_recover_bonus={sect_recover_bonus:.3f}, "
                        f"recover_multiplier={recover_multiplier:.3f}, recover_rate={recover_rate:.3f}, "
                        f"base_actual_rate={base_actual_rate:.3f}, actual_rate={actual_rate:.3f}, hp={hp}, mp={mp}"
                    ),
                    ts(),
                ),
        )
        rate_text = round(actual_rate * 100)
        bonus_part = self._rest_recover_bonus_text(recover_multiplier)
        return (
            f"休息结束，已休息 {self._rest_time_text(elapsed_seconds)}，恢复效率 **{rate_text}%**{bonus_part}。"
            f"血气恢复到 **{hp}**/{max_hp}，精神恢复到 **{mp}**/{max_mp}。<休息>"
        )

    def _rest_window_state(self, player: dict) -> dict[str, int | str]:
        """读取或初始化当前休息恢复窗口。"""

        full_seconds = REST_FULL_MINUTES * 60
        elapsed = max(0, min(full_seconds, int(player.get("rest_window_elapsed_seconds") or 0)))
        started_at = str(player.get("rest_window_started_at") or "")
        if not started_at or elapsed >= full_seconds:
            elapsed = 0
            started_at = ts()
            base_hp = int(player["hp"])
            base_mp = int(player["mp"])
        else:
            base_hp_value = player.get("rest_window_hp")
            base_mp_value = player.get("rest_window_mp")
            base_hp = int(player["hp"] if base_hp_value is None else base_hp_value)
            base_mp = int(player["mp"] if base_mp_value is None else base_mp_value)

        return {
            "started_at": started_at,
            "base_hp": max(0, min(int(player["max_hp"]), base_hp)),
            "base_mp": max(0, min(int(player["max_mp"]), base_mp)),
            "elapsed_seconds": elapsed,
            "remaining_seconds": max(0, full_seconds - elapsed),
        }

    @staticmethod
    def _rest_recover_value(base_value: int, current_value: int, max_value: int, rate: float) -> int:
        """按恢复窗口基线结算，避免普通反复短休重复刷新前段收益。"""

        max_value = max(1, int(max_value))
        current_value = max(0, min(max_value, int(current_value)))
        base_value = max(0, min(max_value, int(base_value)))
        target = base_value + int((max_value - base_value) * max(0.0, min(1.0, rate)))
        return min(max_value, max(current_value, target))

    @staticmethod
    def _actual_rest_recovery_rate(
        before_hp: int,
        before_mp: int,
        after_hp: int,
        after_mp: int,
        max_hp: int,
        max_mp: int,
    ) -> float:
        """按给定前后状态计算实际补回比例。"""

        hp_missing = max(0, int(max_hp) - max(0, int(before_hp)))
        mp_missing = max(0, int(max_mp) - max(0, int(before_mp)))
        missing = hp_missing + mp_missing
        if missing <= 0:
            return 1.0
        hp_gain = max(0, min(int(max_hp), int(after_hp)) - max(0, int(before_hp)))
        mp_gain = max(0, min(int(max_mp), int(after_mp)) - max(0, int(before_mp)))
        return max(0.0, min(1.0, (hp_gain + mp_gain) / missing))

    @staticmethod
    def _rest_recover_bonus_text(multiplier: float) -> str:
        """把恢复增益展示成基础恢复量上的倍率。"""

        value = max(0.0, float(multiplier) * 100)
        if abs(value - 100.0) < 0.05:
            return ""
        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"，恢复增益 **{text}%**"

    @staticmethod
    def _rest_time_text(seconds: int) -> str:
        """格式化休息时长。"""

        seconds = max(0, int(seconds))
        minutes, rest = divmod(seconds, 60)
        if minutes <= 0:
            return f"{rest} 秒"
        if rest <= 0:
            return f"{minutes} 分钟"
        return f"{minutes} 分 {rest} 秒"

    def _current_combat_info(self, client_id: str, player: dict, weapon: dict | None) -> dict:
        """读取当前实战速度、技能节奏和武器定位。"""

        skill = self.db.fetch_one("SELECT * FROM weapon_skill_defs WHERE skill_id = ?", (weapon["skill_id"],)) if weapon else None
        effects = self._merge_effects(self.equipment_bonuses(client_id), self._weapon_effects(weapon))
        return self.combat_profile(int(player["level"]), weapon, skill, effects)

    def _weapon_profile_lines(self, weapon: dict | None, combat_info: dict) -> list[str]:
        """生成修仙信息里的武器区块。"""

        if not weapon:
            return ["未装备", "定位：未装备武器，只有基础出手", "技能：无", "附魔：无"]

        weapon_id = int(weapon["weapon_id"])
        skill = self.db.fetch_one(
            "SELECT name FROM weapon_skill_defs WHERE skill_id = ?",
            (weapon["skill_id"],),
        )
        skill_name = skill["name"] if skill else "普通攻击"
        custom_skill = self.db.fetch_one(
            "SELECT custom_name FROM weapon_enchant_names WHERE weapon_id = ? AND slot_no = 0",
            (weapon_id,),
        )
        base_skill = enchant_label_name(skill_name, custom_skill["custom_name"] if custom_skill else "")
        enchants = self._weapon_enchant_profile_text(weapon_id, load_json(weapon["enchant_effects"], []))
        return [
            f"{weapon_id_label(weapon_id)} {weapon_label_name(weapon)} [{weapon['quality']}]",
            f"类型：{weapon['weapon_type']}｜定位：{combat_info['weapon_style']}",
            f"攻击：**+{self.weapon_attack(weapon)}**",
            f"技能：{base_skill}",
            f"附魔：{enchants}",
        ]

    def _weapon_enchant_profile_text(self, weapon_id: int, enchant_ids: object) -> str:
        """生成面板里的附魔摘要。"""

        if not isinstance(enchant_ids, list) or not enchant_ids:
            return "无"
        custom_rows = self.db.fetch_all(
            "SELECT slot_no, custom_name FROM weapon_enchant_names WHERE weapon_id = ?",
            (weapon_id,),
        )
        custom_names = {int(row["slot_no"]): row["custom_name"] for row in custom_rows}
        labels = []
        for slot_no, enchant_id in enumerate(enchant_ids, start=1):
            row = self.db.fetch_one("SELECT name FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
            base_name = row["name"] if row else str(enchant_id)
            labels.append(f"{slot_no}.{enchant_label_name(base_name, custom_names.get(slot_no, ''))}")
        return "、".join(labels)

    def _fixed_equipment_lines(self, client_id: str) -> list[str]:
        """生成修仙信息里的装备区块。"""

        self.db.ensure_fixed_equipment(client_id)
        rows = self.db.fetch_all(
            "SELECT * FROM fixed_equipment WHERE client_id = ?",
            (client_id,),
        )
        row_map = {row["slot"]: row for row in rows}
        parts = []
        for slot in EQUIPMENT_SLOTS:
            row = row_map.get(slot)
            if row is None:
                parts.append(f"{slot} Lv0 0孔")
                continue
            parts.append(f"{fixed_equipment_label(row)} Lv{row['level']} {row['hole_count']}孔")
        if not parts:
            return ["无"]
        return parts

    def _mount_profile_lines(self, client_id: str, player: dict) -> list[str]:
        """生成修仙信息里的坐骑区块。等级 < 10 时返回空。"""
        level = player.get("level", 0)
        if level < 10:
            return ["等级达到 10 级后开启"]
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )
        if not mount:
            return ["等级达到 10 级后开启"]
        mount_def = self.db.fetch_one(
            "SELECT * FROM mount_defs WHERE mount_id=?", (mount["mount_id"],)
        )
        if not mount_def:
            return ["坐骑数据异常"]
        mt = mount_def["mount_type"]
        if mt == "extreme":
            tier_text = "极显化"
        elif mt == "manifest":
            tier_text = "显化"
        else:
            tier_text = f"{mount_def['tier']}阶"

        # 方向前缀（显化/极显化）
        direction = mount_def.get("manifest_direction") or ""
        name = mount_def["name"]
        stars = mount["stars"]
        max_stars = mount_def["max_stars"]
        if direction:
            line1 = f"{tier_text} · {direction} · {name} ⭐{stars}/{max_stars}"
        else:
            line1 = f"{tier_text} · {name} ⭐{stars}/{max_stars}"

        lines = [line1]

        # 意境/大道显化/极境
        lore = mount_def.get("lore") or ""
        if lore:
            if mt == "extreme":
                lines.append(f"极境：{lore}")
            elif mt == "manifest":
                lines.append(f"大道显化：{lore}")
            else:
                lines.append(f"意境：{lore}")

        # 进阶祝福值（极显化无进阶）
        if mt != "extreme":
            blessing = mount["blessing_value"]
            total = mount_def.get("advance_blessing_total") or 0
            if blessing > 0 or mount["blessing_expires_at"]:
                blessing_text = f"进阶祝福：{blessing}/{total}"
                expires_at = mount["blessing_expires_at"]
                if expires_at:
                    expires = dt(expires_at)
                    if expires:
                        remaining = expires - now()
                        if remaining.total_seconds() > 0:
                            hours = int(remaining.total_seconds() // 3600)
                            minutes = int((remaining.total_seconds() % 3600) // 60)
                            blessing_text += f"（剩余 {hours} 时 {minutes} 分）"
                lines.append(blessing_text)

        return lines

    def _daily_bonus_lines(self, client_id: str) -> list[str]:
        """生成今日气运、天气、灵潮和今日合计加成。"""

        day = business_day()
        fortune = self.db.fetch_one(
            """
            SELECT fortune, flavor, effect
            FROM daily_fortunes
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, day),
        )
        world = world_state_for_day(day)
        weather = world["weather"]
        tide = world["tide"]
        effects = [world["effect"]]
        if fortune:
            effects.insert(0, load_json(fortune["effect"], {}))

        return [
            f"气运：{self._fortune_bonus_text(fortune)}",
            f"天气：{weather['name']}（{format_effect(weather['effect'])}）",
            f"灵潮：{tide['name']}（{format_effect(tide['effect'])}）",
            f"今日加成：{self._effect_total_text(effects)}",
        ]

    def _daily_bonus_total_text(self, client_id: str) -> str:
        """生成状态面板里的一行今日加成摘要。"""

        for line in reversed(self._daily_bonus_lines(client_id)):
            if line.startswith("今日加成："):
                return line.replace("今日加成：", "", 1)
        return "无"

    def _nemesis_text(self, client_id: str) -> str:
        """读取当前对玩家仇恨最高的人，并换算报复指数。"""

        row = self.db.fetch_one(
            """
            SELECT h.hate_value, h.robbery_count, p.display_name
            FROM player_hatreds h
            LEFT JOIN players p ON p.client_id = h.from_client_id
            WHERE h.to_client_id = ? AND h.hate_value > 0
            ORDER BY h.hate_value DESC, h.robbery_count DESC, h.updated_at DESC
            LIMIT 1
            """,
            (client_id,),
        )
        if not row:
            return ""

        hate_value = int(row["hate_value"])
        revenge_index = min(100, hate_value * 20)
        robbery_count = int(row["robbery_count"])
        name = str(row.get("display_name") or "未知玩家")
        return f"{name}（仇恨 **{hate_value}**，报复指数 **{revenge_index}**，抢劫 **{robbery_count}** 次）"

    @staticmethod
    def _fortune_bonus_text(fortune: dict | None) -> str:
        """把玩家今日签到气运整理成面板文字。"""

        if not fortune:
            return "未签到"
        return f"{fortune['fortune']}（{format_effect(fortune['effect'])}）"

    @staticmethod
    def _effect_total_text(effects: list[dict]) -> str:
        """把多来源今日加成合并展示。"""

        total: dict[str, float] = {}
        for effect in effects:
            for key, value in effect.items():
                if isinstance(value, int | float):
                    total[key] = total.get(key, 0) + float(value)
        return format_effect(total)

    def _refresh_diary(self, client_id: str, player: dict) -> None:
        """按当前数据补齐修仙日记里程碑。"""

        now_text = ts()
        entries = []
        entries.extend(self._base_diary_entries(client_id, player, now_text))
        entries.extend(self._identity_diary_entries(client_id, player, now_text))
        entries.extend(self._travel_trade_diary_entries(client_id, now_text))
        entries.extend(self._weapon_diary_entries(client_id, now_text))
        entries.extend(self._recycle_diary_entries(client_id, now_text))
        entries.extend(self._battle_diary_entries(client_id, now_text))
        entries.extend(self._misc_diary_entries(client_id, now_text))

        with self.db.transaction() as conn:
            for key, text, created_at in entries:
                self.record_journal_conn(
                    conn,
                    client_id,
                    key,
                    text,
                    created_at=created_at,
                    keep_first_time=True,
                )

    def _base_diary_entries(self, client_id: str, player: dict, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的基础资料：创建、等级、财富和当前位置。"""

        vault = self.db.fetch_one("SELECT balance FROM source_vaults WHERE client_id = ?", (client_id,))
        vault_balance = int(vault["balance"]) if vault else 0
        return [
            ("created", f"{player['created_at']} 初入修仙界，名为 {player['display_name']}。", player["created_at"]),
            ("level", f"修为已至 {player['level']} 级，累计经验 {player['exp']}。", now_text),
            (
                "wealth",
                f"随身源石 {money(player['source_stones'])}，源库存量 {money(vault_balance)}。",
                now_text,
            ),
            ("location", f"当前停留在 {player['location_name']}，状态为 {player['status']}。", now_text),
        ]

    def _identity_diary_entries(self, client_id: str, player: dict, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的身份成长：签到、新手礼包和改名。"""

        entries = []
        sign_count = self.stat_count(
            client_id,
            "sign_count",
            "SELECT COUNT(*) AS count FROM game_logs WHERE client_id = ? AND action = '签到'",
            (client_id,),
        )
        if sign_count:
            entries.append(("sign", f"累计签到 {sign_count} 次，日日有进，慢慢成路。", now_text))
        if int(player["newbie_claimed"]):
            entries.append(("newbie_gift", "已经领取新手礼包，最初的一点底气还在账上。", now_text))
        rename_count = self.stat_count(
            client_id,
            "rename_count",
            "SELECT COUNT(*) AS count FROM game_logs WHERE client_id = ? AND action = '改名'",
            (client_id,),
        )
        if rename_count:
            entries.append(("rename", f"改名 {rename_count} 次，修仙界称呼几经流转。", now_text))
        return entries

    def _travel_trade_diary_entries(self, client_id: str, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的行动记录：探险、跑商和二手市场。"""

        entries = []
        explore_count = self.stat_count(
            client_id,
            "explore_count",
            "SELECT COUNT(*) AS count FROM exploration_records WHERE client_id = ?",
            (client_id,),
        )
        if explore_count:
            latest = self.db.fetch_one(
                """
                SELECT location_name, status
                FROM exploration_records
                WHERE client_id = ?
                ORDER BY record_id DESC
                LIMIT 1
                """,
                (client_id,),
            )
            latest_text = f"，最近一次在 {latest['location_name']}（{latest['status']}）" if latest else ""
            entries.append(("explore", f"累计探险 {explore_count} 次{latest_text}。", now_text))

        trade_sell_count = self.stat_count(
            client_id,
            "trade_sell_count",
            "SELECT COUNT(*) AS count FROM trade_records WHERE client_id = ? AND action = 'sell'",
            (client_id,),
        )
        trade_net = self.stat_total(
            client_id,
            "trade_net",
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN action = 'sell' THEN total_price - fee
                    WHEN action = 'buy' THEN -(total_price + fee)
                    ELSE 0
                END
            ), 0) AS total
            FROM trade_records
            WHERE client_id = ? AND action IN ('buy', 'sell')
            """,
            (client_id,),
        )
        if trade_sell_count:
            entries.append(("trade", f"累计跑商出售 {trade_sell_count} 次，净利润 {money(trade_net)} 源石。", now_text))

        second_hand_sell = self.stat_count(
            client_id,
            "second_hand_sell_count",
            "SELECT COUNT(*) AS count FROM second_hand_records WHERE seller_id = ?",
            (client_id,),
        )
        second_hand_buy = self.stat_count(
            client_id,
            "second_hand_buy_count",
            "SELECT COUNT(*) AS count FROM second_hand_records WHERE buyer_id = ?",
            (client_id,),
        )
        if second_hand_sell or second_hand_buy:
            entries.append(
                (
                    "second_hand",
                    f"二手市场成交：卖出 {second_hand_sell} 次，买入 {second_hand_buy} 次。",
                    now_text,
                )
            )
        return entries

    def _weapon_diary_entries(self, client_id: str, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的武器收藏。"""

        entries = []
        weapon_count = self._count("player_weapons", "holder_id = ?", (client_id,))
        if weapon_count:
            equipped = self.equipped_weapon_row(client_id)
            equipped_text = f"，当前执 {weapon_label_name(equipped)}" if equipped else ""
            entries.append(("weapon", f"武器库已收纳 {weapon_count} 把武器{equipped_text}。", now_text))
        rare_weapon = self.db.fetch_one(
            """
            SELECT quality, COUNT(*) AS count
            FROM player_weapons
            WHERE holder_id = ? AND quality IN ('稀品', '珍品')
            GROUP BY quality
            ORDER BY CASE quality WHEN '珍品' THEN 2 ELSE 1 END DESC
            LIMIT 1
            """,
            (client_id,),
        )
        if rare_weapon:
            entries.append(("rare_weapon", f"曾得 {rare_weapon['quality']} 武器 {rare_weapon['count']} 把，坊间称其手气不浅。", now_text))
        return entries

    def _recycle_diary_entries(self, client_id: str, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的回收记录。"""

        entries = []
        recycle_count = self.stat_count(
            client_id,
            "weapon_recycle_count",
            "SELECT COUNT(*) AS count FROM weapon_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        recycle_income = self.stat_total(
            client_id,
            "weapon_recycle_income",
            "SELECT COALESCE(SUM(total_price), 0) AS total FROM weapon_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        if recycle_count:
            entries.append(("weapon_recycle", f"累计出售武器 {recycle_count} 把，得源石 {money(recycle_income)}。", now_text))
        gem_recycle_count = self.stat_count(
            client_id,
            "gem_recycle_count",
            "SELECT COUNT(*) AS count FROM gem_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        gem_recycle_income = self.stat_total(
            client_id,
            "gem_recycle_income",
            "SELECT COALESCE(SUM(total_price), 0) AS total FROM gem_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        if gem_recycle_count:
            entries.append(("gem_recycle", f"累计出售宝石 {gem_recycle_count} 次，得源石 {money(gem_recycle_income)}。", now_text))
        book_recycle_count = self.stat_count(
            client_id,
            "book_recycle_count",
            "SELECT COUNT(*) AS count FROM book_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        book_recycle_income = self.stat_total(
            client_id,
            "book_recycle_income",
            "SELECT COALESCE(SUM(total_price), 0) AS total FROM book_recycle_records WHERE client_id = ?",
            (client_id,),
        )
        if book_recycle_count:
            entries.append(("book_recycle", f"累计出售技能书 {book_recycle_count} 次，得源石 {money(book_recycle_income)}。", now_text))
        return entries

    def _battle_diary_entries(self, client_id: str, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的战斗记录：虫洞、首领和玩家对战。"""

        entries = []
        wormhole_count = self.stat_count(
            client_id,
            "wormhole_count",
            "SELECT COUNT(*) AS count FROM wormhole_participants WHERE client_id = ?",
            (client_id,),
        )
        if wormhole_count:
            damage = self.stat_total(client_id, "wormhole_damage", "SELECT COALESCE(SUM(damage), 0) AS total FROM wormhole_participants WHERE client_id = ?", (client_id,))
            entries.append(("wormhole", f"挑战过异界虫洞 {wormhole_count} 场，累计伤害 {damage}。", now_text))
        boss_count = self.stat_count(
            client_id,
            "boss_count",
            "SELECT COUNT(*) AS count FROM seasonal_boss_participants WHERE client_id = ?",
            (client_id,),
        )
        if boss_count:
            damage = self.stat_total(client_id, "boss_damage", "SELECT COALESCE(SUM(damage), 0) AS total FROM seasonal_boss_participants WHERE client_id = ?", (client_id,))
            entries.append(("seasonal_boss", f"挑战过岁时首领 {boss_count} 场，累计伤害 {damage}。", now_text))

        duel_count = self.stat_count(
            client_id,
            "duel_count",
            "SELECT COUNT(*) AS count FROM duel_records WHERE from_client_id = ? OR to_client_id = ?",
            (client_id, client_id),
        )
        duel_win = self.stat_count(
            client_id,
            "duel_win_count",
            "SELECT COUNT(*) AS count FROM duel_records WHERE winner_id = ?",
            (client_id,),
        )
        if duel_count:
            entries.append(("duel", f"公开对战 {duel_count} 场，其中胜利 {duel_win} 场。", now_text))
        return entries

    def _misc_diary_entries(self, client_id: str, now_text: str) -> list[tuple[str, str, str]]:
        """日记里的杂项记录：铭刻和物品使用。"""

        entries = []
        inscription_count = self.stat_count(
            client_id,
            "inscription_count",
            """
            SELECT COUNT(*) AS count FROM game_logs
            WHERE client_id = ? AND action IN ('铭刻装备', '铭刻武器', '铭刻附魔', '铭刻自带技能')
            """,
            (client_id,),
        )
        if inscription_count:
            entries.append(("inscription", f"使用铭刻之羽 {inscription_count} 次，把名字刻进自己的器物里。", now_text))
        item_use_count = self.stat_count(
            client_id,
            "item_use_count",
            "SELECT COUNT(*) AS count FROM game_logs WHERE client_id = ? AND action = '使用物品'",
            (client_id,),
        )
        if item_use_count:
            entries.append(("item_use", f"使用恢复或成长物品 {item_use_count} 次，生死间也懂得惜身。", now_text))
        return entries

    def _count(self, table: str, where: str, params: tuple) -> int:
        """执行简单计数。"""

        row = self.db.fetch_one(f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params)
        return int(row["count"]) if row else 0

    def _total(self, sql: str, params: tuple) -> int:
        """执行 SUM 类统计。"""

        row = self.db.fetch_one(sql, params)
        if not row:
            return 0
        return int(row["total"] or 0)

    def _physique_profile_lines(self, player: dict) -> list[str]:
        """生成修仙信息里的体质区块。"""

        row = self.db.fetch_one(
            "SELECT name, grade, kind, physique_value, effect FROM physique_defs WHERE physique_id = ?",
            (player["physique_id"],),
        )
        if not row:
            return [
                f"🌿 体质值 {player['physique_value']}",
                "✨ 未知品阶 · 未知向",
                "💤 天赋：无特殊效果",
            ]
        effect = format_effect(row["effect"])
        value = int(row["physique_value"])
        icon = "🌿" if value <= 0 else "🌱"
        stage = self._physique_stage_text(str(row["grade"]), str(row["kind"]), value)
        trait_icon = "💤" if effect == "无主动效果" else "🛡️"
        return [
            f"{icon} {row['name']}",
            f"✨ {stage}",
            f"{trait_icon} 天赋：{effect}",
        ]

    @staticmethod
    def _physique_stage_text(grade: str, kind: str, value: int) -> str:
        """把体质品阶、方向和体质值写成更像玩家面板的文字。"""

        if value <= 0:
            return f"{grade} · {kind}向 · 未觉醒"
        return f"{grade}{PlayerService._chinese_number(value)}重 · {kind}向"

    @staticmethod
    def _chinese_number(value: int) -> str:
        """把 1-99 的整数转成中文数字，用于体质重数。"""

        digits = "零一二三四五六七八九"
        if value <= 10:
            return "十" if value == 10 else digits[value]
        if value < 20:
            return "十" + digits[value % 10]
        tens, ones = divmod(value, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")


service = PlayerService(db)

__all__ = ["PlayerService", "service"]
