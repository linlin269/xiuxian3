"""修仙界历史服务。"""

from __future__ import annotations

from ..format_text import T

from datetime import date, datetime, time, timedelta
from typing import Any

from ..common import (
    CoreService,
    business_day,
    dump_json,
    format_effect,
    load_json,
    money,
    ts,
    weapon_label_name,
    world_state_for_day,
)
from ..constants import DAY_RESET_HOUR
from ..sql import db
from ..world_materials import WorldMaterialService


NEWSPAPER_TITLE = "修仙早报:v5"
HISTORY_VOLUME_COMMANDS = (
    "人物史榜",
    "宗门史榜",
    "城池史榜",
    "战斗名局",
    "商路奇闻",
    "异界虫洞录",
)
NEWSPAPER_NAV_BUTTONS = "<风云榜><修仙早报><修仙界历史>"
HISTORY_NAV_BUTTONS = "<风云榜><修仙早报><修仙界历史><人物史榜><宗门史榜><城池史榜><战斗名局><商路奇闻><异界虫洞录>"
OLD_TEXT_MARKERS = ("☆", "┌", "└", "├", "│", "模板:", "自带技能:", "附魔栏:", "存放:")
CHRONICLE_KEY_PREFIX = "xiuxian_history:"
HISTORY_VOLUME_ALIASES = {
    "人物史榜": "人物",
    "宗门史榜": "宗门",
    "城池史榜": "城池",
    "战斗名局": "战斗",
    "商路奇闻": "商路",
    "异界虫洞录": "虫洞",
}


