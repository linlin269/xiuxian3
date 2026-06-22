"""探险组件服务。"""

from __future__ import annotations

from datetime import timedelta

from .. import combat_log_text
from ..combat_core import service as combat_service
from ..common import CoreService, dump_json, load_json, now, random, ts, weapon_id_label
from ..constants import ENCOUNTER_SECONDS, EXPLORE_MINUTES, MAX_LEVEL, WORLD_COORD_MAX, WORLD_COORD_MIN
from ..format_text import T
from ..sect_war import sect_direction_bonus_conn
from ..sql import db
from ..weapon_core import service as weapon_service
from ..world_materials import WorldMaterialService
from ..battle_log_links import battle_log_markdown
from ..异火.service import service as flame_service


SECRET_REALM_LOCATIONS = {"太虚秘境"}
SECRET_REALM_FIRST_WEAPON_CHECK_ROUND = 3
SECRET_REALM_WEAPON_ROUND_SIZE = 3
SECRET_REALM_MAX_ENCOUNTERS = 30
SECRET_REALM_EXP_RATE = 0.25
SECRET_REALM_GEM_CHANCE = 0.25
SECRET_REALM_GEM_LEVELS = [1, 2, 3, 4, 5, 6, 7, 8]
SECRET_REALM_GEM_LEVEL_WEIGHTS = [50, 25, 12, 7, 3, 2, 0.8, 0.2]
MONSTER_KIND_LOOT_POOLS = {
    "妖": (
        ("loot_yao_2", 28),
        ("loot_yao_3", 24),
        ("loot_yao_4", 24),
        ("loot_yao_1", 15),
        ("loot_yao_5", 11),
        ("loot_yao_6", 8),
    ),
    "魔": (
        ("loot_mo_4", 26),
        ("loot_mo_3", 22),
        ("loot_mo_2", 18),
        ("loot_mo_1", 14),
        ("loot_mo_6", 11),
        ("loot_mo_5", 8),
    ),
    "鬼": (
        ("loot_gui_2", 28),
        ("loot_gui_4", 24),
        ("loot_gui_5", 20),
        ("loot_gui_1", 14),
        ("loot_gui_3", 9),
        ("loot_gui_6", 7),
    ),
    "龙": (
        ("loot_long_1", 28),
        ("loot_long_2", 16),
        ("loot_long_3", 5),
    ),
    "兽": (
        ("loot_shou_2", 26),
        ("loot_shou_3", 24),
        ("loot_shou_4", 22),
        ("loot_shou_1", 16),
        ("loot_shou_5", 10),
        ("loot_shou_6", 7),
    ),
    "兵": (
        ("loot_bing_2", 24),
        ("loot_bing_3", 20),
        ("loot_bing_4", 18),
        ("loot_bing_6", 12),
        ("loot_bing_5", 10),
        ("loot_bing_1", 5),
    ),
    "傀": (
        ("loot_bing_2", 28),
        ("loot_bing_3", 18),
        ("loot_bing_4", 16),
        ("loot_bing_5", 12),
        ("loot_bing_6", 10),
    ),
}
SECRET_REALM_ENVIRONMENTS = (
    ("幽冥风", "阴风压魂，怪物精神压迫更重。", {"hp": 1.00, "attack": 1.05, "defense": 0.96}),
    ("镜天影", "镜影错乱，遭遇强度起伏更大。", {"hp": 0.96, "attack": 1.02, "defense": 1.02}),
    ("龙骨尘", "龙骨尘暴翻涌，怪物血防更厚。", {"hp": 1.10, "attack": 0.98, "defense": 1.08}),
    ("星火雨", "星火落如雨，怪物攻势更烈。", {"hp": 0.98, "attack": 1.10, "defense": 0.98}),
    ("归墟潮", "归墟潮汐反复，战斗更拖长。", {"hp": 1.06, "attack": 1.00, "defense": 1.04}),
)
FEATURE_LABELS = {
    "trade": "商路",
    "explore": "探险",
    "special_buyer": "特殊收购",
    "recycle:weapon": "武器回收",
    "recycle:gem": "宝石回收",
    "recycle:book": "技能书回收",
}
RECYCLE_TYPE_LABELS = {
    "weapon": "武器",
    "gem": "宝石",
    "book": "技能书",
}