class XiuxianHistoryService(CoreService):
    """风云榜、修仙早报、修仙界历史、史册分卷和公开人物志。"""

    def leaderboard(self, client_id: str) -> str:
        """查看当前业务日的全服榜单。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        start, end = self._business_window()
        panel = T.panel()
        panel.section(f"风云榜·{business_day()}")
        panel.line(self._top_damage_text(start, end))
        panel.line(self._top_rich_text())
        panel.line(self._top_trade_text(start, end))
        panel.line(self._top_explore_text(start, end))
        panel.line(self._top_luck_text(start, end))
        panel.line(self._top_active_text(start, end))
        return panel.render() + "<风云榜><修仙早报><修仙界历史>"

    def newspaper(self, client_id: str) -> str:
        """查看今日修仙早报。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        day = business_day()
        row = self.db.fetch_one("SELECT title, content FROM daily_newspapers WHERE business_day = ?", (day,))
        cached_content = self._current_newspaper_content(row)
        if cached_content:
            return self._newspaper_response(cached_content)

        content = self._build_newspaper(day)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_newspapers
                (business_day, title, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (day, NEWSPAPER_TITLE, content, ts()),
            )
        return self._newspaper_response(content)

    def _current_newspaper_content(self, row: Any) -> str:
        """只复用当前富文本版本的早报缓存。"""

        if not row or row["title"] != NEWSPAPER_TITLE:
            return ""
        content = str(row["content"])
        if any(marker in content for marker in OLD_TEXT_MARKERS):
            return ""
        return content

    def _newspaper_response(self, content: str) -> str:
        """早报缓存和新生成内容统一追加跳转按钮。"""

        return T.attach(content, NEWSPAPER_NAV_BUTTONS)

    def chronicle(self, client_id: str) -> str:
        """查看修仙界历史总入口。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        day = business_day()
        self._save_or_get_chronicle(day, refresh=True)
        rows = self.db.fetch_all(
            """
            SELECT key, value
            FROM schema_meta
            WHERE key LIKE ?
            ORDER BY key DESC
            LIMIT 5
            """,
            (f"{CHRONICLE_KEY_PREFIX}%",),
        )

        panel = T.panel()
        panel.section("修仙界历史")
        panel.line("风云榜看当世热闹，史册分卷只收留名结果；普通流水不进史书。")
        panel.hr()
        panel.section("史册分卷")
        panel.line("人物史榜｜宗门史榜｜城池史榜")
        panel.line("战斗名局｜商路奇闻｜异界虫洞录")
        panel.hr()
        panel.section("最近大事")
        for row in rows:
            current_day = str(row["key"]).removeprefix(CHRONICLE_KEY_PREFIX)
            entries = self._decode_entries(row["value"])
            panel.line(current_day)
            for entry in entries:
                panel.line(f"- {entry}")
        return panel.render() + HISTORY_NAV_BUTTONS

    def history_volume(self, client_id: str, volume: str) -> str:
        """查看一个史册分卷。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        key = HISTORY_VOLUME_ALIASES.get(str(volume).strip())
        builders = {
            "人物": self._history_player_lines,
            "宗门": self._history_sect_lines,
            "城池": self._history_city_lines,
            "战斗": self._history_battle_lines,
            "商路": self._history_trade_lines,
            "虫洞": self._history_wormhole_lines,
        }
        builder = builders.get(key or "")
        if not builder:
            return T.hint("没有这卷史册。", "可查看：人物史榜、宗门史榜、城池史榜、战斗名局、商路奇闻、异界虫洞录。")

        panel = T.panel()
        panel.section(str(volume).strip())
        lines = builder()
        if lines:
            panel.lines(lines)
        else:
            panel.line("暂无足以入史的记录。")
        panel.hr()
        panel.line("史册只收破纪录、首创、周期霸主和特殊事件，普通流水会按清理周期散入风里。")
        return panel.render() + HISTORY_NAV_BUTTONS

    def profile(self, client_id: str, message: str) -> str:
        """公开查看一位玩家的修仙界档案。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        if not message.strip():
            return T.hint("缺少要查看的玩家名称。", "发送：人物志 玩家名称，例如：人物志 青衫客")

        target_id = self.player_id_from_last_arg(message)
        target = self.player(target_id) if target_id else None
        if not target:
            return T.hint("没有找到这位玩家。", "请确认名称是否正确，或直接@对方。")

        title = self.refresh_titles(target_id, target) or "无"
        weapon = self.equipped_weapon_row(target_id)
        weapon_text = weapon_label_name(weapon) if weapon else "未佩戴"
        wormhole_count = self.stat_count(
            target_id,
            "wormhole_count",
            "SELECT COUNT(*) AS count FROM wormhole_participants WHERE client_id = ?",
            (target_id,),
        )
        boss_count = self.stat_count(
            target_id,
            "boss_count",
            "SELECT COUNT(*) AS count FROM seasonal_boss_participants WHERE client_id = ?",
            (target_id,),
        )
        explore_count = self.stat_count(
            target_id,
            "explore_count",
            "SELECT COUNT(*) AS count FROM exploration_records WHERE client_id = ?",
            (target_id,),
        )
        trade_count = self.stat_count(
            target_id,
            "trade_count",
            "SELECT COUNT(*) AS count FROM trade_records WHERE client_id = ?",
            (target_id,),
        )
        duel_wins = self.stat_count(
            target_id,
            "duel_win_count",
            "SELECT COUNT(*) AS count FROM duel_records WHERE winner_id = ?",
            (target_id,),
        )
        material_text = self._material_profile_text(target_id)

        panel = T.panel()
        panel.section(f"{target['display_name']}人物志")
        panel.line(f"称号：{title}")
        panel.line(f"等级：**{target['level']}**")
        panel.line(f"常驻地点：{target['location_name']}")
        panel.line(f"代表武器：{weapon_text}")
        panel.line(
            f"修仙界事迹：参与虫洞 **{wormhole_count}** 次｜挑战首领 **{boss_count}** 次｜"
            f"探险 **{explore_count}** 次｜跑商 **{trade_count}** 次｜对战胜利 **{duel_wins}** 次"
        )
        if material_text:
            panel.line(f"世界流转：{material_text}")
        return panel.render()

    def _build_newspaper(self, day: str) -> str:
        """生成今日修仙早报。"""

        start, end = self._business_window(day)
        panel = T.panel()
        panel.section(f"修仙早报·{day}")
        panel.line(f"小编按：今日修仙界从 {DAY_RESET_HOUR:02d}:00 起算，茶摊照例收风。")
        panel.hr()
        panel.section("天地气象")
        panel.lines(self._world_state_lines(day))
        panel.hr()
        panel.section("头版人物")
        panel.line(self._top_damage_text(start, end))
        panel.line(self._top_rich_text())
        panel.line(self._top_explore_text(start, end))
        panel.line(self._top_luck_text(start, end))
        panel.hr()
        panel.section("坊间传闻")
        panel.line(self._rumor_text(start, end))
        panel.hr()
        panel.section("商会风向")
        panel.line(self._business_wind_text(start, end))
        panel.lines(self._city_wind_lines())
        panel.hr()
        panel.section("首领动向")
        panel.lines(self._boss_trend_lines(day, start, end))
        return panel.render() + "<商场推荐><首领>"

    def _world_state_lines(self, day: str) -> list[str]:
        """生成早报里的全服天气和灵潮栏目。"""

        state = world_state_for_day(day)
        weather = state["weather"]
        tide = state["tide"]
        total = format_effect(state["effect"])
        return [
            f"今日天气：{weather['name']}。{weather['flavor']}（{format_effect(weather['effect'])}）",
            f"今日灵潮：{tide['name']}。{tide['flavor']}（{format_effect(tide['effect'])}）",
            f"全服生效：{total}",
        ]

    def _city_wind_lines(self) -> list[str]:
        """生成城池世界物资风向。"""

        lines: list[str] = []

        def add_line(text: str) -> None:
            if text and len(lines) < 5:
                lines.append(text)

        top_city = self.db.fetch_one(
            """
            SELECT location_name, city_level, build_exp
            FROM city_world_states
            ORDER BY city_level DESC, build_exp DESC
            LIMIT 1
            """
        )
        if top_city:
            add_line(f"城池建设：{top_city['location_name']} Lv.{top_city['city_level']}，建设余劲 {top_city['build_exp']}。")
        medicine = self.db.fetch_one(
            """
            SELECT location_name,
                   medicine_material + medicine_catalyst + medicine_fuel AS stock,
                   medicine_guard
            FROM city_world_states
            ORDER BY stock ASC, medicine_guard DESC, updated_at DESC
            LIMIT 1
            """
        )
        if medicine:
            add_line(
                f"药路最紧：{medicine['location_name']}，余量 {medicine['stock']}，防备 {medicine['medicine_guard']}。"
            )
        life = self.db.fetch_one(
            """
            SELECT location_name,
                   life_food + life_salt + life_water + life_cloth + life_fuel AS life_score
            FROM city_world_states
            ORDER BY life_score DESC, updated_at DESC
            LIMIT 1
            """
        )
        if life:
            add_line(f"民生最盛：{life['location_name']}，民生存量 {life['life_score']}。")
        treasure = self.db.fetch_one(
            """
            SELECT city_name, status, current_price, bid_count, x, y
            FROM treasure_maps
            WHERE status IN ('拍卖中', '可拾取', '宗主待领', '已成交')
            ORDER BY generated_at DESC
            LIMIT 1
            """
        )
        if treasure:
            add_line(self._treasure_wind_text(treasure))
        relic = self.db.fetch_one(
            """
            SELECT location_name, relic_energy, city_level
            FROM city_world_states
            ORDER BY relic_energy DESC
            LIMIT 1
            """
        )
        if relic and int(relic["relic_energy"]) > 0:
            limit = WorldMaterialService.relic_limit(int(relic["city_level"]))
            add_line(f"古物蓄能：{relic['location_name']} {relic['relic_energy']}/{limit}。")
        war = self.db.fetch_one(
            """
            SELECT buyer_name, prep_name, prep_value, threshold, pending
            FROM war_prep_states
            ORDER BY pending DESC, prep_value * 1.0 / CASE WHEN threshold <= 0 THEN 1 ELSE threshold END DESC
            LIMIT 1
            """
        )
        if war and int(war["prep_value"]) > 0:
            suffix = "，已待牵引" if int(war["pending"]) else ""
            add_line(f"战备风向：{war['prep_name']} {war['prep_value']}/{war['threshold']}{suffix}。")
        return lines

    @staticmethod
    def _treasure_wind_text(row: dict[str, Any]) -> str:
        """生成早报里的藏宝图动向。"""

        status = str(row["status"])
        if status == "拍卖中":
            return f"藏宝图动向：{row['city_name']} 正在拍卖，现价 {money(row['current_price'])}，出价 {row['bid_count']}/10。"
        if status == "可拾取":
            return f"藏宝图动向：{row['city_name']} 图落荒地 ({row['x']},{row['y']})。"
        if status == "宗主待领":
            return f"藏宝图动向：{row['city_name']} 图卷入宗门山门，宗主待领。"
        return f"藏宝图动向：{row['city_name']} 已成交，买主待领。"

    def _material_profile_text(self, client_id: str) -> str:
        """生成公开人物志里的世界物资回收倾向。"""

        total = self.db.fetch_one(
            """
            SELECT COALESCE(SUM(quantity), 0) AS quantity
            FROM world_material_records
            WHERE client_id = ?
            """,
            (client_id,),
        )
        quantity = int(total["quantity"] or 0) if total else 0
        if quantity <= 0:
            return ""

        top = self.db.fetch_one(
            """
            SELECT category, COALESCE(SUM(quantity), 0) AS quantity
            FROM world_material_records
            WHERE client_id = ?
            GROUP BY category
            ORDER BY quantity DESC
            LIMIT 1
            """,
            (client_id,),
        )
        if not top:
            return f"回收物资 **{quantity}** 件"
        return f"回收物资 **{quantity}** 件｜主要流向 {top['category']} **{top['quantity']}** 件"

    def _history_player_lines(self) -> list[str]:
        """人物史榜：首创和长期人物纪录。"""

        lines: list[str] = []
        first_player = self.db.fetch_one("SELECT display_name, created_at FROM players ORDER BY created_at ASC LIMIT 1")
        if first_player:
            lines.append(f"开界第一人：{first_player['display_name']}，于 {self._date_text(first_player['created_at'])} 入世。")

        top_level = self.db.fetch_one(
            "SELECT display_name, level FROM players ORDER BY level DESC, exp DESC, created_at ASC LIMIT 1"
        )
        if top_level:
            lines.append(f"境界最高：{top_level['display_name']} Lv.{top_level['level']}。")

        rich = self._richest_row()
        if rich and int(rich["total"] or 0) > 0:
            lines.append(f"明面资产最高：{self.format_player_name(rich['client_id'])}，约 {money(rich['total'])}。")

        for label, row in (
            ("探险最多", self._top_lifetime_stat_row("explore_count", "exploration_records", "client_id", "COUNT(*)")),
            ("跑商最多", self._top_lifetime_stat_row("trade_sell_count", "trade_records", "client_id", "SUM(CASE WHEN action = 'sell' THEN 1 ELSE 0 END)")),
            ("虫洞伤害最高", self._top_lifetime_stat_row("wormhole_damage", "wormhole_participants", "client_id", "SUM(damage)")),
            ("首领伤害最高", self._top_lifetime_stat_row("boss_damage", "seasonal_boss_participants", "client_id", "SUM(damage)")),
            ("对战胜场最多", self._top_lifetime_stat_row("duel_win_count", "duel_records", "winner_id", "COUNT(*)", "winner_id IS NOT NULL AND winner_id != ''")),
        ):
            if row and int(row["total"] or 0) > 0:
                lines.append(f"{label}：{self.format_player_name(row['client_id'])}，{row['total']}。")

        weapon = self.db.fetch_one(
            """
            SELECT COALESCE(w.custom_name, '') AS custom_name,
                   d.name,
                   w.quality,
                   w.level,
                   w.max_level,
                   w.exp,
                   w.holder_id
            FROM player_weapons AS w
            JOIN weapon_defs AS d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id NOT LIKE '__%__:%'
            ORDER BY w.max_level DESC, w.level DESC, w.exp DESC, w.created_at ASC
            LIMIT 1
            """
        )
        if weapon:
            lines.append(
                f"名器当前最高：{self.format_player_name(weapon['holder_id'])} 持有"
                f"「{weapon_label_name(weapon)}」{weapon['quality']} Lv.{weapon['level']}/{weapon['max_level']}。"
            )
        return lines

    def _history_sect_lines(self) -> list[str]:
        """宗门史榜：宗门等级、底蕴和宗门战纪录。"""

        lines: list[str] = []
        top = self.db.fetch_all(
            """
            SELECT s.name, s.location_name, st.level, st.exp, st.influence_merit, st.support_merit, st.build_merit
            FROM sect_stats AS st
            JOIN sects AS s ON s.sect_id = st.sect_id
            ORDER BY st.level DESC, st.exp DESC, st.build_merit DESC
            LIMIT 3
            """
        )
        for index, row in enumerate(top, 1):
            lines.append(
                f"山门第 {index}：{row['name']}（{row['location_name']}）Lv.{row['level']}，"
                f"影响力 {row['influence_merit']}｜供养 {row['support_merit']}｜建设 {row['build_merit']}。"
            )

        influence = self.db.fetch_one(
            """
            SELECT s.name, COALESCE(SUM(r.influence), 0) AS total
            FROM sect_influence_records AS r
            JOIN sects AS s ON s.sect_id = r.sect_id
            GROUP BY r.sect_id
            ORDER BY total DESC
            LIMIT 1
            """
        )
        if influence and int(influence["total"] or 0) > 0:
            lines.append(f"宗门战累计影响最高：{influence['name']}，累计 {influence['total']}。")

        reward = self.db.fetch_one(
            """
            SELECT s.name, COUNT(*) AS count
            FROM sect_war_rewards AS r
            JOIN sects AS s ON s.sect_id = r.sect_id
            GROUP BY r.sect_id
            ORDER BY count DESC
            LIMIT 1
            """
        )
        if reward and int(reward["count"] or 0) > 0:
            lines.append(f"淬锋丹发放最多：{reward['name']}，累计 {reward['count']} 枚待领/已领。")
        return lines

    def _history_city_lines(self) -> list[str]:
        """城池史榜：世界状态承接点纪录。"""

        lines: list[str] = []
        city = self.db.fetch_one(
            """
            SELECT location_name, city_level, build_exp
            FROM city_world_states
            ORDER BY city_level DESC, build_exp DESC
            LIMIT 1
            """
        )
        if city:
            lines.append(f"城池等级最高：{city['location_name']} Lv.{city['city_level']}，建设余劲 {city['build_exp']}。")

        life = self.db.fetch_one(
            """
            SELECT location_name,
                   life_food + life_salt + life_water + life_cloth + life_fuel AS score
            FROM city_world_states
            ORDER BY score DESC, updated_at DESC
            LIMIT 1
            """
        )
        if life:
            lines.append(f"天道恩赐最盛：{life['location_name']}，民生总势 {life['score']}。")

        medicine = self.db.fetch_one(
            """
            SELECT location_name,
                   medicine_material + medicine_catalyst + medicine_fuel AS stock,
                   medicine_guard
            FROM city_world_states
            ORDER BY stock DESC, medicine_guard DESC
            LIMIT 1
            """
        )
        if medicine:
            lines.append(f"药路储备最厚：{medicine['location_name']}，药路存量 {medicine['stock']}，防备 {medicine['medicine_guard']}。")

        relic = self.db.fetch_one(
            """
            SELECT location_name, relic_energy, city_level
            FROM city_world_states
            ORDER BY relic_energy DESC, city_level DESC
            LIMIT 1
            """
        )
        if relic:
            limit = WorldMaterialService.relic_limit(int(relic["city_level"]))
            lines.append(f"古物蓄能最高：{relic['location_name']}，{relic['relic_energy']}/{limit}。")

        treasure = self.db.fetch_one(
            """
            SELECT city_name, COUNT(*) AS count
            FROM treasure_maps
            GROUP BY city_name
            ORDER BY count DESC
            LIMIT 1
            """
        )
        if treasure and int(treasure["count"] or 0) > 0:
            lines.append(f"藏宝图出世最多：{treasure['city_name']}，累计 {treasure['count']} 张。")
        return lines

    def _history_battle_lines(self) -> list[str]:
        """战斗名局：保留期内的极端战斗和首领纪录。"""

        lines: list[str] = []
        wormhole_hit = self.db.fetch_one(
            """
            SELECT c.client_id, c.damage, c.killed, w.boss_name, w.level
            FROM wormhole_challenge_records AS c
            JOIN wormholes AS w ON w.wormhole_id = c.wormhole_id
            ORDER BY c.damage DESC, c.created_at ASC
            LIMIT 1
            """
        )
        if wormhole_hit and int(wormhole_hit["damage"] or 0) > 0:
            suffix = "，并完成击破" if int(wormhole_hit["killed"] or 0) else ""
            lines.append(
                f"虫洞重击：{self.format_player_name(wormhole_hit['client_id'])} 对「{wormhole_hit['boss_name']}」"
                f"打出 {wormhole_hit['damage']} 伤害{suffix}。"
            )

        boss_hit = self.db.fetch_one(
            """
            SELECT c.client_id, c.damage, c.killed, e.boss_name, e.level
            FROM boss_challenge_records AS c
            JOIN seasonal_boss_events AS e ON e.event_id = c.event_id
            ORDER BY c.damage DESC, c.created_at ASC
            LIMIT 1
            """
        )
        if boss_hit and int(boss_hit["damage"] or 0) > 0:
            suffix = "，并完成击破" if int(boss_hit["killed"] or 0) else ""
            lines.append(
                f"首领重击：{self.format_player_name(boss_hit['client_id'])} 对「{boss_hit['boss_name']}」"
                f"打出 {boss_hit['damage']} 伤害{suffix}。"
            )

        duel = self.db.fetch_one(
            """
            SELECT winner_id, loser_id, mode, stake, created_at
            FROM duel_records
            WHERE winner_id IS NOT NULL AND winner_id != ''
            ORDER BY stake DESC, created_at DESC
            LIMIT 1
            """
        )
        if duel:
            stake = int(duel["stake"] or 0)
            stake_text = f"，赌注 {money(stake)}" if stake > 0 else ""
            lines.append(
                f"对战名局：{self.format_player_name(duel['winner_id'])} 胜过 "
                f"{self.format_player_name(duel['loser_id'])}（{duel['mode']}{stake_text}）。"
            )

        robbery = self.db.fetch_one(
            """
            SELECT robber_id, target_id, winner_id, success, loot_text, created_at
            FROM robbery_records
            ORDER BY success DESC, created_at DESC
            LIMIT 1
            """
        )
        if robbery:
            result = "得手" if int(robbery["success"] or 0) else "失手"
            loot = str(robbery["loot_text"] or "").strip()
            loot_text = f"，夺得 {loot}" if loot else ""
            lines.append(
                f"抢劫名局：{self.format_player_name(robbery['robber_id'])} 袭向 "
                f"{self.format_player_name(robbery['target_id'])}，{result}{loot_text}。"
            )
        return lines

    def _history_trade_lines(self) -> list[str]:
        """商路奇闻：贸易、回收和藏宝图事件。"""

        lines: list[str] = []
        best_profit = self._top_lifetime_stat_row(
            "trade_net",
            "trade_records",
            "client_id",
            """
            SUM(
                CASE
                    WHEN action = 'sell' THEN total_price - fee
                    WHEN action = 'buy' THEN -(total_price + fee)
                    ELSE 0
                END
            )
            """,
            "action IN ('buy', 'sell')",
        )
        if best_profit and int(best_profit["total"] or best_profit.get("net") or 0) > 0:
            total = int(best_profit.get("total") or best_profit.get("net") or 0)
            lines.append(f"商路净利最高：{self.format_player_name(best_profit['client_id'])}，累计 {money(total)}。")

        material = self._top_lifetime_stat_row(
            "world_material_quantity",
            "world_material_records",
            "client_id",
            "SUM(quantity)",
        )
        if material and int(material["total"] or 0) > 0:
            lines.append(f"世界物资流转最多：{self.format_player_name(material['client_id'])}，累计 {material['total']} 件。")

        recycle = self._top_recycle_income_row()
        if recycle and int(recycle["total"] or 0) > 0:
            lines.append(f"回收源石最高：{self.format_player_name(recycle['client_id'])}，累计 {money(recycle['total'])}。")

        bid = self.db.fetch_one(
            """
            SELECT t.city_name, t.current_price, t.bid_count, t.status, t.highest_bidder
            FROM treasure_maps AS t
            ORDER BY t.current_price DESC, t.bid_count DESC, t.generated_at DESC
            LIMIT 1
            """
        )
        if bid and int(bid["current_price"] or 0) > 0:
            bidder = self.format_player_name(bid["highest_bidder"]) if str(bid["highest_bidder"] or "") else "无名买主"
            lines.append(
                f"藏宝图最高价：{bid['city_name']} 图，{money(bid['current_price'])}，"
                f"{bid['bid_count']}/10 次出价，当前 {bidder}。"
            )
        return lines

    def _history_wormhole_lines(self) -> list[str]:
        """异界虫洞录：虫洞事件和参与纪录。"""

        lines: list[str] = []
        total = self.db.fetch_one(
            """
            SELECT COUNT(*) AS count,
                   SUM(CASE WHEN status = '已击杀' THEN 1 ELSE 0 END) AS killed,
                   MAX(level) AS max_level
            FROM wormholes
            """
        )
        if total and int(total["count"] or 0) > 0:
            lines.append(
                f"虫洞现世：累计 {total['count']} 次，击破 {int(total['killed'] or 0)} 次，最高 Lv.{int(total['max_level'] or 0)}。"
            )

        first_kill = self.db.fetch_one(
            """
            SELECT boss_name, killed_at, location_name, level
            FROM wormholes
            WHERE status = '已击杀' AND killed_at IS NOT NULL
            ORDER BY killed_at ASC
            LIMIT 1
            """
        )
        if first_kill:
            lines.append(
                f"虫洞首破：「{first_kill['boss_name']}」在 {first_kill['location_name']} 被击破，"
                f"时记 {self._date_text(first_kill['killed_at'])}。"
            )

        hardest = self.db.fetch_one(
            """
            SELECT boss_name, location_name, level, difficulty, status
            FROM wormholes
            ORDER BY level DESC, difficulty DESC, opened_at ASC
            LIMIT 1
            """
        )
        if hardest:
            lines.append(
                f"最高难度：「{hardest['boss_name']}」Lv.{hardest['level']}，"
                f"曾现于 {hardest['location_name']}，结局 {self._status_word(hardest['status'])}。"
            )

        damage = self._top_lifetime_stat_row("wormhole_damage", "wormhole_participants", "client_id", "SUM(damage)")
        if damage and int(damage["total"] or 0) > 0:
            lines.append(f"虫洞伤害留名：{self.format_player_name(damage['client_id'])}，累计 {damage['total']}。")
        return lines

    def _top_lifetime_stat_row(
        self,
        stat_key: str,
        live_table: str,
        client_column: str,
        live_expr: str,
        where: str = "1=1",
    ) -> dict[str, Any] | None:
        """读取长期统计叠加当前明细后的最高玩家。"""

        rows = self._lifetime_stat_rows(stat_key)
        live_rows = self._live_stat_rows(live_table, client_column, live_expr, where)
        totals: dict[str, int] = {}
        for row in rows:
            client_id = str(row["client_id"])
            totals[client_id] = totals.get(client_id, 0) + int(row["total"] or 0)
        for row in live_rows:
            client_id = str(row["client_id"])
            totals[client_id] = totals.get(client_id, 0) + int(row["total"] or 0)
        return self._top_total_from_map(totals)

    def _lifetime_stat_rows(self, stat_key: str) -> list[dict[str, Any]]:
        """读取某个长期统计键的所有玩家值。"""

        return self.db.fetch_all(
            """
            SELECT client_id, stat_value AS total
            FROM player_lifetime_stats
            WHERE stat_key = ? AND stat_value > 0
            """,
            (stat_key,),
        )

    def _live_stat_rows(self, table: str, client_column: str, expr: str, where: str) -> list[dict[str, Any]]:
        """读取当前保留期明细聚合。"""

        if expr == "0" or where == "0":
            return []
        allowed_tables = {
            "exploration_records",
            "trade_records",
            "wormhole_participants",
            "seasonal_boss_participants",
            "duel_records",
            "world_material_records",
        }
        if table not in allowed_tables:
            return []
        return self.db.fetch_all(
            f"""
            SELECT {client_column} AS client_id, COALESCE({expr}, 0) AS total
            FROM {table}
            WHERE {where}
            GROUP BY {client_column}
            HAVING total > 0
            """
        )

    def _top_total_from_map(self, totals: dict[str, int]) -> dict[str, Any] | None:
        """从 client_id -> total 中选出最高有效值。"""

        best_client = ""
        best_total = 0
        for client_id, total in totals.items():
            if total > best_total and self.player(client_id):
                best_client = client_id
                best_total = total
        if not best_client:
            return None
        return {"client_id": best_client, "total": best_total}

    def _better_total_row(self, *rows: dict[str, Any] | None) -> dict[str, Any] | None:
        """从若干 total/net 行里取数值最高者。"""

        best: dict[str, Any] | None = None
        best_total = 0
        for row in rows:
            if not row:
                continue
            total = int(row.get("total") or row.get("net") or 0)
            if total > best_total:
                best = dict(row)
                best["total"] = total
                best_total = total
        return best

    def _top_recycle_income_row(self) -> dict[str, Any] | None:
        """读取武器、宝石、技能书回收收入最高者。"""

        totals: dict[str, int] = {}
        for stat_key in ("weapon_recycle_income", "gem_recycle_income", "book_recycle_income"):
            for row in self._lifetime_stat_rows(stat_key):
                client_id = str(row["client_id"])
                totals[client_id] = totals.get(client_id, 0) + int(row["total"] or 0)

        for table in ("weapon_recycle_records", "gem_recycle_records", "book_recycle_records"):
            rows = self.db.fetch_all(
                f"""
                SELECT client_id, COALESCE(SUM(total_price), 0) AS total
                FROM {table}
                GROUP BY client_id
                HAVING total > 0
                """
            )
            for row in rows:
                client_id = str(row["client_id"])
                totals[client_id] = totals.get(client_id, 0) + int(row["total"] or 0)
        return self._top_total_from_map(totals)

    @staticmethod
    def _date_text(value: Any) -> str:
        """把时间戳裁成适合史册展示的日期文本。"""

        text = str(value or "").replace("T", " ").strip()
        return text[:16] if len(text) >= 16 else text

    def _save_or_get_chronicle(self, day: str, refresh: bool = False) -> list[str]:
        """读取或保存某一天的大事记；当天会刷新，旧日保持沉淀结果。"""

        key = f"{CHRONICLE_KEY_PREFIX}{day}"
        if not refresh:
            row = self.db.fetch_one("SELECT value FROM schema_meta WHERE key = ?", (key,))
            if row:
                return self._decode_entries(row["value"])

        entries = self._build_chronicle_day(day)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO schema_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, dump_json(entries)),
            )
        return entries

    def _build_chronicle_day(self, day: str) -> list[str]:
        """按当天实际数据生成世界年表条目。"""

        start, end = self._business_window(day)
        entries: list[str] = []

        boss = self.db.fetch_one(
            """
            SELECT boss_name, status
            FROM seasonal_boss_events
            WHERE business_day = ?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (day,),
        )
        if boss:
            entries.append(f"今日首领「{boss['boss_name']}」{self._status_word(boss['status'])}。")

        wormholes = self._wormhole_summary(start, end)
        if wormholes["total"]:
            entries.append(
                f"异界虫洞出现 {wormholes['total']} 次，"
                f"其中 {wormholes['killed']} 次被众人合力击破。"
            )

        trade = self._top_trade_row(start, end)
        if trade:
            entries.append(
                f"今日跑商最高收益由 {self.format_player_name(trade['client_id'])} 创下，"
                f"净利润 {money(trade['net'])}。"
            )

        damage = self._top_damage_row(start, end)
        if damage:
            entries.append(
                f"{self.format_player_name(damage['client_id'])} 今日 Boss 总伤害最高，留下 {damage['damage']} 点战绩。"
            )

        luck = self._top_luck_row(start, end)
        if luck:
            entries.append(
                f"{self.format_player_name(luck['original_owner_id'])} 获得 {luck['quality']}武器「{luck['name']}」，"
                "坊间称其手气正盛。"
            )

        new_players = self._count(
            """
            SELECT COUNT(*) AS count
            FROM players
            WHERE datetime(replace(created_at, 'T', ' ')) >= ?
              AND datetime(replace(created_at, 'T', ' ')) < ?
            """,
            (start, end),
        )
        if new_players:
            entries.append(f"今日新增 {new_players} 位道友入世，天枢城又添新灯。")

        return entries or ["今日山河无大事，茶摊照旧开张。"]

    def _top_damage_text(self, start: str, end: str) -> str:
        """今日 Boss 伤害最高者。"""

        row = self._top_damage_row(start, end)
        if not row:
            return "今日最猛：暂无 Boss 挑战记录。"
        return f"今日最猛：{self.format_player_name(row['client_id'])}，Boss 总伤害 {row['damage']}。"

    def _top_rich_text(self) -> str:
        """当前明面资产最高者。"""

        row = self._richest_row()
        if not row:
            return "今日最富：暂无玩家资产记录。"
        return f"今日最富：{self.format_player_name(row['client_id'])}，明面资产约 {money(row['total'])}。"

    def _top_trade_text(self, start: str, end: str) -> str:
        """今日普通跑商净利润最高者。"""

        row = self._top_trade_row(start, end)
        if not row:
            return "今日商魂：暂无普通跑商出售。"
        return f"今日商魂：{self.format_player_name(row['client_id'])}，跑商净利润 {money(row['net'])}。"

    def _top_explore_text(self, start: str, end: str) -> str:
        """今日探险次数最高者。"""

        row = self._top_explore_row(start, end)
        if not row:
            return "今日最勤：暂无探险记录。"
        return f"今日最勤：{self.format_player_name(row['client_id'])}，探险 {row['count']} 次。"

    def _top_luck_text(self, start: str, end: str) -> str:
        """今日珍稀武器获得者。"""

        row = self._top_luck_row(start, end)
        if not row:
            return "今日欧气：暂无珍稀武器入世。"
        return (
            f"今日欧气：{self.format_player_name(row['original_owner_id'])}，"
            f"新得 {row['quality']}武器「{row['name']}」{row['count']} 把。"
        )

    def _top_active_text(self, start: str, end: str) -> str:
        """今日关键行为最多者。"""

        row = self._top_active_row(start, end)
        if not row:
            return "今日活跃：暂无关键日志。"
        return f"今日活跃：{self.format_player_name(row['client_id'])}，关键行为 {row['count']} 次。"

    def _rumor_text(self, start: str, end: str) -> str:
        """生成一条坊间传闻。"""

        luck = self._top_luck_row(start, end)
        if luck:
            return f"坊间传闻：有人看见 {self.format_player_name(luck['original_owner_id'])} 抱着「{luck['name']}」路过茶摊。"

        active = self._top_active_row(start, end)
        if active:
            return f"坊间传闻：茶摊老板说，{self.format_player_name(active['client_id'])} 今日脚步最急。"

        rich = self._richest_row()
        if rich and int(rich["total"]) > 0:
            return f"坊间传闻：商会账房偷偷记下，{self.format_player_name(rich['client_id'])} 的源石声最响。"

        return "坊间传闻：今日风声尚轻，适合先签到，再慢慢探路。"

    def _business_wind_text(self, start: str, end: str) -> str:
        """生成商会风向。"""

        row = self._top_trade_row(start, end)
        if row:
            return (
                f"商会风向：今日普通跑商最高收益来自 {self.format_player_name(row['client_id'])}，"
                f"净利润 {money(row['net'])}。"
            )
        return "商会风向：今日普通跑商账簿还空着，想开张可先看 商场推荐。"

    def _boss_trend_lines(self, day: str, start: str, end: str) -> list[str]:
        """生成首领和虫洞动向。"""

        lines: list[str] = []
        boss = self.db.fetch_one(
            """
            SELECT boss_name, status
            FROM seasonal_boss_events
            WHERE business_day = ?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (day,),
        )
        if boss:
            lines.append(f"首领动向：岁时情劫「{boss['boss_name']}」{self._status_word(boss['status'])}。")
        else:
            lines.append("首领动向：今日暂无岁时情劫现世。")

        wormholes = self._wormhole_summary(start, end)
        if wormholes["total"]:
            lines.append(
                f"虫洞动向：今日出现 {wormholes['total']} 处异界虫洞，"
                f"开启 {wormholes['active']}，击破 {wormholes['killed']}。"
            )
        else:
            lines.append("虫洞动向：今日暂未发现异界虫洞。")
        return lines

    def _top_damage_row(self, start: str, end: str) -> dict[str, Any] | None:
        """查询今日 Boss 总伤害第一名。"""

        return self.db.fetch_one(
            """
            SELECT client_id, SUM(damage) AS damage
            FROM (
                SELECT client_id, damage, updated_at FROM wormhole_participants
                UNION ALL
                SELECT client_id, damage, updated_at FROM seasonal_boss_participants
            )
            WHERE datetime(replace(updated_at, 'T', ' ')) >= ?
              AND datetime(replace(updated_at, 'T', ' ')) < ?
            GROUP BY client_id
            ORDER BY damage DESC
            LIMIT 1
            """,
            (start, end),
        )

    def _richest_row(self) -> dict[str, Any] | None:
        """查询当前明面资产最高者。"""

        return self.db.fetch_one(
            """
            SELECT p.client_id,
                   p.source_stones + COALESCE(v.balance, 0) AS total
            FROM players p
            LEFT JOIN source_vaults v ON v.client_id = p.client_id
            ORDER BY total DESC
            LIMIT 1
            """
        )

    def _top_trade_row(self, start: str, end: str) -> dict[str, Any] | None:
        """查询今日普通跑商净利润第一名。"""

        return self.db.fetch_one(
            """
            SELECT client_id,
                   SUM(
                       CASE
                           WHEN action = 'sell' THEN total_price - fee
                           WHEN action = 'buy' THEN -(total_price + fee)
                           ELSE 0
                       END
                   ) AS net
            FROM trade_records
            WHERE action IN ('buy', 'sell')
              AND datetime(replace(created_at, 'T', ' ')) >= ?
              AND datetime(replace(created_at, 'T', ' ')) < ?
            GROUP BY client_id
            HAVING net > 0
            ORDER BY net DESC
            LIMIT 1
            """,
            (start, end),
        )

    def _top_explore_row(self, start: str, end: str) -> dict[str, Any] | None:
        """查询今日探险次数第一名。"""

        return self.db.fetch_one(
            """
            SELECT client_id, COUNT(*) AS count
            FROM exploration_records
            WHERE datetime(replace(started_at, 'T', ' ')) >= ?
              AND datetime(replace(started_at, 'T', ' ')) < ?
            GROUP BY client_id
            ORDER BY count DESC
            LIMIT 1
            """,
            (start, end),
        )

    def _top_luck_row(self, start: str, end: str) -> dict[str, Any] | None:
        """查询今日最高品质武器获得者。"""

        return self.db.fetch_one(
            """
            SELECT COALESCE(l.original_owner_id, w.holder_id) AS original_owner_id,
                   w.quality,
                   d.name,
                   COUNT(*) AS count
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            LEFT JOIN weapon_legends l ON l.weapon_id = w.weapon_id
            WHERE w.quality IN ('稀品', '珍品')
              AND COALESCE(l.original_owner_id, w.holder_id) NOT LIKE '__%__:%'
              AND datetime(replace(w.created_at, 'T', ' ')) >= ?
              AND datetime(replace(w.created_at, 'T', ' ')) < ?
            GROUP BY original_owner_id, w.quality, d.name
            ORDER BY CASE w.quality WHEN '稀品' THEN 2 ELSE 1 END DESC, count DESC
            LIMIT 1
            """,
            (start, end),
        )

    def _top_active_row(self, start: str, end: str) -> dict[str, Any] | None:
        """查询今日关键行为最多者。"""

        return self.db.fetch_one(
            """
            SELECT client_id, COUNT(*) AS count
            FROM game_logs
            WHERE datetime(replace(created_at, 'T', ' ')) >= ?
              AND datetime(replace(created_at, 'T', ' ')) < ?
            GROUP BY client_id
            ORDER BY count DESC
            LIMIT 1
            """,
            (start, end),
        )

    def _wormhole_summary(self, start: str, end: str) -> dict[str, int]:
        """统计今日虫洞出现、开启和击破数量。"""

        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = '开启' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN status = '已击杀' THEN 1 ELSE 0 END) AS killed
            FROM wormholes
            WHERE datetime(replace(opened_at, 'T', ' ')) >= ?
              AND datetime(replace(opened_at, 'T', ' ')) < ?
            """,
            (start, end),
        )
        return {
            "total": int(row["total"] or 0) if row else 0,
            "active": int(row["active"] or 0) if row else 0,
            "killed": int(row["killed"] or 0) if row else 0,
        }

    def _count(self, sql: str, params: tuple[Any, ...]) -> int:
        """执行 COUNT 查询并返回整数。"""

        row = self.db.fetch_one(sql, params)
        return int(row["count"] or 0) if row else 0

    @staticmethod
    def _decode_entries(value: Any) -> list[str]:
        """把 schema_meta 里的大事记 JSON 还原成文本列表。"""

        entries = load_json(value, [])
        if isinstance(entries, list):
            return [str(entry) for entry in entries if str(entry).strip()]
        return [str(value)]

    @staticmethod
    def _status_word(status: str) -> str:
        """把数据库状态转成早报里的短句。"""

        return {
            "开启": "正在现世",
            "已击破": "已被合力击破",
            "已击杀": "已被合力击破",
            "已退去": "已退去",
        }.get(status, str(status))

    @staticmethod
    def _business_window(day: str | None = None) -> tuple[str, str]:
        """返回业务日开始和结束时间。"""

        value = date.fromisoformat(day or business_day())
        start = datetime.combine(value, time(hour=DAY_RESET_HOUR))
        end = start + timedelta(days=1)
        return start.isoformat(sep=" ", timespec="seconds"), end.isoformat(sep=" ", timespec="seconds")


service = XiuxianHistoryService(db)

__all__ = ["XiuxianHistoryService", "service"]