class ExplorationService(CoreService):
    """30 分钟探险预计算和领取。"""

    def __init__(self, database) -> None:
        super().__init__(database)
        self.world_material = WorldMaterialService(database)

    def locations(self, client_id: str) -> str:
        """查看探险地点。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all("SELECT * FROM exploration_locations ORDER BY recommended_level, name")
        panel = T.panel()
        panel.section("探险地图")
        panel.line(f"范围：左下角 ({WORLD_COORD_MIN},{WORLD_COORD_MIN})｜右上角 ({WORLD_COORD_MAX},{WORLD_COORD_MAX})")
        panel.line("普通地点同时承担探险、商路和城池状态；太虚秘境是特殊战斗点。")
        panel.hr()
        panel.section("普通城池")
        for row in rows:
            if self._is_secret_realm(row["name"]):
                continue
            specialties = self._trade_specialties_text(str(row["name"]))
            panel.line(
                f"{row['name']} ({row['x']},{row['y']})｜推荐 **Lv.{row['recommended_level']}**｜"
                f"怪物 Lv.{row['min_level']}-{row['max_level']}｜特产：{specialties}"
            )
            state_lines = self.world_material.city_state_lines(str(row["name"]), compact=True)
            if state_lines:
                panel.line("城池：" + " ".join(state_lines))
        secret_rows = [row for row in rows if self._is_secret_realm(row["name"])]
        if secret_rows:
            panel.hr()
            panel.section("特殊秘境")
            for row in secret_rows:
                panel.line(
                    f"{row['name']} ({row['x']},{row['y']})｜动态映身｜"
                    f"推荐 **Lv.{row['recommended_level']}**｜{row['desc']}"
                )
        buttons = [f"导航 {row['name']}" for row in rows]
        buttons.extend(["地图", "商场推荐"])
        return panel.render() + T.buttons(*buttons)

    def current_location(self, client_id: str) -> str:
        """查看当前位置。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        x = int(player["x"])
        y = int(player["y"])
        location_name = str(player["location_name"])
        world_point = self._world_point_at(x, y)
        exploration = self._exploration_location_at(x, y)
        trade = self._trade_location_at(x, y)
        special_buyer = self._special_buyer_at(x, y)
        recycle = self._recycle_location_at(x, y)
        sect = self._sect_at(x, y)
        treasure = self._treasure_pickup_at(x, y)

        panel = T.panel()
        panel.section("地图·当前位置")
        panel.line(f"{location_name} ({x},{y})")
        if world_point:
            features = self._feature_text(load_json(world_point["features"], []))
            panel.line(f"地貌：{world_point['terrain']}｜类型：{world_point['category']}｜功能：{features}")
            if str(world_point.get("desc") or "").strip():
                panel.line(f"说明：{world_point['desc']}")
        else:
            panel.line("地貌：荒野｜类型：自由坐标｜功能：暂无固定建筑")
            panel.line("荒野可以作为宗门山门坐标；若要探险或跑商，先导航到普通城池。")

        buttons: list[str] = []
        if exploration:
            if self._is_secret_realm(str(exploration["name"])):
                panel.hr()
                panel.section("秘境")
                panel.line("太虚秘境按玩家等级动态映身，主要产出高阶宝石和全池武器。")
            else:
                panel.hr()
                panel.section("探险")
                panel.line(
                    f"推荐 **Lv.{exploration['recommended_level']}**｜"
                    f"怪物 Lv.{exploration['min_level']}-{exploration['max_level']}｜{exploration['desc']}"
                )
            buttons.append("探险")

        if trade:
            panel.hr()
            panel.section("商路城池")
            panel.line(f"本地特产：{self._trade_specialties_text(str(trade['name']))}")
            state_lines = self.world_material.city_state_lines(str(trade["name"]), compact=True)
            if state_lines:
                panel.lines(state_lines)
            buttons.extend(["商场推荐", "自动出售"])

        if special_buyer:
            panel.hr()
            panel.section("特殊收购")
            panel.line(f"{special_buyer['buyer_name']}：收购对应战利品，出售会自动按今日价格曲线结算。")
            buttons.append("自动出售")

        if recycle:
            panel.hr()
            panel.section("回收建筑")
            recycle_type = str(recycle["recycle_type"])
            label = RECYCLE_TYPE_LABELS.get(recycle_type, recycle_type)
            panel.line(f"{recycle['name']}：回收{label}，当前系数 {float(recycle['price_factor']):.2f}。")
            if recycle_type == "weapon":
                buttons.append("出售全部 武器")
            elif recycle_type == "gem":
                buttons.append("出售全部 宝石")
            elif recycle_type == "book":
                buttons.append("出售全部 技能书")

        if sect:
            panel.hr()
            panel.section("宗门山门")
            panel.line(f"{sect['name']}｜宗主：{self._player_name(str(sect['master_client_id']))}｜成员：{self._sect_member_count(int(sect['sect_id']))}")
            buttons.extend([f"加入宗门 {sect['name']}", "宗门"])

        if treasure:
            panel.hr()
            panel.section("藏宝图")
            panel.line(f"{treasure['city_name']}旧藏散落于此：{treasure['weapon_name']}[稀品] 上限{treasure['weapon_max_level']}。")
            buttons.extend(["领取藏宝图", "藏宝图"])

        if not world_point and not sect:
            buttons.extend(["导航 天枢城", "探险列表"])
        else:
            buttons.append("探险列表")
        return panel.render() + T.buttons(*buttons)

    def start(self, client_id: str, location_name: str = "") -> str:
        """开始探险。

        不带地点时使用玩家当前位置；带地点时先切到该探险地点再开始。
        移动没有时间成本，所以这里直接更新位置，避免用户还要先发一次导航。
        """

        player, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()
        assert player is not None
        active = self._active_record(client_id)
        if active:
            return T.hint("你已经在探险中或有待领取结果。", "发送：探险状态 查看进度；30 分钟后发送：结束探险<探险状态><结束探险>")
        if player["status"] != "空闲":
            return T.hint(f"当前状态为 {player['status']}，不能开始探险。", "先处理当前状态")
        if player["hp"] <= 0:
            return T.hint("血气不足，不能开始探险。", "发送：休息，时间到后发送：结束休息")
        target_name = location_name.strip() or player["location_name"]
        location = self._exploration_location(target_name)
        if not location:
            if location_name.strip():
                return T.hint(f"没有找到探险地点：{target_name}。", "发送：探险列表 查看可探险地点。<探险列表>")
            return T.hint("当前位置不是探险地点。", "发送：探险列表 查看可探险地点，或发送：探险 地点名<探险列表>")

        weapon_service.ensure_starter_weapon(client_id)
        explore_player = dict(player)
        explore_player["location_name"] = location["name"]
        explore_player["x"] = location["x"]
        explore_player["y"] = location["y"]
        result = self._precompute(client_id, explore_player)
        started = now()
        ready = started + timedelta(seconds=self._result_duration_seconds(result))
        with self.db.transaction() as conn:
            active = conn.execute(
                """
                SELECT 1 FROM exploration_records
                WHERE client_id = ? AND claimed = 0
                LIMIT 1
                """,
                (client_id,),
            ).fetchone()
            if active:
                return T.hint("你已经在探险中或有待领取结果。", "发送：探险状态 查看进度；30 分钟后发送：结束探险<探险状态><结束探险>")
            fresh_player = conn.execute(
                "SELECT status, hp FROM players WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not fresh_player or fresh_player["status"] != "空闲":
                return T.hint("当前状态已变化，不能开始探险。", "发送：修仙信息 查看当前状态后再操作。<修仙信息>")
            if int(fresh_player["hp"]) <= 0:
                return T.hint("血气不足，不能开始探险。", "发送：休息，时间到后发送：结束休息<休息>")
            for item_id, quantity in result.get("medicine_used", {}).items():
                row = conn.execute(
                    "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
                    (client_id, item_id),
                ).fetchone()
                if not row or int(row["quantity"]) < int(quantity):
                    return T.hint("自动用药库存已变化，无法开始探险。", "发送：纳戒 确认恢复药数量后，再发送：探险<纳戒>")
            cursor = conn.execute(
                """
                UPDATE players
                SET status = '探险中', location_name = ?, x = ?, y = ?
                WHERE client_id = ? AND status = '空闲' AND hp > 0
                """,
                (location["name"], location["x"], location["y"], client_id),
            )
            if cursor.rowcount <= 0:
                return T.hint("当前状态已变化，不能开始探险。", "发送：修仙信息 查看当前状态后再操作。<修仙信息>")
            for item_id, quantity in result.get("medicine_used", {}).items():
                self.remove_ring_conn(conn, client_id, item_id, int(quantity))
            conn.execute(
                """
                INSERT INTO exploration_records
                (client_id, location_name, status, started_at, ready_at, result)
                VALUES (?, ?, '探险中', ?, ?, ?)
                """,
                (client_id, location["name"], ts(started), ts(ready), dump_json(result)),
            )
        auto_state = "开启" if player["auto_use_medicine"] else "关闭"
        duration_text = self._result_duration_text(result)
        return f"开始探险：{location['name']}。自动用药：{auto_state}。{duration_text}后可结算。<探险状态>"

    def status(self, client_id: str) -> str:
        """查看探险状态。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        record = self._active_record(client_id)
        if not record:
            return T.hint("当前没有探险。", "发送：探险列表 选地点，再发送：探险<探险列表>")
        result = load_json(record["result"], {})
        events = list(result.get("events", []))
        current = now()
        elapsed = max(0, int((current - self._time(record["started_at"])).total_seconds()))
        total = self._result_duration_seconds(result)
        done = min(total, elapsed)
        if result.get("dead"):
            state = "已重伤，预计算已停止"
            reason = "本体战斗失败，后续不再继续遇怪。"
        elif result.get("bag_full"):
            state = "背包已满，预计算已停止"
            reason = "下一件背包掉落已经装不下，后续不再继续遇怪。"
        elif elapsed >= total:
            state = "已到点，待领取"
            reason = f"{self._seconds_text(total)}已到，可以领取本次探险结果。"
        else:
            state = "探险中"
            reason = f"预计算已经完成，但本体仍需等 {self._seconds_text(total)} 冷却到点。"
        ready_at = self._effective_ready_at(record, result)
        can_claim = not (ready_at and current < ready_at)
        if can_claim:
            action = "现在可以发送：结束探险"
            time_text = "可领取"
        else:
            left = max(1, int((ready_at - current).total_seconds() // 60) + 1)
            action = f"{left} 分钟后发送：结束探险"
            time_text = f"还需约 {left} 分钟"

        display_player = self._result_display_player(result, player)
        summary = self._status_summary(display_player, result, events)
        panel = T.panel()
        panel.section(f"探险状态·{record['location_name']}")
        panel.line(f"状态：{state}")
        panel.line(f"时间：已过 **{self._seconds_text(done)}/{self._seconds_text(total)}**｜{time_text}")
        if isinstance(result.get("secret_realm"), dict):
            realm = result["secret_realm"]
            panel.line(f"秘境：{realm.get('name', '未知')}｜最高强度 {self._secret_realm_result_level_text(realm)}")
        panel.line(f"原因：{reason}")
        panel.line(f"战斗：{summary['fight_text']}")
        panel.line(f"预计经验：**+{summary['exp_total']}**")
        panel.line(f"预计武器经验：**+{summary['weapon_exp_total']}**")
        panel.line(f"最后状态：血气 **{summary['hp_left']}/{display_player['max_hp']}**｜精神 **{summary['mp_left']}/{display_player['max_mp']}**")
        if summary["drop_text"]:
            panel.line(f"预计收获：{summary['drop_text']}")
        else:
            panel.line("预计收获：暂无掉落")
        if summary["medicine_text"]:
            panel.line(f"自动用药：{summary['medicine_text']}")
        return T.attach(panel.render(), f"{action}<结束探险>")

    def claim(self, client_id: str) -> str | dict:
        """领取探险结果。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        record = self._active_record(client_id)
        if not record:
            return T.hint("当前没有可领取探险。", "发送：探险 开始一轮，或发送：探险记录 查看历史。<探险>")
        result = load_json(record["result"], {})
        stored_ready_at = self._time(record["ready_at"])
        ready_at = self._effective_ready_at(record, result)
        if ready_at < stored_ready_at:
            record = dict(record)
            record["ready_at"] = ts(ready_at)
        if ready_at and now() < ready_at:
            left = max(1, int((ready_at - now()).total_seconds() // 60) + 1)
            if result.get("secret_realm"):
                return T.hint(f"秘境冷却未结束，{left} 分钟后才能结束探险。", "先发送：探险状态 查看预计算结果。<探险状态>")
            return T.hint(f"探险还没有到 30 分钟冷却，{left} 分钟后才能结束探险。", "先发送：探险状态 查看预计算结果。<探险状态>")
        with self.db.transaction() as conn:
            active = conn.execute(
                "SELECT * FROM exploration_records WHERE record_id = ? AND claimed = 0",
                (record["record_id"],),
            ).fetchone()
            if not active:
                return T.hint("当前没有可领取探险。", "发送：探险 开始一轮，或发送：探险记录 查看历史。<探险>")
            record = dict(active)
            result = load_json(record["result"], {})
            ready_at = self._effective_ready_at(record, result)
            if ready_at and now() < ready_at:
                left = max(1, int((ready_at - now()).total_seconds() // 60) + 1)
                if result.get("secret_realm"):
                    return T.hint(f"秘境冷却未结束，{left} 分钟后才能结束探险。", "先发送：探险状态 查看预计算结果。<探险状态>")
                return T.hint(f"探险还没有到 30 分钟冷却，{left} 分钟后才能结束探险。", "先发送：探险状态 查看预计算结果。<探险状态>")
            if ts(ready_at) != str(record["ready_at"]):
                record["ready_at"] = ts(ready_at)
                conn.execute(
                    "UPDATE exploration_records SET ready_at = ? WHERE record_id = ? AND claimed = 0",
                    (record["ready_at"], record["record_id"]),
                )
            settlement = self._claim_settlement(player, result)
            events = settlement["events"]
            exp_total = settlement["exp_total"]
            weapon_exp_total = settlement["weapon_exp_total"]
            drops = settlement["drops"]
            ring_drops = settlement["ring_drops"]
            hp_left = settlement["hp_left"]
            mp_left = settlement["mp_left"]
            dead = settlement["dead"]
            weapon_drops: list[str] = []
            for item_id, quantity in drops.items():
                ok, reason = self.can_add_backpack_conn(conn, client_id, item_id, quantity)
                if not ok:
                    return T.hint("背包空间不足，无法领取探险结果。", f"{reason}<自动出售>")
            old_level, new_level = self.add_exp_conn(conn, client_id, exp_total)
            for item_id, quantity in drops.items():
                self.add_backpack_conn(conn, client_id, item_id, quantity)
            for item_id, quantity in ring_drops.items():
                ring_id, ring_level = self._parse_ring_drop_key(item_id)
                if ring_level > 0:
                    self.add_gem_conn(conn, client_id, ring_id, ring_level, quantity)
                else:
                    self.add_ring_conn(conn, client_id, ring_id, quantity)
            for event in events:
                self.record_weapon_combat_conn(
                    conn,
                    client_id,
                    int(event.get("weapon_id", 0)),
                    monster_kill=bool(event.get("win")),
                    damage=int(event.get("highest_damage", 0)),
                    weapon_exp=int(event.get("weapon_exp", 0)),
                )
            for drop in self._weapon_drops_from_result(result):
                if not drop:
                    continue
                weapon_id = weapon_service.create_weapon_conn(
                    conn,
                    client_id,
                    drop["weapon_def_id"],
                    drop["quality"],
                    drop["max_level"],
                    equipped=False,
                )
                weapon_drops.append(f"{weapon_id_label(weapon_id)} {drop['name']}[{drop['quality']}] 上限{drop['max_level']}")
            # --- 异火掉落判定 ---
            flame_result = flame_service.roll_explore_flame(conn, client_id)
            flame_text = ""
            if flame_result.get("granted"):
                flame_text = flame_result["text"]
            elif flame_result.get("compensation"):
                comp = flame_result["compensation"]
                flame_text = flame_service.compensation_text(comp["flame_name"], comp.get("reason", ""))
            final_hp = max(1, hp_left)
            final_mp = 0 if hp_left <= 0 else max(0, mp_left)
            conn.execute(
                """
                UPDATE players
                SET hp = ?,
                    mp = ?,
                    status = CASE WHEN status = '探险中' THEN '空闲' ELSE status END
                WHERE client_id = ?
                """,
                (final_hp, final_mp, client_id),
            )
            if not result.get("secret_realm"):
                self.reset_rest_window_conn(conn, client_id, final_hp, final_mp)
            conn.execute(
                """
                UPDATE exploration_records
                SET status = '已领取', finished_at = ?, claimed = 1
                WHERE record_id = ?
                """,
                (ts(), record["record_id"]),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '领取探险', ?, ?)",
                (
                    client_id,
                    (
                        f"record_id={record['record_id']}, location={record['location_name']}, "
                        f"exp={exp_total}, weapon_exp={weapon_exp_total}, level={old_level}->{new_level}, "
                        f"items={sum(drops.values()) + sum(ring_drops.values())}, "
                        f"weapons={len(weapon_drops)}, dead={int(dead)}"
                    ),
                    ts(),
                ),
            )

        final_player = self.player(client_id) or player
        log_player = self._result_display_player(result, final_player)
        log = self._claim_log_block(
            record=record,
            player=log_player,
            result=result,
            events=events,
            exp_total=exp_total,
            weapon_exp_total=weapon_exp_total,
            old_level=old_level,
            new_level=new_level,
            drops=drops,
            ring_drops=ring_drops,
            weapon_drops=weapon_drops,
            medicine_used=result.get("medicine_used", {}),
            dead=dead,
            bag_full=bool(result.get("bag_full")),
        )
        # 追加异火掉落信息到结算消息
        if flame_text:
            if isinstance(log, str):
                log = log + f"\n🔥 {flame_text}"
            elif isinstance(log, dict):
                log.setdefault("extra", []).append(flame_text)
        return log

    def records(self, client_id: str) -> str:
        """查看最近探险记录。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()
        rows = self.db.fetch_all(
            """
            SELECT * FROM exploration_records
            WHERE client_id = ?
            ORDER BY started_at DESC
            LIMIT 5
            """,
            (client_id,),
        )
        if not rows:
            return T.hint("暂无探险记录。", "发送：探险 开始第一次探险。<探险>")
        panel = T.panel()
        panel.section("探险记录")
        for row in rows:
            panel.line(f"〔{row['record_id']}〕｜{row['location_name']}｜{row['status']}｜{row['started_at']}")
        return panel.render()

    def _precompute(self, client_id: str, player: dict) -> dict:
        """预计算 30 分钟探险事件。"""

        if self._is_secret_realm(player["location_name"]):
            return self._precompute_secret_realm(client_id, player)

        min_level, max_level = self._monster_level_range(player)
        monsters = self.db.fetch_all(
            """
            SELECT * FROM monster_defs
            WHERE level BETWEEN ? AND ?
            ORDER BY level
            """,
            (min_level, max_level),
        )
        if not monsters:
            monsters = self.db.fetch_all(
                "SELECT * FROM monster_defs WHERE level BETWEEN ? AND ? ORDER BY level",
                (max(1, player["level"] - 5), max(5, player["level"] + 8)),
            )
        if not monsters:
            monsters = self.db.fetch_all("SELECT * FROM monster_defs ORDER BY level LIMIT 1")
        fight_count = max(1, EXPLORE_MINUTES * 60 // ENCOUNTER_SECONDS)
        events = []
        hp_left = player["hp"]
        mp_left = player["mp"]
        weight_now = self.backpack_weight(client_id)
        item_kinds = {row["item_id"] for row in self.backpack_rows(client_id)}
        pending_drops: dict[str, int] = {}
        explore_bonus = min(0.2, self.equipment_bonuses(client_id).get("explore_bonus", 0))
        medicine_stock = self._medicine_stock(client_id) if player["auto_use_medicine"] else {}
        medicine_used: dict[str, int] = {}
        for _event_index in range(fight_count):
            hp_left, mp_left = self._auto_use_medicine(
                hp_left,
                mp_left,
                player,
                medicine_stock,
                medicine_used,
            )
            monster = dict(random.choice(monsters))
            monster["drop_item_id"] = self._roll_monster_loot_item(monster)
            event = combat_service.fight_monster(
                client_id,
                monster,
                start_hp=hp_left,
                start_mp=mp_left,
            )
            hp_left = int(event.get("hp_left", hp_left))
            mp_left = int(event.get("mp_left", mp_left))
            event["hp_left"] = hp_left
            event["mp_left"] = mp_left
            if hp_left <= 0:
                events.append(event)
                return self._precompute_result(
                    player,
                    events,
                    medicine_used,
                    explore_bonus,
                    dead=True,
                    bag_full=False,
                    client_id=client_id,
                )
            hp_left, mp_left = self._auto_use_medicine(
                hp_left,
                mp_left,
                player,
                medicine_stock,
                medicine_used,
            )
            event["hp_left"] = hp_left
            event["mp_left"] = mp_left
            if event.get("drop_item_id"):
                can_take, weight_now = self._take_drop_preview(
                    player,
                    event["drop_item_id"],
                    weight_now,
                    item_kinds,
                    pending_drops,
                )
                if not can_take:
                    event["drop_item_id"] = ""
                    event["bag_full"] = True
                    events.append(event)
                    return self._precompute_result(
                        player,
                        events,
                        medicine_used,
                        explore_bonus,
                        dead=False,
                        bag_full=True,
                        client_id=client_id,
                    )
            if random.random() < 0.22 + explore_bonus * 0.5:
                location_drop = self._roll_location_drop(player["location_name"])
                if location_drop:
                    can_take, weight_now = self._take_drop_preview(
                        player,
                        location_drop,
                        weight_now,
                        item_kinds,
                        pending_drops,
                    )
                    if not can_take:
                        event["bag_full"] = True
                        events.append(event)
                        return self._precompute_result(
                            player,
                            events,
                            medicine_used,
                            explore_bonus,
                            dead=False,
                            bag_full=True,
                            client_id=client_id,
                        )
                    event["location_drop_item_id"] = location_drop
            if random.random() < 0.16 + explore_bonus * 0.3:
                event["ring_drop_id"] = self._roll_ring_drop(
                    player["location_name"],
                    0.004 if self._is_secret_realm(player["location_name"]) else 0.0,
                )
            events.append(event)
        return self._precompute_result(
            player,
            events,
            medicine_used,
            explore_bonus,
            dead=False,
            bag_full=False,
            client_id=client_id,
        )

    def _precompute_secret_realm(self, client_id: str, player: dict) -> dict:
        """预计算太虚秘境：怪物按玩家等级浮动，日志和时长独立。"""

        templates = self.db.fetch_all("SELECT * FROM monster_defs ORDER BY level")
        if not templates:
            templates = self.db.fetch_all("SELECT * FROM monster_defs ORDER BY level LIMIT 1")
        events = []
        hp_left = player["hp"]
        mp_left = player["mp"]
        explore_bonus = min(0.2, self.equipment_bonuses(client_id).get("explore_bonus", 0))
        medicine_stock = self._medicine_stock(client_id) if player["auto_use_medicine"] else {}
        medicine_used: dict[str, int] = {}
        environment = self._secret_realm_environment()

        for _ in range(SECRET_REALM_MAX_ENCOUNTERS):
            hp_left, mp_left = self._auto_use_medicine(hp_left, mp_left, player, medicine_stock, medicine_used)
            monster = self._secret_realm_actor(player, templates, environment)
            event = combat_service.fight_secret_realm_actor(client_id, monster, start_hp=hp_left, start_mp=mp_left)
            self._scale_secret_realm_exp(event)
            event["secret_realm"] = True
            event["secret_environment"] = environment["name"]
            event["secret_environment_desc"] = environment["desc"]
            event["display_level"] = monster["display_level"]
            event["void_power"] = monster["void_power"]
            event["raw_level"] = monster["raw_level"]
            event["monster_label"] = self._secret_realm_level_label(monster)
            hp_left = int(event.get("hp_left", hp_left))
            mp_left = int(event.get("mp_left", mp_left))
            event["hp_left"] = hp_left
            event["mp_left"] = mp_left
            if hp_left <= 0:
                events.append(event)
                return self._precompute_result(
                    player,
                    events,
                    medicine_used,
                    explore_bonus,
                    dead=True,
                    bag_full=False,
                    client_id=client_id,
                    secret_realm=environment,
                )

            hp_left, mp_left = self._auto_use_medicine(hp_left, mp_left, player, medicine_stock, medicine_used)
            event["hp_left"] = hp_left
            event["mp_left"] = mp_left

            if random.random() < SECRET_REALM_GEM_CHANCE + explore_bonus * 0.3:
                gem_drop = self._roll_secret_realm_gem_drop()
                if gem_drop:
                    event["ring_drop_id"] = gem_drop["gem_id"]
                    event["ring_drop_level"] = gem_drop["level"]
            if random.random() < 0.18 + explore_bonus * 0.25:
                location_drop = self._roll_secret_realm_location_drop()
                if location_drop:
                    event["location_drop_item_id"] = location_drop
            events.append(event)

        return self._precompute_result(
            player,
            events,
            medicine_used,
            explore_bonus,
            dead=False,
            bag_full=False,
            client_id=client_id,
            secret_realm=environment,
        )

    def _precompute_result(
        self,
        player: dict,
        events: list[dict],
        medicine_used: dict[str, int],
        explore_bonus: float,
        *,
        dead: bool,
        bag_full: bool,
        client_id: str = "",
        secret_realm: dict | None = None,
    ) -> dict:
        """整理预计算结果，并判定本轮武器掉落。

        普通探险保持整轮判定一次；秘境按若干场为一关反复判定，
        避免长秘境只在开局抽一次，后续失去随机探索感。
        """

        result = {
            "dead": dead,
            "bag_full": bag_full,
            "medicine_used": medicine_used,
            "events": events,
            "player_snapshot": self._player_snapshot(player),
            "duration_seconds": (
                max(ENCOUNTER_SECONDS, len(events) * ENCOUNTER_SECONDS)
                if secret_realm
                else EXPLORE_MINUTES * 60
            ),
        }
        if secret_realm:
            result["secret_realm"] = {
                "name": secret_realm["name"],
                "desc": secret_realm["desc"],
                "duration_seconds": result["duration_seconds"],
                "highest_void_power": max((int(event.get("void_power", 0)) for event in events), default=0),
                "highest_display_level": max((int(event.get("display_level", 0)) for event in events), default=0),
            }
        if client_id:
            result["combat_snapshot"] = self._combat_snapshot(client_id, player)
        weapon_drops = self._roll_weapon_drops(player, events, explore_bonus)
        if weapon_drops:
            result["weapon_drops"] = weapon_drops
            if len(weapon_drops) == 1:
                result["weapon_drop"] = weapon_drops[0]
        return result

    def _roll_weapon_drops(self, player: dict, events: list[dict], explore_bonus: float) -> list[dict]:
        """按探险类型生成武器预掉落。"""

        if not self._is_secret_realm(player["location_name"]):
            if not any(event.get("win") for event in events):
                return []
            if random.random() < self._weapon_drop_chance(explore_bonus):
                return [weapon_service.roll_weapon_drop(player["location_name"])]
            return []

        drops = []
        for stage_events in self._secret_realm_weapon_check_stages(events):
            if stage_events and random.random() < self._secret_realm_weapon_drop_chance(explore_bonus):
                drops.append(weapon_service.roll_weapon_drop(""))
        return drops

    @staticmethod
    def _secret_realm_weapon_check_stages(events: list[dict]) -> list[list[dict]]:
        """读取秘境武器判定段。

        每满 3 场判定一次，也就是第 3、6、9...30 场后判断。
        """

        first_check = SECRET_REALM_FIRST_WEAPON_CHECK_ROUND
        round_size = max(1, SECRET_REALM_WEAPON_ROUND_SIZE)
        if len(events) < first_check:
            return []

        stages = []
        for start in range(0, len(events), round_size):
            stage = events[start : start + round_size]
            if len(stage) == round_size:
                stages.append(stage)
        return stages

    @staticmethod
    def _player_snapshot(player: dict) -> dict:
        """保存本轮探险开始时会影响展示的玩家状态。"""

        fields = (
            "client_id",
            "display_name",
            "level",
            "exp",
            "hp",
            "mp",
            "max_hp",
            "max_mp",
            "base_attack",
            "defense",
            "source_stones",
            "status",
            "location_name",
            "x",
            "y",
            "auto_use_medicine",
            "battle_log_detail",
        )
        return {key: player[key] for key in fields if key in player}

    def _combat_snapshot(self, client_id: str, player: dict) -> dict:
        """保存探险开始时用于玩家对战的完整战斗快照。"""

        weapon = weapon_service.equipped_weapon(client_id)
        skill = weapon_service.skill(weapon["skill_id"]) if weapon else None
        equipment_effects = self.equipment_bonuses(client_id)
        weapon_effects = self._weapon_effects(weapon)
        return {
            "player": self._player_snapshot(player),
            "weapon": dict(weapon) if weapon else {},
            "skill": dict(skill) if skill else {},
            "skill_label": weapon_service.weapon_skill_label(int(weapon["weapon_id"]), skill) if weapon and skill else "",
            "equipment_effects": equipment_effects,
            "weapon_effects": weapon_effects,
            "effects": self._merge_effects(equipment_effects, weapon_effects),
        }

    @staticmethod
    def _result_display_player(result: dict, player: dict) -> dict:
        """用开始快照修正探险展示用状态，避免领取时面板变化影响日志。"""

        snapshot = result.get("player_snapshot")
        if not isinstance(snapshot, dict):
            return player

        display_player = dict(player)
        for key in ("level", "max_hp", "max_mp", "location_name", "x", "y", "auto_use_medicine"):
            if key in snapshot:
                display_player[key] = snapshot[key]
        return display_player

    @staticmethod
    def _weapon_drop_chance(explore_bonus: float) -> float:
        """整轮探险武器掉落概率。"""

        return max(0.0, min(0.55, 0.35 + explore_bonus))

    @staticmethod
    def _secret_realm_weapon_drop_chance(explore_bonus: float) -> float:
        """秘境每关武器掉落概率。"""

        return max(0.0, min(0.26, 0.20 + explore_bonus * 0.3))

    @staticmethod
    def _is_secret_realm(location_name: str) -> bool:
        """判断是否使用秘境规则。"""

        return location_name in SECRET_REALM_LOCATIONS

    @staticmethod
    def _result_duration_seconds(result: dict) -> int:
        """读取本轮探险实际冷却时长。"""

        return max(ENCOUNTER_SECONDS, int(result.get("duration_seconds") or EXPLORE_MINUTES * 60))

    def _result_duration_text(self, result: dict) -> str:
        """读取本轮探险实际冷却文本。"""

        return self._seconds_text(self._result_duration_seconds(result))

    def _effective_ready_at(self, record: dict, result: dict):
        """读取有效领取时间，避免异常 ready_at 超过本轮规则时长。"""

        ready_at = self._time(record["ready_at"])
        started_at = self._time(record["started_at"])
        expected_ready_at = started_at + timedelta(seconds=self._result_duration_seconds(result))
        if ready_at > expected_ready_at:
            return expected_ready_at
        return ready_at

    @staticmethod
    def _seconds_text(seconds: int) -> str:
        """把秒数格式化成短时间文本。"""

        seconds = max(0, int(seconds))
        minutes, rest = divmod(seconds, 60)
        if minutes <= 0:
            return f"{rest} 秒"
        if rest <= 0:
            return f"{minutes} 分钟"
        return f"{minutes} 分 {rest} 秒"

    @staticmethod
    def _secret_realm_environment() -> dict:
        """随机一个太虚秘境环境。"""

        name, desc, rates = random.choice(SECRET_REALM_ENVIRONMENTS)
        return {"name": name, "desc": desc, "rates": rates}

    def _secret_realm_actor(self, player: dict, templates: list[dict], environment: dict) -> dict:
        """按玩家状态生成一名太虚秘境对应角色。"""

        player_level = int(player["level"])
        offset = self._secret_realm_level_offset()
        raw_level = max(1, player_level + offset)
        display_level = max(1, min(MAX_LEVEL, raw_level))
        void_power = max(0, raw_level - MAX_LEVEL)
        near_templates = [row for row in templates if abs(int(row["level"]) - display_level) <= 12]
        template = random.choice(near_templates or templates)

        source_weapon = weapon_service.equipped_weapon(str(player["client_id"]))
        source_skill = weapon_service.skill(source_weapon["skill_id"]) if source_weapon else None
        source_effects = self._merge_effects(self.equipment_bonuses(str(player["client_id"])), self._weapon_effects(source_weapon))
        player_hp = max(1, int(player.get("max_hp", player.get("hp", 1))))
        player_defense = max(0, int(player.get("defense", 0)))
        level_rate = max(0.65, display_level / max(1, int(player["level"])))
        rates = environment["rates"]
        void_hp = 1 + void_power * 0.02
        void_attack = 1 + void_power * 0.012
        void_defense = 1 + void_power * 0.008
        hp = max(1, int(player_hp * (0.50 + level_rate * 0.12) * rates["hp"] * void_hp))
        mp = max(1, int(int(player.get("max_mp", player.get("mp", 1))) * (0.86 + level_rate * 0.12)))
        actor = {
            "id": f"taixu:{player['client_id']}",
            "name": f"太虚映身·{template['name']}",
            "level": display_level,
            "raw_level": raw_level,
            "display_level": display_level,
            "void_power": void_power,
            "hp": hp,
            "max_hp": hp,
            "mp": mp,
            "max_mp": mp,
            "base_attack": max(1, int(int(player.get("base_attack", 1)) * (0.95 + level_rate * 0.08) * rates["attack"] * void_attack)),
            "defense": max(0, int(player_defense * (0.32 + level_rate * 0.08) * rates["defense"] * void_defense)),
            "weapon": self._secret_realm_weapon(source_weapon, level_rate, rates, void_attack),
            "skill": dict(source_skill) if source_skill else None,
            "skill_label": str((source_skill or {}).get("name") or ""),
            "effects": self._secret_realm_effects(source_effects, rates),
            "drop_item_id": "",
            "drop_chance": 0,
        }
        return actor

    @staticmethod
    def _secret_realm_weapon(source_weapon: dict | None, level_rate: float, rates: dict, void_attack: float) -> dict | None:
        """生成太虚映身的武器副本。"""

        if not source_weapon:
            return None
        weapon = dict(source_weapon)
        source_level = int(weapon.get("level", 0))
        weapon["weapon_id"] = -abs(int(weapon.get("weapon_id") or 1))
        weapon["level"] = max(0, min(MAX_LEVEL, int(source_level * min(1.08, 0.96 + level_rate * 0.04))))
        weapon["max_level"] = max(int(weapon["level"]), int(weapon.get("max_level", weapon["level"])))
        base_attack = int(weapon.get("base_attack", 1))
        weapon["base_attack"] = max(1, int(base_attack * (0.92 + level_rate * 0.08) * rates["attack"] * void_attack))
        return weapon

    @staticmethod
    def _secret_realm_effects(source_effects: dict[str, float], rates: dict) -> dict[str, float]:
        """生成太虚映身的战斗效果，保留玩家流派但略收敛。"""

        effects: dict[str, float] = {}
        for key, value in source_effects.items():
            if not isinstance(value, int | float):
                continue
            if key in {"max_hp_bonus", "max_mp_bonus", "defense_bonus", "explore_bonus", "trade_bonus", "recover_bonus"}:
                continue
            effects[key] = float(value) * 0.72
        effects["hit_bonus"] = effects.get("hit_bonus", 0.0) + max(0.0, float(rates["attack"]) - 1.0) * 0.08
        return effects

    @staticmethod
    def _secret_realm_level_offset() -> int:
        """秘境怪物等级浮动：常规贴身，小概率冲高。"""

        roll = random.random()
        if roll < 0.70:
            return random.randint(-5, 12)
        if roll < 0.95:
            return random.randint(13, 25)
        return random.randint(26, 40)

    @staticmethod
    def _secret_realm_level_label(monster: dict) -> str:
        """秘境怪物等级展示，100 级后显示秘境强度。"""

        label = f"Lv.{int(monster['display_level'])}"
        void_power = int(monster.get("void_power", 0))
        if void_power > 0:
            label += f"｜秘境强度 +{void_power}"
        return label

    @staticmethod
    def _secret_realm_result_level_text(realm: dict) -> str:
        """秘境结果里的最高强度展示。"""

        level = int(realm.get("highest_display_level") or 0)
        void_power = int(realm.get("highest_void_power") or 0)
        if level <= 0:
            return "无"
        if void_power > 0:
            return f"Lv.{level}｜秘境强度 +{void_power}"
        return f"Lv.{level}"

    def _monster_level_range(self, player: dict) -> tuple[int, int]:
        """按当前探险地点决定怪物等级段。"""

        location = self.db.fetch_one(
            "SELECT min_level, max_level FROM exploration_locations WHERE name = ?",
            (player["location_name"],),
        )
        if location:
            return max(1, int(location["min_level"])), max(1, int(location["max_level"]))
        return max(1, player["level"] - 5), max(5, player["level"] + 8)

    def _exploration_location(self, name: str) -> dict | None:
        """读取探险地点，并补上导航坐标。"""

        location = self.db.fetch_one(
            "SELECT * FROM exploration_locations WHERE name = ?",
            (name.strip(),),
        )
        if not location:
            return None
        location["x"] = int(location["x"])
        location["y"] = int(location["y"])
        return location

    def _world_point_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的命名世界点。"""

        return self.db.fetch_one("SELECT * FROM world_locations WHERE x = ? AND y = ?", (int(x), int(y)))

    def _exploration_location_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的探险地点。"""

        return self.db.fetch_one("SELECT * FROM exploration_locations WHERE x = ? AND y = ?", (int(x), int(y)))

    def _trade_location_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的商路城池。"""

        return self.db.fetch_one("SELECT * FROM trade_locations WHERE x = ? AND y = ?", (int(x), int(y)))

    def _special_buyer_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的特殊收购点。"""

        return self.db.fetch_one("SELECT * FROM special_buyers WHERE x = ? AND y = ?", (int(x), int(y)))

    def _recycle_location_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的回收建筑。"""

        return self.db.fetch_one("SELECT * FROM recycle_locations WHERE x = ? AND y = ?", (int(x), int(y)))

    def _treasure_pickup_at(self, x: int, y: int) -> dict | None:
        """读取脚下可拾取藏宝图。"""

        return self.db.fetch_one(
            """
            SELECT *
            FROM treasure_maps
            WHERE status = '可拾取'
              AND x = ? AND y = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (int(x), int(y)),
        )

    def _sect_at(self, x: int, y: int) -> dict | None:
        """读取当前位置上的玩家宗门山门。"""

        return self.db.fetch_one("SELECT * FROM sects WHERE location_x = ? AND location_y = ?", (int(x), int(y)))

    def _sect_member_count(self, sect_id: int) -> int:
        """读取宗门成员数量。"""

        row = self.db.fetch_one("SELECT COUNT(*) AS count FROM sect_members WHERE sect_id = ?", (int(sect_id),))
        return int(row["count"]) if row else 0

    def _player_name(self, client_id: str) -> str:
        """读取玩家展示名。"""

        row = self.db.fetch_one("SELECT display_name FROM players WHERE client_id = ?", (client_id,))
        return str(row["display_name"]) if row else client_id

    def _trade_specialties_text(self, location_name: str) -> str:
        """读取城池三个纯经济特产。"""

        row = self.db.fetch_one("SELECT specialties FROM trade_locations WHERE name = ?", (location_name,))
        if not row:
            return "无"
        names = [name.strip() for name in str(row["specialties"]).split(",") if name.strip()]
        return "、".join(names) if names else "无"

    @staticmethod
    def _feature_text(features: object) -> str:
        """把世界点功能标记转成短中文。"""

        if not isinstance(features, list):
            return "无"
        labels = [FEATURE_LABELS.get(str(feature), str(feature)) for feature in features if str(feature).strip()]
        return "、".join(labels) if labels else "无"

    def _active_record(self, client_id: str) -> dict | None:
        """读取未领取探险。"""

        return self.db.fetch_one(
            """
            SELECT * FROM exploration_records
            WHERE client_id = ? AND claimed = 0
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (client_id,),
        )

    def _take_drop_preview(
        self,
        player: dict,
        item_id: str,
        weight_now: int,
        item_kinds: set[str],
        pending_drops: dict[str, int],
    ) -> tuple[bool, int]:
        """预估背包是否还能装下本次掉落。"""

        item = self.item_def(item_id)
        if not item:
            return True, weight_now

        next_weight = weight_now + int(item["weight"])
        if next_weight > int(player["weight_limit"]):
            return False, weight_now

        known = item_id in item_kinds or item_id in pending_drops
        if not known and len(item_kinds) + len(pending_drops) + 1 > int(player["backpack_limit"]):
            return False, weight_now

        current = self.db.fetch_one(
            "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
            (player["client_id"], item_id),
        )
        current_quantity = int(current["quantity"]) if current else 0
        pending_quantity = int(pending_drops.get(item_id, 0))
        if current_quantity + pending_quantity + 1 > int(item["stack_limit"]):
            return False, weight_now

        pending_drops[item_id] = pending_drops.get(item_id, 0) + 1
        return True, next_weight

    def _roll_location_drop(self, location_name: str) -> str:
        """按当前地点随机一个古界物资掉落；纯经济不从探险产出。"""

        _ = location_name
        rows = self.db.fetch_all(
            """
            SELECT item_id
            FROM item_defs
            WHERE category IN ('药路', '民生', '建设', '古物')
            """
        )
        if not rows:
            return ""
        weights = []
        for row in rows:
            item = self.item_def(row["item_id"])
            category = str(item["category"]) if item else ""
            weights.append({"药路": 34, "民生": 26, "建设": 28, "古物": 12}.get(category, 10))
        return random.choices(rows, weights=weights, k=1)[0]["item_id"]

    def _roll_secret_realm_location_drop(self) -> str:
        """太虚秘境低频掉落古界物资，偏古物、战利品和高阶建设。"""

        rows = self.db.fetch_all(
            """
            SELECT item_id, category
            FROM item_defs
            WHERE category IN ('古物', '战利品', '建设')
            """
        )
        if not rows:
            return ""
        weights = []
        for row in rows:
            item = self.item_def(row["item_id"])
            category = str(item["category"]) if item else str(row["category"])
            effect = load_json(item["effect"], {}) if item else {}
            subtype = str(effect.get("subtype") or "")
            if category == "古物":
                weights.append({"厚蕴": 36, "中蕴": 24, "微蕴": 12}.get(subtype, 18))
            elif category == "战利品":
                weights.append(22)
            elif category == "建设":
                weights.append({"阵基": 12, "城防": 14, "华饰": 16}.get(subtype, 4))
            else:
                weights.append(1)
        return random.choices(rows, weights=weights, k=1)[0]["item_id"]

    @staticmethod
    def _roll_monster_loot_item(monster: dict) -> str:
        """按怪物族群滚战利品，保留怪物原掉落作为偏向。"""

        base_item = str(monster.get("drop_item_id") or "").strip()
        pool = MONSTER_KIND_LOOT_POOLS.get(str(monster.get("kind") or "").strip())
        if not pool:
            return base_item

        weights: dict[str, int] = {}
        for item_id, weight in pool:
            weights[item_id] = weights.get(item_id, 0) + max(1, int(weight))
        if base_item:
            weights[base_item] = weights.get(base_item, 0) + 35
        return random.choices(list(weights), weights=list(weights.values()), k=1)[0]

    def _roll_secret_realm_gem_drop(self) -> dict | None:
        """太虚秘境掉落 1-8 级宝石，高等级极低概率。"""

        rows = self.db.fetch_all(
            """
            SELECT ring_item_id
            FROM ring_item_defs
            WHERE category = '宝石'
            """
        )
        if not rows:
            return None
        return {
            "gem_id": random.choice(rows)["ring_item_id"],
            "level": random.choices(SECRET_REALM_GEM_LEVELS, weights=SECRET_REALM_GEM_LEVEL_WEIGHTS, k=1)[0],
        }

    @staticmethod
    def _scale_secret_realm_exp(event: dict) -> None:
        """太虚秘境只给少量经验。"""

        exp = max(1, int(int(event.get("exp", 0)) * SECRET_REALM_EXP_RATE))
        event["exp"] = exp
        summary = str(event.get("summary") or "")
        if "经验+" in summary:
            event["summary"] = summary.rsplit("经验+", 1)[0] + f"经验+{exp}"

    @staticmethod
    def _ring_drop_key(item_id: str, level: int = 0) -> str:
        """纳戒掉落统计 key；宝石需要保留等级。"""

        level = max(0, int(level))
        return f"{item_id}#lv{level}" if level > 0 else item_id

    @staticmethod
    def _parse_ring_drop_key(key: str) -> tuple[str, int]:
        """还原纳戒掉落统计 key。"""

        item_id, marker, level_text = str(key).rpartition("#lv")
        if not marker:
            return str(key), 0
        try:
            return item_id, max(1, int(level_text))
        except ValueError:
            return str(key), 0

    def _roll_ring_drop(self, location_name: str = "", play_bonus: float = 0.0) -> str:
        """随机掉落纳戒物品。

        恢复类、宝石和技能书都进纳戒，所以探险获得时直接写入纳戒。
        """

        rows = self.db.fetch_all("""
            SELECT ring_item_id, category
            FROM ring_item_defs
            WHERE category IN ('恢复类', '宝石', '技能书')
              AND ring_item_id != 'cuifengdan'
              AND ring_item_id NOT LIKE 'extreme_%'
            """)
        if not rows:
            return ""

        groups = {
            "恢复类": [row for row in rows if row["category"] == "恢复类"],
            "宝石": [row for row in rows if row["category"] == "宝石"],
            "技能书": [row for row in rows if row["category"] == "技能书"],
        }
        roll = random.random()
        if roll < 0.62 and groups["恢复类"]:
            return random.choice(groups["恢复类"])["ring_item_id"]
        if roll < 0.86 and groups["宝石"]:
            return random.choice(groups["宝石"])["ring_item_id"]
        if groups["技能书"]:
            books = [row for row in groups["技能书"] if not str(row["ring_item_id"]).startswith("extreme_")]
            book_id = random.choice(books or groups["技能书"])["ring_item_id"]
            return self.maybe_upgrade_extreme_book(book_id, location_name, play_bonus)
        return random.choice(rows)["ring_item_id"]

    def _medicine_stock(self, client_id: str) -> dict[str, dict]:
        """读取可自动消耗的恢复药。

        只读取能恢复血气或精神的恢复类物品。
        福袋虽然也在恢复类里，但它只给源石，不会被自动使用。
        """

        rows = self.db.fetch_all(
            """
            SELECT r.ring_item_id, r.quantity, e.name, e.effect
            FROM ring_items r
            JOIN ring_item_defs e ON e.ring_item_id = r.ring_item_id
            WHERE r.client_id = ?
              AND r.quantity > 0
              AND e.category = '恢复类'
              AND e.usable = 1
            """,
            (client_id,),
        )
        stock: dict[str, dict] = {}
        for row in rows:
            effect = load_json(row["effect"], {})
            has_recovery = any(effect.get(key) for key in ("hp_delta", "hp_ratio", "mp_delta", "mp_ratio"))
            if not has_recovery:
                continue
            stock[row["ring_item_id"]] = {
                "name": row["name"],
                "quantity": int(row["quantity"]),
                "effect": effect,
            }
        return stock

    def _auto_use_medicine(
        self,
        hp: int,
        mp: int,
        player: dict,
        stock: dict[str, dict],
        used: dict[str, int],
    ) -> tuple[int, int]:
        """按阈值自动使用恢复药。

        血气低于 45% 时补到约 75%。
        精神低于 30% 时补到约 65%。
        只修改本次探险预计算里的临时血气和精神，真正扣药在开始探险时统一扣。
        """

        if not stock:
            return hp, mp

        max_hp = int(player["max_hp"])
        max_mp = int(player["max_mp"])
        with self.db.transaction() as conn:
            support_bonus = sect_direction_bonus_conn(conn, str(player["client_id"]), "support")
            build_bonus = sect_direction_bonus_conn(conn, str(player["client_id"]), "build")
        stable_bonus = min(0.05, support_bonus * 0.04 + build_bonus * 0.025)
        if hp > 0 and hp <= int(max_hp * (0.45 + stable_bonus)):
            hp = self._recover_value(hp, max_hp, "hp", stock, used, int(max_hp * (0.75 + stable_bonus)))
        if mp <= int(max_mp * (0.30 + stable_bonus)):
            mp = self._recover_value(mp, max_mp, "mp", stock, used, int(max_mp * (0.65 + stable_bonus)))
        return hp, mp

    def _recover_value(
        self,
        value: int,
        max_value: int,
        kind: str,
        stock: dict[str, dict],
        used: dict[str, int],
        target: int,
    ) -> int:
        """用最合适的恢复药把某个数值补到目标线。"""

        if value >= target:
            return min(max_value, value)

        while value < target:
            options = []
            for item_id, item in stock.items():
                if item["quantity"] <= 0:
                    continue
                effect = item["effect"]
                amount = int(effect.get(f"{kind}_delta") or 0)
                amount += int(max_value * float(effect.get(f"{kind}_ratio") or 0))
                if amount > 0:
                    options.append((amount, item_id))
            if not options:
                break

            need = target - value
            enough = [option for option in options if option[0] >= need]
            amount, item_id = min(enough or options)
            if not enough:
                amount, item_id = max(options)
            stock[item_id]["quantity"] -= 1
            used[item_id] = used.get(item_id, 0) + 1
            value = min(max_value, value + amount)
        return value

    def _format_medicine_used(self, medicine_used: dict[str, int]) -> str:
        """把自动消耗的恢复药整理成玩家可读文本。"""

        texts = []
        for item_id, quantity in medicine_used.items():
            item = self.ring_item_def(item_id)
            texts.append(f"{item['name'] if item else item_id} x{quantity}")
        return "、".join(texts)

    def _claim_settlement(self, player: dict, result: dict) -> dict:
        """按最终预计算结果整理领取结算，供事务内外保持同一口径。"""

        events = list(result.get("events", []))
        exp_total = sum(int(event.get("exp", 0)) for event in events)
        weapon_exp_total = self._weapon_exp_total(events)
        drops: dict[str, int] = {}
        ring_drops: dict[str, int] = {}
        hp_left = int(player["hp"])
        mp_left = int(player["mp"])
        dead = False
        for event in events:
            hp_left = int(event.get("hp_left", hp_left))
            mp_left = int(event.get("mp_left", mp_left))
            if event.get("drop_item_id"):
                drops[event["drop_item_id"]] = drops.get(event["drop_item_id"], 0) + 1
            if event.get("location_drop_item_id"):
                item_id = event["location_drop_item_id"]
                drops[item_id] = drops.get(item_id, 0) + 1
            if event.get("ring_drop_id"):
                ring_key = self._ring_drop_key(event["ring_drop_id"], int(event.get("ring_drop_level") or 0))
                ring_drops[ring_key] = ring_drops.get(ring_key, 0) + 1
            if hp_left <= 0:
                dead = True
                mp_left = 0
                event["mp_left"] = 0
                break
        return {
            "events": events,
            "exp_total": exp_total,
            "weapon_exp_total": weapon_exp_total,
            "drops": drops,
            "ring_drops": ring_drops,
            "hp_left": hp_left,
            "mp_left": mp_left,
            "dead": dead,
        }

    def _claim_log_block(
        self,
        *,
        record: dict,
        player: dict,
        result: dict,
        events: list[dict],
        exp_total: int,
        weapon_exp_total: int,
        old_level: int,
        new_level: int,
        drops: dict[str, int],
        ring_drops: dict[str, int],
        weapon_drops: list[str],
        medicine_used: dict[str, int],
        dead: bool,
        bag_full: bool,
    ) -> str | dict:
        """把结束探险的战斗过程和最终结算整理成短消息和战斗日志链接。"""

        if result.get("secret_realm"):
            return self._secret_realm_claim_log(
                record=record,
                player=player,
                result=result,
                events=events,
                exp_total=exp_total,
                weapon_exp_total=weapon_exp_total,
                old_level=old_level,
                new_level=new_level,
                drops=drops,
                ring_drops=ring_drops,
                weapon_drops=weapon_drops,
                medicine_used=medicine_used,
                dead=dead,
                bag_full=bag_full,
            )

        return combat_log_text.exploration_brief(
            record=record,
            player=player,
            events=events,
            exp_total=exp_total,
            weapon_exp_total=weapon_exp_total,
            old_level=old_level,
            new_level=new_level,
            drops_text=self._format_backpack_awards(drops),
            ring_drops_text=self._format_ring_awards(ring_drops),
            weapon_drops_text=self._format_weapon_awards(weapon_drops),
            medicine_text=self._format_medicine_used(medicine_used) if medicine_used else "无",
            stop_reason=self._stop_reason(dead, bag_full),
            detail=combat_log_text.wants_detail(player),
        )

    def _secret_realm_claim_log(
        self,
        *,
        record: dict,
        player: dict,
        result: dict,
        events: list[dict],
        exp_total: int,
        weapon_exp_total: int,
        old_level: int,
        new_level: int,
        drops: dict[str, int],
        ring_drops: dict[str, int],
        weapon_drops: list[str],
        medicine_used: dict[str, int],
        dead: bool,
        bag_full: bool,
    ) -> dict:
        """太虚秘境专属简化结算日志。"""

        realm = result.get("secret_realm") if isinstance(result.get("secret_realm"), dict) else {}
        wins = sum(1 for event in events if event.get("win"))
        losses = max(0, len(events) - wins)
        hp_left = int(player.get("hp", 1))
        mp_left = int(player.get("mp", 0))
        level_text = f"{old_level} → {new_level}" if new_level > old_level else f"{new_level}，未升级"
        highest_label = self._secret_realm_result_level_text(realm)
        stop_reason = self._stop_reason(dead, bag_full)
        if not dead and not bag_full:
            stop_reason = "秘境轮数已尽，45 分钟到点"
        record_id = int(record["record_id"])
        log_link = battle_log_markdown(
            f"太虚秘境战斗日志〔{record_id}〕",
            "explore",
            record_id,
            detail=combat_log_text.wants_detail(player),
        )
        lines = [
            "> **太虚秘境结束**",
            f"> 记录 **〔{record['record_id']}〕**｜环境：{realm.get('name', '未知')}",
            f"> {realm.get('desc', '虚空潮汐未留下明确信息。')}",
            f"> 战斗 **{len(events)}** 场｜胜 **{wins}**｜败 **{losses}**｜耗时 **{self._seconds_text(self._result_duration_seconds(result))}**",
            f"> 最高强度：{highest_label}",
            f"> 经验 **+{exp_total}**｜武器经验 **+{weapon_exp_total}**｜等级：{level_text}",
            f"> 最终状态：血气 **{hp_left}/{player['max_hp']}**｜精神 **{mp_left}/{player['max_mp']}**",
            f"> 停止原因：{stop_reason}",
            ">",
            "> **收获**",
            f"> 背包：{self._format_backpack_awards(drops)}",
            f"> 纳戒：{self._format_ring_awards(ring_drops)}",
            f"> 武器：{self._format_weapon_awards(weapon_drops)}",
            f"> 自动用药：{self._format_medicine_used(medicine_used) if medicine_used else '无'}",
            f"> 战斗日志：{log_link}",
            "> 当前状态：空闲",
        ]
        return combat_log_text.markdown_reply("\n".join(lines))

    def _event_log_lines(self, index: int, event: dict, player: dict) -> list[str]:
        """整理单场战斗日志，细到每一次出手。"""

        hp_left = max(0, int(event.get("hp_left", 0)))
        mp_left = max(0, int(event.get("mp_left", 0)))
        lines = [
            f"第 {index} 战",
            f"  概况：{event.get('summary', '无战斗摘要')}",
        ]
        weapon_exp = int(event.get("weapon_exp", 0)) if int(event.get("weapon_id", 0)) > 0 else 0
        if weapon_exp > 0:
            lines.append(f"  武器经验：+{weapon_exp}")
        actions = event.get("actions")
        if isinstance(actions, list) and actions:
            for action in actions:
                lines.extend(self._action_log_lines(action, event, player))
        else:
            lines.append("  逐次出手：无记录")
        lines.append(f"  战后：血气 {hp_left}/{player['max_hp']}，精神 {mp_left}/{player['max_mp']}")
        lines.append(f"  掉落：{self._event_drop_text(event)}")
        if event.get("bag_full"):
            lines.append("  动作：背包已满，本场后停止后续预计算。")
        if hp_left <= 0:
            lines.append("  动作：本体重伤，本场后停止后续预计算。")
        return lines

    def _action_log_lines(self, action: dict, event: dict, player: dict) -> list[str]:
        """整理一次行动条出手日志。"""

        round_no = int(action.get("round", 0))
        monster_name = str(event.get("monster") or "怪物")
        monster_hp_left = max(0, int(action.get("monster_hp_left", 0)))
        monster_hp_max = max(1, int(action.get("monster_hp_max", 1)))
        lines = [f"  第 {round_no} 次行动"]
        if action.get("actor") == "player":
            total_damage = int(action.get("player_total_damage", action.get("damage", 0)))
            combo_damage = int(action.get("combo_damage", 0))
            skill_name = str(action.get("skill_name") or "")
            if action.get("skill_used"):
                attack_text = f"技能「{skill_name}」"
                cost_text = f"，消耗精神 {int(action.get('mp_cost', 0))}"
            else:
                attack_text = "普通攻击"
                cost_text = ""
            combo_text = f"，连击追加 {combo_damage}" if combo_damage > 0 else ""
            life_steal = int(action.get("life_steal", 0))
            steal_text = f"，吸血 +{life_steal}" if life_steal > 0 else ""
            effect = combat_service.action_effect_text(action)
            effect_text = f"，{effect}" if effect else ""
            lines.append(
                f"    我方出手：{attack_text}，造成 {total_damage} 伤害"
                f"{combo_text}{steal_text}{effect_text}{cost_text}；"
                f"{monster_name} 血气 {monster_hp_left}/{monster_hp_max}"
            )
            if monster_hp_left <= 0:
                lines.append(f"    敌方出手：{monster_name} 已倒下，未能出手。")
            return lines

        player_hp_left = max(0, int(action.get("player_hp_left", 0)))
        player_mp_left = max(0, int(action.get("player_mp_left", 0)))
        if action.get("dodged"):
            lines.append(f"    敌方出手：{monster_name} 攻击落空；" f"我方血气 {player_hp_left}/{player['max_hp']}，精神 {player_mp_left}/{player['max_mp']}")
            return lines

        hurt = int(action.get("monster_damage", 0))
        skill_name = str(action.get("monster_skill_name") or "")
        attack_text = f"技能「{skill_name}」" if action.get("monster_skill_used") else "普通攻击"
        effect = combat_service.action_effect_text(action)
        effect_text = f"，{effect}" if effect else ""
        lines.append(
            f"    敌方出手：{attack_text}，造成 {hurt} 伤害{effect_text}；" f"我方血气 {player_hp_left}/{player['max_hp']}，精神 {player_mp_left}/{player['max_mp']}"
        )
        return lines

    def _event_drop_text(self, event: dict) -> str:
        """整理单场战斗掉落。"""

        texts = []
        if event.get("drop_item_id"):
            texts.append("怪物掉落 " + self._item_name(event["drop_item_id"]))
        if event.get("location_drop_item_id"):
            texts.append("古界物资 " + self._item_name(event["location_drop_item_id"]))
        if event.get("ring_drop_id"):
            level = int(event.get("ring_drop_level") or 0)
            if level > 0:
                texts.append(f"宝石 {self._ring_item_name(event['ring_drop_id'])} {level}级")
            else:
                texts.append("纳戒物品 " + self._ring_item_name(event["ring_drop_id"]))
        if event.get("weapon_drop"):
            drop = event["weapon_drop"]
            texts.append(f"武器预掉落 {drop['name']}[{drop['quality']}] 上限{drop['max_level']}")
        return "、".join(texts) if texts else "无"

    @staticmethod
    def _weapon_drops_from_result(result: dict) -> list[dict]:
        """读取本轮武器掉落。

        普通探险通常是一把，秘境可能每几关判定出多把。
        """

        drops = []
        result_drops = result.get("weapon_drops")
        if isinstance(result_drops, list):
            drops.extend(drop for drop in result_drops if isinstance(drop, dict))
        result_drop = result.get("weapon_drop")
        if result_drop:
            known = {
                (drop.get("weapon_def_id"), drop.get("quality"), drop.get("max_level"))
                for drop in drops
            }
            key = (result_drop.get("weapon_def_id"), result_drop.get("quality"), result_drop.get("max_level"))
            if key not in known:
                drops.append(result_drop)
        return drops

    def _format_backpack_awards(self, drops: dict[str, int]) -> str:
        """整理背包最终获得。"""

        if not drops:
            return "无"
        return "、".join(f"{self._item_name(item_id)} x{quantity}" for item_id, quantity in drops.items())

    def _format_ring_awards(self, drops: dict[str, int]) -> str:
        """整理纳戒最终获得。"""

        if not drops:
            return "无"
        texts = []
        for item_id, quantity in drops.items():
            ring_id, level = self._parse_ring_drop_key(item_id)
            level_text = f" {level}级" if level > 0 else ""
            texts.append(f"{self._ring_item_name(ring_id)}{level_text} x{quantity}")
        return "、".join(texts)

    @staticmethod
    def _format_weapon_awards(weapon_drops: list[str]) -> str:
        """整理武器最终获得。"""

        return "、".join(weapon_drops) if weapon_drops else "无"

    @staticmethod
    def _stop_reason(dead: bool, bag_full: bool) -> str:
        """整理本次预计算停止原因。"""

        if dead:
            return "本体重伤，已自动撤离"
        if bag_full:
            return "背包已满，提前停止"
        return "30 分钟到点"

    def _item_name(self, item_id: str) -> str:
        """读取背包物品名称。"""

        item = self.item_def(item_id)
        return item["name"] if item else item_id

    def _ring_item_name(self, item_id: str) -> str:
        """读取纳戒物品名称。"""

        item = self.ring_item_def(item_id)
        return item["name"] if item else item_id

    def _status_summary(self, player: dict, result: dict, events: list[dict]) -> dict:
        """整理探险状态摘要。

        探险在开始时已经预计算完整结果；状态页只展示摘要，
        真正发放经验、物品和武器仍然必须等 `结束探险`。
        """

        wins = sum(1 for event in events if event.get("win"))
        losses = max(0, len(events) - wins)
        exp_total = sum(int(event.get("exp", 0)) for event in events)
        weapon_exp_total = self._weapon_exp_total(events)
        hp_left = int(events[-1].get("hp_left", player["hp"])) if events else int(player["hp"])
        mp_left = int(events[-1].get("mp_left", player["mp"])) if events else int(player["mp"])
        fight_text = f"{len(events)} 场，胜 {wins}，败 {losses}"

        backpack_drops: dict[str, int] = {}
        ring_drops: dict[str, int] = {}
        weapon_drops: list[str] = []
        for event in events:
            for key in ("drop_item_id", "location_drop_item_id"):
                item_id = event.get(key)
                if item_id:
                    backpack_drops[item_id] = backpack_drops.get(item_id, 0) + 1
            ring_id = event.get("ring_drop_id")
            if ring_id:
                ring_key = self._ring_drop_key(ring_id, int(event.get("ring_drop_level") or 0))
                ring_drops[ring_key] = ring_drops.get(ring_key, 0) + 1
        for weapon_drop in self._weapon_drops_from_result(result):
            weapon_drops.append(f"{weapon_drop['name']}[{weapon_drop['quality']}]上限{weapon_drop['max_level']}")

        drop_parts = []
        if backpack_drops:
            drop_parts.append("背包 " + self._format_drop_preview(backpack_drops, ring=False))
        if ring_drops:
            drop_parts.append("纳戒 " + self._format_drop_preview(ring_drops, ring=True))
        if weapon_drops:
            drop_parts.append("武器 " + self._limit_texts(weapon_drops))

        medicine_used = result.get("medicine_used", {})
        return {
            "fight_text": fight_text,
            "exp_total": exp_total,
            "weapon_exp_total": weapon_exp_total,
            "hp_left": max(0, hp_left),
            "mp_left": max(0, mp_left),
            "drop_text": "；".join(drop_parts),
            "medicine_text": self._format_medicine_used(medicine_used) if medicine_used else "",
        }

    @staticmethod
    def _weapon_exp_total(events: list[dict]) -> int:
        """按实际持有武器的战斗事件统计本轮武器经验。"""

        total = 0
        for event in events:
            if int(event.get("weapon_id") or 0) <= 0:
                continue
            total += max(0, int(event.get("weapon_exp", 0)))
        return total

    def _format_drop_preview(self, drops: dict[str, int], ring: bool) -> str:
        """把掉落预览转成短文本。"""

        texts = []
        for item_id, quantity in drops.items():
            lookup_id = item_id
            level_text = ""
            if ring:
                lookup_id, level = self._parse_ring_drop_key(item_id)
                level_text = f" {level}级" if level > 0 else ""
            item = self.ring_item_def(lookup_id) if ring else self.item_def(lookup_id)
            texts.append(f"{item['name'] if item else lookup_id}{level_text} x{quantity}")
        return self._limit_texts(texts)

    @staticmethod
    def _limit_texts(texts: list[str], limit: int = 5) -> str:
        """限制状态页单行长度，避免掉落太多时刷屏。"""

        if len(texts) <= limit:
            return "、".join(texts)
        return "、".join(texts[:limit]) + f" 等{len(texts)}种"

    @staticmethod
    def _time(value: str):
        """转换时间。"""

        from ..common import dt

        return dt(value) or now()


service = ExplorationService(db)

__all__ = ["ExplorationService", "service"]
