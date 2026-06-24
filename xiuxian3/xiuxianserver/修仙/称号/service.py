"""称号组件服务。

称号纯装饰，无属性加成。通过被动数据判定获取，玩家可手动选择佩戴。
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from typing import Any

from ..common import CoreService, money, now, ts
from ..constants import DIRECT_FLOW_RETENTION_DAYS
from ..format_text import T
from ..sql import db


# ─────────────────────────────────────────────
# 称号分类定义
# ─────────────────────────────────────────────

TITLE_CATEGORIES: OrderedDict[str, dict[str, str]] = OrderedDict([
    ("入门", {"desc": "修为境界里程碑"}),
    ("签到", {"desc": "日日修行不辍"}),
    ("经济", {"desc": "源石与商道成就"}),
    ("探险", {"desc": "踏遍山河万险"}),
    ("世界物资", {"desc": "药路·民生·建设·古物·战利品"}),
    ("武器", {"desc": "铸造·收藏·回收之路"}),
    ("战斗", {"desc": "虫洞·首领·对战征伐"}),
    ("铭刻", {"desc": "羽墨铭名之道"}),
    ("宗门", {"desc": "宗门归属与贡献"}),
    ("特殊", {"desc": "复仇·异火·藏宝图·市场"}),
])

# 称号元数据: (名称, 分类, 描述, 获取途径)
TITLE_META: dict[str, tuple[str, str, str, str]] = {
    # 入门
    "初入仙途": ("入门", "初入修仙之门", "创建修仙角色"),
    "问道初心": ("入门", "初窥修仙门径", "等级达到 10"),
    "半步金丹": ("入门", "修为渐成，半步之遥", "等级达到 50"),
    "元婴修士": ("入门", "元婴成形，修为高深", "等级达到 80"),
    "天人合一": ("入门", "天人合一，修为圆满", "等级达到 100"),
    # 签到
    "晨钟常客": ("签到", "晨钟暮鼓，日日不辍", "累计签到 7 次"),
    "风雨无阻": ("签到", "风雨不辍，日日修行", "累计签到 30 次"),
    "日夜兼程": ("签到", "日月不停，道心坚定", "累计签到 100 次"),
    # 经济
    "小富即安": ("经济", "小有积蓄，心安理得", "随身源石达到 5 万"),
    "藏源有道": ("经济", "源库充盈，藏而有道", "源库余额达到 10 万"),
    "财气盈门": ("经济", "财气盈门，百事亨通", "明面资产达到 30 万"),
    "商路识途": ("经济", "商路纵横，了然于胸", "跑商净利润达到 10 万"),
    "腰缠万贯": ("经济", "腰缠万贯，财大气粗", "随身源石达到 50 万"),
    "富甲一方": ("经济", "富甲一方，坐拥金山", "总资产达到 200 万"),
    "跑商巨贾": ("经济", "商道通天，利润惊人", "跑商净利润达到 50 万"),
    # 探险
    "探险常客": ("探险", "探险之地的常客", "近况探险 ≥ 3 或累计 ≥ 5"),
    "山河熟客": ("探险", "山河万里，尽在脚下", "累计探险达到 30"),
    "万险不惧": ("探险", "万险加身，面不改色", "累计探险达到 100"),
    "踏遍山河": ("探险", "踏遍天下山河", "累计探险达到 200"),
    # 世界物资
    "丹火借道客": ("世界物资", "药路常客，丹火借道", "近况回收药路 ≥ 3"),
    "灯火续命人": ("世界物资", "凡城灯火，续命济世", "近况回收民生 ≥ 3"),
    "护城搬山客": ("世界物资", "一砖一瓦，垒城筑基", "近况回收建设 ≥ 3"),
    "秘库经手人": ("世界物资", "秘库旧宝，经手留痕", "近况回收古物 ≥ 2"),
    "战备供货人": ("世界物资", "战场利刃，战备货源", "近况战利品出售 ≥ 3"),
    "看炉顺药人": ("世界物资", "看炉顺药，药路熟脸", "累计回收药路 ≥ 15"),
    "垒城匠手": ("世界物资", "垒城匠手，百炼成基", "累计回收建设 ≥ 15"),
    "旧宝摸金者": ("世界物资", "摸金校尉，旧宝新主", "累计回收古物 ≥ 10"),
    "镇妖战备客": ("世界物资", "镇妖备战，利刃出鞘", "累计回收战利品 ≥ 10"),
    # 武器
    "兵器收藏家": ("武器", "百兵齐聚，收藏成癖", "拥有武器 ≥ 5"),
    "百炼持刃": ("武器", "百炼成钢，持刃而立", "最高武器等级 ≥ 40"),
    "铸剑客": ("武器", "铸剑淬锋，匠心独运", "出售武器 ≥ 3"),
    "藏经归客": ("武器", "藏经万卷，归于一炉", "出售技能书 ≥ 3"),
    "琢玉散人": ("武器", "琢玉成器，散人自得", "出售宝石 ≥ 3"),
    "欧气外露": ("武器", "欧气外露，天命所归", "拥有稀品或珍品武器"),
    "满锋候选": ("武器", "满锋在望，候选之资", "最高武器上限 ≥ 80"),
    "多刃之主": ("武器", "刃多不压身", "拥有武器 ≥ 10"),
    "七窍玲珑": ("武器", "七窍全开，攻防兼备", "装备全部开孔"),
    "淬锋达人": ("武器", "淬锋成瘾，刃利三分", "武器淬锋 ≥ 3"),
    # 战斗
    "虫洞先锋": ("战斗", "虚空之中，率先破阵", "参与虫洞 ≥ 1"),
    "虫洞鏖战者": ("战斗", "虫洞鏖战，伤敌万千", "虫洞累计伤害 ≥ 2 万"),
    "虚空破壁人": ("战斗", "虚空之中，破壁前行", "参与虫洞 ≥ 10"),
    "岁时赴约人": ("战斗", "岁时情劫，如期赴约", "挑战首领 ≥ 1"),
    "情劫破阵者": ("战斗", "情劫破阵，伤敌万千", "首领累计伤害 ≥ 2 万"),
    "岁寒知己": ("战斗", "与天命劫数相知", "挑战首领 ≥ 10"),
    "斗法胜手": ("战斗", "斗法之中，胜券在握", "近况对战胜利 ≥ 2 或累计 ≥ 3"),
    "独步一方": ("战斗", "独步一方，未逢敌手", "对战胜利 ≥ 15"),
    "剑走偏锋": ("战斗", "剑走偏锋，以抢代商", "抢劫成功 ≥ 3"),
    # 铭刻
    "羽墨留名": ("铭刻", "羽墨留名，铭刻入骨", "铭刻 ≥ 1"),
    "万铭归一": ("铭刻", "万法归一，铭刻大成", "铭刻 ≥ 5"),
    # 宗门
    "宗门弟子": ("宗门", "拜入宗门，正式修行", "已加入宗门"),
    "门中翘楚": ("宗门", "宗门翘楚，出类拔萃", "宗门个人贡献 ≥ 5000"),
    # 特殊
    "睚眦必报": ("特殊", "有仇必报，睚眦之怨亦清", "复仇成功"),
    "火种拾遗": ("特殊", "拾得天地异火种", "拥有 ≥ 1 种异火"),
    "寻宝人": ("特殊", "踏上寻宝之路", "拾取过藏宝图"),
    "市井掮客": ("特殊", "市井穿梭，掮客之能", "二手市场成交 ≥ 10"),
    "散财有道": ("特殊", "散财亦有大道", "二手市场支出 ≥ 30 万"),
}


class TitleService(CoreService):
    """称号组件服务。"""

    def __init__(self) -> None:
        super().__init__(db)

    # ─────────────────────────────────────────────
    # 命令入口
    # ─────────────────────────────────────────────

    def my_titles(self, client_id: str) -> str:
        """查看当前佩戴称号、已拥有数量、入口按钮。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        manual = self._manual_title(client_id)
        auto = self.active_title(client_id)

        with self.db.transaction() as conn:
            owned = self._count_conn(conn, "player_titles", "client_id = ?", (client_id,))
        total = len(TITLE_META)

        panel = T.panel()
        panel.section("称号")
        if manual:
            panel.line(f"当前佩戴：**{manual}**（手动）")
        elif auto:
            panel.line(f"当前佩戴：**{auto}**（自动）")
        else:
            panel.line("当前佩戴：无")
        if manual and auto and manual != auto:
            panel.line(f"自动佩戴：{auto}（自动最高）")
        panel.line(f"已拥有：{owned} / {total}")
        return panel.render() + T.buttons("称号列表", "称号图鉴", "称号卸下")

    def list_all(self, client_id: str) -> str:
        """按分类展示全部称号和获取状态。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        with self.db.transaction() as conn:
            owned_set = self._owned_titles_set(conn, client_id)

        panel = T.panel()
        panel.section("称号列表")
        for cat_name in TITLE_CATEGORIES:
            cat_titles = [t for t, (c, *_rest) in TITLE_META.items() if c == cat_name]
            owned_in_cat = [t for t in cat_titles if t in owned_set]
            panel.line(f"▸ {cat_name}（{len(owned_in_cat)}/{len(cat_titles)}）")
            parts: list[str] = []
            for t in cat_titles:
                mark = "✅" if t in owned_set else "❌"
                parts.append(f"{mark} {t}")
            panel.line("  " + " · ".join(parts))
        return panel.render() + T.buttons("称号图鉴", "称号", "称号卸下")

    def detail(self, client_id: str, message: str) -> str:
        """查看单个称号的获取途径和佩戴状态。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        title_name = message.strip()
        if not title_name:
            return T.hint("请指定称号名称。", "发送：称号详情 称号名，例如：称号详情 初入仙途")

        if title_name not in TITLE_META:
            return T.hint(f"称号「{title_name}」不存在。", "发送：称号列表 查看所有称号")

        cat, desc, acquire = TITLE_META[title_name]

        with self.db.transaction() as conn:
            owned = self._is_owned(conn, client_id, title_name)
            reason_row = conn.execute(
                "SELECT reason FROM player_titles WHERE client_id = ? AND title = ?",
                (client_id, title_name),
            ).fetchone()
        reason = str(reason_row["reason"]) if reason_row else ""
        manual = self._manual_title(client_id)

        panel = T.panel()
        panel.section(f"称号详情 · {title_name}")
        panel.line(f"分类：{cat}")
        panel.line(f"描述：{desc}")
        panel.line(f"获取途径：{acquire}")
        if owned:
            panel.line(f"状态：✅ 已拥有（{reason}）")
        else:
            panel.line("状态：🔒 未解锁")
        if manual == title_name:
            panel.line("佩戴：当前手动佩戴中")
        elif owned:
            panel.line("佩戴：当前未佩戴")
        return panel.render() + T.buttons("称号佩戴 " + title_name, "称号列表", "称号")

    def equip(self, client_id: str, message: str) -> str:
        """手动佩戴已拥有的某个称号。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        title_name = message.strip()
        if not title_name:
            return T.hint("请指定要佩戴的称号名称。", "发送：称号佩戴 称号名")

        if title_name not in TITLE_META:
            return T.hint(f"称号「{title_name}」不存在。", "发送：称号列表 查看所有称号")

        with self.db.transaction() as conn:
            if not self._is_owned(conn, client_id, title_name):
                return T.hint(f"你尚未获得称号「{title_name}」。", "发送：称号图鉴 查看获取进度")
            current = ts()
            self._upsert_manual_title_conn(conn, client_id, title_name, current)

        panel = T.panel()
        panel.section("称号佩戴成功")
        panel.line(f"已手动佩戴：**{title_name}**")
        return panel.render() + T.buttons("称号", "称号卸下", "修仙信息")

    def unequip(self, client_id: str) -> str:
        """卸下手动佩戴，恢复自动佩戴模式。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        manual = self._manual_title(client_id)
        if not manual:
            return T.hint("你当前没有手动佩戴称号。", "当前为自动佩戴模式，发送：称号佩戴 称号名 可手动选择")

        with self.db.transaction() as conn:
            self._upsert_manual_title_conn(conn, client_id, "", ts())

        auto = self.active_title(client_id)
        panel = T.panel()
        panel.section("称号已卸下")
        panel.line("已恢复自动佩戴模式。")
        if auto:
            panel.line(f"当前自动佩戴：**{auto}**")
        return panel.render() + T.buttons("称号", "称号列表", "修仙信息")

    def codex(self, client_id: str) -> str:
        """全部称号的锁定/解锁状态和获取进度。"""

        player, err = self.require_player(client_id)
        if err:
            return err

        with self.db.transaction() as conn:
            owned_set = self._owned_titles_set(conn, client_id)
            reason_map: dict[str, str] = {}
            for row in conn.execute(
                "SELECT title, reason FROM player_titles WHERE client_id = ?",
                (client_id,),
            ).fetchall():
                reason_map[str(row["title"])] = str(row["reason"])

        total = len(TITLE_META)
        owned = len(owned_set)

        panel = T.panel()
        panel.section("称号图鉴")
        panel.line(f"已解锁 {owned} / {total}")
        for cat_name in TITLE_CATEGORIES:
            cat_titles = [t for t, (c, *_rest) in TITLE_META.items() if c == cat_name]
            owned_in_cat = [t for t in cat_titles if t in owned_set]
            panel.line(f"▸ {cat_name}（{len(owned_in_cat)}/{len(cat_titles)}）")
            for t in cat_titles:
                if t in owned_set:
                    reason = reason_map.get(t, "")
                    panel.line(f"  ✅ {t}" + (f" — {reason}" if reason else ""))
                else:
                    _cat, _desc, acquire = TITLE_META[t]
                    panel.line(f"  🔒 {t} — {acquire}")
        return panel.render() + T.buttons("称号", "称号列表", "称号卸下")

    # ─────────────────────────────────────────────
    # 被动刷新（供其他组件结算时调用）
    # ─────────────────────────────────────────────

    def refresh_titles(self, client_id: str, player: dict[str, Any] | None = None) -> str:
        """按当前数据刷新称号池，并自动佩戴当前最合适的一个。"""

        with self.db.transaction() as conn:
            if player is None:
                row = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
                if not row:
                    return ""
                player = dict(row)
            return self.refresh_titles_conn(conn, client_id, player)

    def refresh_titles_conn(self, conn: sqlite3.Connection, client_id: str, player: dict[str, Any]) -> str:
        """在事务里刷新称号，并返回自动佩戴的称号。"""

        stats = self._title_stats_conn(conn, client_id, player)
        rules = self._title_rules(stats)
        current = ts()
        valid = self._save_valid_titles_conn(conn, client_id, rules, current)

        conn.execute("UPDATE player_titles SET active = 0 WHERE client_id = ?", (client_id,))
        if not valid:
            return ""
        active_title = max(valid, key=lambda item: item[0])[1]
        conn.execute(
            """
            UPDATE player_titles
            SET active = 1, updated_at = ?
            WHERE client_id = ? AND title = ?
            """,
            (current, client_id, active_title),
        )
        return active_title

    def active_title(self, client_id: str) -> str:
        """读取当前自动佩戴称号。"""

        row = self.db.fetch_one(
            """
            SELECT title FROM player_titles
            WHERE client_id = ? AND active = 1
            LIMIT 1
            """,
            (client_id,),
        )
        return str(row["title"]) if row else ""

    def current_display_title(self, client_id: str) -> str:
        """读取当前显示称号：手动优先，否则自动。"""

        manual = self.manual_title(client_id)
        if manual:
            return manual
        return self.active_title(client_id)

    def manual_title(self, client_id: str) -> str:
        """读取手动佩戴称号。"""

        return self._manual_title(client_id)

    def set_manual_title(self, client_id: str, title_name: str) -> None:
        """保存手动佩戴称号。"""

        with self.db.transaction() as conn:
            self._upsert_manual_title_conn(conn, client_id, title_name, ts())

    def clear_manual_title(self, client_id: str) -> None:
        """清空手动佩戴称号，恢复自动模式。"""

        with self.db.transaction() as conn:
            self._upsert_manual_title_conn(conn, client_id, "", ts())

    def delete_title_prefs(self, client_id: str) -> None:
        """删除称号偏好记录。"""

        with self.db.transaction() as conn:
            self._delete_title_prefs_conn(conn, client_id)

    # ─────────────────────────────────────────────
    # 内部方法
    # ─────────────────────────────────────────────

    def _manual_title(self, client_id: str) -> str:
        """读取手动佩戴称号。"""

        row = self.db.fetch_one(
            "SELECT manual_title FROM title_prefs WHERE client_id = ?",
            (client_id,),
        )
        if not row:
            return ""
        val = str(row["manual_title"] or "").strip()
        return val

    @staticmethod
    def _upsert_manual_title_conn(conn: sqlite3.Connection, client_id: str, manual_title: str, current: str) -> None:
        """在事务里写入或清空手动称号偏好。"""

        conn.execute(
            """
            INSERT INTO title_prefs (client_id, manual_title, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id)
            DO UPDATE SET manual_title = excluded.manual_title, updated_at = excluded.updated_at
            """,
            (client_id, manual_title, current),
        )

    @staticmethod
    def _delete_title_prefs_conn(conn: sqlite3.Connection, client_id: str) -> None:
        """在事务里删除称号偏好记录。"""

        conn.execute("DELETE FROM title_prefs WHERE client_id = ?", (client_id,))

    @staticmethod
    def _owned_titles_set(conn: sqlite3.Connection, client_id: str) -> set[str]:
        """读取玩家已拥有称号集合。"""

        rows = conn.execute(
            "SELECT title FROM player_titles WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        return {str(r["title"]) for r in rows}

    @staticmethod
    def _is_owned(conn: sqlite3.Connection, client_id: str, title_name: str) -> bool:
        """判断玩家是否拥有某个称号。"""

        row = conn.execute(
            "SELECT 1 FROM player_titles WHERE client_id = ? AND title = ? LIMIT 1",
            (client_id, title_name),
        ).fetchone()
        return row is not None

    def _title_stats_conn(self, conn: sqlite3.Connection, client_id: str, player: dict[str, Any]) -> dict[str, Any]:
        """收集称号判断需要的玩家统计。"""

        def count(table: str, where: str, params: tuple[Any, ...]) -> int:
            return self._count_conn(conn, table, where, params)

        vault = conn.execute("SELECT balance FROM source_vaults WHERE client_id = ?", (client_id,)).fetchone()
        vault_balance = int(vault["balance"]) if vault else 0
        source_stones = int(player["source_stones"])
        weapon_row = conn.execute(
            """
            SELECT COALESCE(MAX(max_level), 0) AS max_level,
                   COALESCE(MAX(level), 0) AS level
            FROM player_weapons
            WHERE holder_id = ?
            """,
            (client_id,),
        ).fetchone()
        max_weapon_level = int(weapon_row["max_level"]) if weapon_row else 0
        highest_weapon_level = int(weapon_row["level"]) if weapon_row else 0

        # 二手市场支出
        market_spend_row = conn.execute(
            """
            SELECT COALESCE(SUM(total_price + fee), 0) AS total
            FROM second_hand_records
            WHERE buyer_id = ?
            """,
            (client_id,),
        ).fetchone()
        market_spend = int(market_spend_row["total"] or 0) if market_spend_row else 0

        # 二手市场成交笔数
        market_deals = count(
            "second_hand_records",
            "buyer_id = ? OR seller_id = ?",
            (client_id, client_id),
        )

        # 装备全部开孔
        all_slots_opened = self._all_equipment_slots_opened(conn, client_id)

        # 淬锋次数
        temper_count = count("game_logs", "client_id = ? AND action = '武器淬锋'", (client_id,))

        # 抢劫成功次数
        robbery_win_count = self.stat_count_conn(
            conn, client_id, "robbery_win_count",
            "duel_records", "winner_id = ? AND mode = 'robbery'", (client_id,),
        )

        # 复仇成功
        revenge_count = count("game_logs", "client_id = ? AND action = '复仇成功'", (client_id,))

        # 异火数量
        flame_count = count("player_flames", "client_id = ?", (client_id,))

        # 藏宝图出价
        treasure_bid_count = count("treasure_map_bids", "client_id = ?", (client_id,))

        # 世界物资累计回收
        total_world_med = self.stat_count_conn(
            conn, client_id, "total_world_med",
            "world_material_records", "client_id = ? AND category = '药路'", (client_id,),
        )
        total_world_build = self.stat_count_conn(
            conn, client_id, "total_world_build",
            "world_material_records", "client_id = ? AND category = '建设'", (client_id,),
        )
        total_world_relic = self.stat_count_conn(
            conn, client_id, "total_world_relic",
            "world_material_records", "client_id = ? AND category = '古物'", (client_id,),
        )
        total_world_trophy = self.stat_count_conn(
            conn, client_id, "total_world_trophy",
            "world_material_records", "client_id = ? AND category = '战利品'", (client_id,),
        )

        return {
            "source_stones": source_stones,
            "vault_balance": vault_balance,
            "total_assets": source_stones + vault_balance,
            "level": int(player.get("level", 1)),
            "sign_count": self.stat_count_conn(
                conn, client_id, "sign_count",
                "game_logs", "client_id = ? AND action = '签到'", (client_id,),
            ),
            "explore_count": self.stat_count_conn(
                conn, client_id, "explore_count",
                "exploration_records", "client_id = ?", (client_id,),
            ),
            "recent_explore_count": self._recent_count_conn(
                conn, "exploration_records", "client_id = ?", (client_id,),
                time_column="started_at",
            ),
            "trade_sell_count": self.stat_count_conn(
                conn, client_id, "trade_sell_count",
                "trade_records", "client_id = ? AND action = 'sell'", (client_id,),
            ),
            "recent_trade_sell_count": self._recent_count_conn(
                conn, "trade_records", "client_id = ? AND action = 'sell'", (client_id,),
            ),
            "recent_world_med_count": self._recent_count_conn(
                conn, "world_material_records", "client_id = ? AND category = '药路'", (client_id,),
            ),
            "recent_world_life_count": self._recent_count_conn(
                conn, "world_material_records", "client_id = ? AND category = '民生'", (client_id,),
            ),
            "recent_world_build_count": self._recent_count_conn(
                conn, "world_material_records", "client_id = ? AND category = '建设'", (client_id,),
            ),
            "recent_world_relic_count": self._recent_count_conn(
                conn, "world_material_records", "client_id = ? AND category = '古物'", (client_id,),
            ),
            "recent_special_sell_count": self._recent_count_conn(
                conn,
                "trade_records",
                "client_id = ? AND action IN ('special_sell', 'special_auto_sell')",
                (client_id,),
            ),
            "trade_net": self.stat_total_conn(
                conn, client_id, "trade_net",
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
            ),
            "weapon_count": count("player_weapons", "holder_id = ?", (client_id,)),
            "weapon_recycle_count": self.stat_count_conn(
                conn, client_id, "weapon_recycle_count",
                "weapon_recycle_records", "client_id = ?", (client_id,),
            ),
            "gem_recycle_count": self.stat_count_conn(
                conn, client_id, "gem_recycle_count",
                "gem_recycle_records", "client_id = ?", (client_id,),
            ),
            "book_recycle_count": self.stat_count_conn(
                conn, client_id, "book_recycle_count",
                "book_recycle_records", "client_id = ?", (client_id,),
            ),
            "wormhole_count": self.stat_count_conn(
                conn, client_id, "wormhole_count",
                "wormhole_participants", "client_id = ?", (client_id,),
            ),
            "wormhole_damage": self.stat_total_conn(
                conn, client_id, "wormhole_damage",
                "SELECT COALESCE(SUM(damage), 0) AS total FROM wormhole_participants WHERE client_id = ?",
                (client_id,),
            ),
            "boss_count": self.stat_count_conn(
                conn, client_id, "boss_count",
                "seasonal_boss_participants", "client_id = ?", (client_id,),
            ),
            "boss_damage": self.stat_total_conn(
                conn, client_id, "boss_damage",
                "SELECT COALESCE(SUM(damage), 0) AS total FROM seasonal_boss_participants WHERE client_id = ?",
                (client_id,),
            ),
            "duel_win_count": self.stat_count_conn(
                conn, client_id, "duel_win_count",
                "duel_records", "winner_id = ?", (client_id,),
            ),
            "recent_duel_win_count": self._recent_count_conn(
                conn, "duel_records", "winner_id = ?", (client_id,),
            ),
            "inscription_count": self.stat_count_conn(
                conn, client_id, "inscription_count",
                "game_logs",
                "client_id = ? AND action IN ('铭刻装备', '铭刻武器', '铭刻附魔', '铭刻自带技能')",
                (client_id,),
            ),
            "rare_weapon": self._exists_conn(
                conn, "player_weapons",
                "holder_id = ? AND quality IN ('稀品', '珍品')", (client_id,),
            ),
            "max_weapon_level": max_weapon_level,
            "highest_weapon_level": highest_weapon_level,
            # 新增统计
            "market_spend": market_spend,
            "market_deals": market_deals,
            "all_slots_opened": all_slots_opened,
            "temper_count": temper_count,
            "robbery_win_count": robbery_win_count,
            "revenge_count": revenge_count,
            "flame_count": flame_count,
            "treasure_bid_count": treasure_bid_count,
            "total_world_med": total_world_med,
            "total_world_build": total_world_build,
            "total_world_relic": total_world_relic,
            "total_world_trophy": total_world_trophy,
            "sect_member": self._exists_conn(conn, "sect_members", "client_id = ?", (client_id,)),
            "sect_contribution": self._sect_contribution(conn, client_id),
        }

    @staticmethod
    def _all_equipment_slots_opened(conn: sqlite3.Connection, client_id: str) -> bool:
        """判断装备是否全部开孔。"""

        rows = conn.execute(
            "SELECT slot, hole_count FROM fixed_equipment WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        if not rows:
            return False
        return all(int(r["hole_count"]) >= 1 for r in rows)

    @staticmethod
    def _sect_contribution(conn: sqlite3.Connection, client_id: str) -> int:
        """读取宗门个人贡献。"""

        row = conn.execute(
            """
            SELECT COALESCE(SUM(influence), 0) AS total
            FROM sect_contribution_records
            WHERE client_id = ?
            """,
            (client_id,),
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    @staticmethod
    def _title_rules(stats: dict[str, Any]) -> tuple[tuple[int, str, str, bool], ...]:
        """把玩家统计转成称号规则。返回 (优先级, 称号名, reason, 是否满足)。"""

        explore_regular = stats["recent_explore_count"] >= 3 or stats["explore_count"] >= 5
        explore_regular_reason = (
            f"近况探险 {stats['recent_explore_count']} 次"
            if stats["recent_explore_count"] >= 3
            else f"累计探险 {stats['explore_count']} 次"
        )
        trade_regular = stats["recent_trade_sell_count"] >= 5 or stats["trade_sell_count"] >= 20
        trade_regular_reason = (
            f"近况跑商出售 {stats['recent_trade_sell_count']} 次"
            if stats["recent_trade_sell_count"] >= 5
            else f"普通跑商出售 {stats['trade_sell_count']} 次"
        )
        duel_regular = stats["recent_duel_win_count"] >= 2 or stats["duel_win_count"] >= 3
        duel_regular_reason = (
            f"近况对战胜利 {stats['recent_duel_win_count']} 次"
            if stats["recent_duel_win_count"] >= 2
            else f"对战胜利 {stats['duel_win_count']} 次"
        )
        rules = (
            # 入门
            (10, "初入仙途", "已经创建修仙角色", True),
            (15, "问道初心", f"等级 {stats['level']}", stats["level"] >= 10),
            (22, "半步金丹", f"等级 {stats['level']}", stats["level"] >= 50),
            (68, "元婴修士", f"等级 {stats['level']}", stats["level"] >= 80),
            (95, "天人合一", f"等级 {stats['level']}", stats["level"] >= 100),
            # 签到
            (18, "晨钟常客", f"累计签到 {stats['sign_count']} 次", stats["sign_count"] >= 7),
            (48, "风雨无阻", f"累计签到 {stats['sign_count']} 次", stats["sign_count"] >= 30),
            (78, "日夜兼程", f"累计签到 {stats['sign_count']} 次", stats["sign_count"] >= 100),
            # 经济
            (20, "小富即安", "随身源石达到 5 万", stats["source_stones"] >= 50_000),
            (24, "藏源有道", "源库余额达到 10 万", stats["vault_balance"] >= 100_000),
            (28, "财气盈门", "明面资产达到 30 万", stats["total_assets"] >= 300_000),
            (38, "商路识途", f"跑商净利润 {money(stats['trade_net'])}", stats["trade_net"] >= 100_000),
            (70, "腰缠万贯", "随身源石达到 50 万", stats["source_stones"] >= 500_000),
            (80, "富甲一方", "总资产达到 200 万", stats["total_assets"] >= 2_000_000),
            (72, "跑商巨贾", f"跑商净利润 {money(stats['trade_net'])}", stats["trade_net"] >= 500_000),
            # 探险
            (30, "探险常客", explore_regular_reason, explore_regular),
            (34, "山河熟客", f"累计探险 {stats['explore_count']} 次", stats["explore_count"] >= 30),
            (65, "万险不惧", f"累计探险 {stats['explore_count']} 次", stats["explore_count"] >= 100),
            (85, "踏遍山河", f"累计探险 {stats['explore_count']} 次", stats["explore_count"] >= 200),
            # 世界物资
            (36, "丹火借道客", f"近况回收药路 {stats['recent_world_med_count']} 次", stats["recent_world_med_count"] >= 3),
            (37, "灯火续命人", f"近况回收民生 {stats['recent_world_life_count']} 次", stats["recent_world_life_count"] >= 3),
            (39, "护城搬山客", f"近况回收建设 {stats['recent_world_build_count']} 次", stats["recent_world_build_count"] >= 3),
            (41, "秘库经手人", f"近况回收古物 {stats['recent_world_relic_count']} 次", stats["recent_world_relic_count"] >= 2),
            (42, "战备供货人", f"近况战利品出售 {stats['recent_special_sell_count']} 次", stats["recent_special_sell_count"] >= 3),
            (44, "看炉顺药人", f"累计回收药路 {stats['total_world_med']} 次", stats["total_world_med"] >= 15),
            (49, "垒城匠手", f"累计回收建设 {stats['total_world_build']} 次", stats["total_world_build"] >= 15),
            (77, "旧宝摸金者", f"累计回收古物 {stats['total_world_relic']} 次", stats["total_world_relic"] >= 10),
            (81, "镇妖战备客", f"累计回收战利品 {stats['total_world_trophy']} 次", stats["total_world_trophy"] >= 10),
            # 武器
            (40, "兵器收藏家", f"拥有武器 {stats['weapon_count']} 把", stats["weapon_count"] >= 5),
            (43, "百炼持刃", f"最高武器等级 {stats['highest_weapon_level']}", stats["highest_weapon_level"] >= 40),
            (45, "铸剑客", f"出售武器 {stats['weapon_recycle_count']} 次", stats["weapon_recycle_count"] >= 3),
            (46, "藏经归客", f"出售技能书 {stats['book_recycle_count']} 次", stats["book_recycle_count"] >= 3),
            (47, "琢玉散人", f"出售宝石 {stats['gem_recycle_count']} 次", stats["gem_recycle_count"] >= 3),
            (55, "欧气外露", "拥有稀品或珍品武器", stats["rare_weapon"]),
            (58, "满锋候选", f"最高武器上限 {stats['max_weapon_level']}", stats["max_weapon_level"] >= 80),
            (51, "多刃之主", f"拥有武器 {stats['weapon_count']} 把", stats["weapon_count"] >= 10),
            (79, "七窍玲珑", "装备全部开孔", stats["all_slots_opened"]),
            (83, "淬锋达人", f"武器淬锋 {stats['temper_count']} 次", stats["temper_count"] >= 3),
            # 战斗
            (50, "虫洞先锋", f"参与虫洞 {stats['wormhole_count']} 次", stats["wormhole_count"] > 0),
            (52, "虫洞鏖战者", f"虫洞累计伤害 {stats['wormhole_damage']}", stats["wormhole_damage"] >= 20_000),
            (74, "虚空破壁人", f"参与虫洞 {stats['wormhole_count']} 次", stats["wormhole_count"] >= 10),
            (60, "岁时赴约人", f"挑战岁时首领 {stats['boss_count']} 次", stats["boss_count"] > 0),
            (62, "情劫破阵者", f"首领累计伤害 {stats['boss_damage']}", stats["boss_damage"] >= 20_000),
            (76, "岁寒知己", f"挑战首领 {stats['boss_count']} 次", stats["boss_count"] >= 10),
            (64, "斗法胜手", duel_regular_reason, duel_regular),
            (82, "独步一方", f"对战胜利 {stats['duel_win_count']} 次", stats["duel_win_count"] >= 15),
            (53, "剑走偏锋", f"抢劫成功 {stats['robbery_win_count']} 次", stats["robbery_win_count"] >= 3),
            # 铭刻
            (66, "羽墨留名", f"铭刻 {stats['inscription_count']} 次", stats["inscription_count"] >= 1),
            (88, "万铭归一", f"铭刻 {stats['inscription_count']} 次", stats["inscription_count"] >= 5),
            # 宗门
            (32, "宗门弟子", "已加入宗门", stats["sect_member"]),
            (75, "门中翘楚", f"宗门贡献 {stats['sect_contribution']}", stats["sect_contribution"] >= 5000),
            # 特殊
            (56, "睚眦必报", f"复仇成功 {stats['revenge_count']} 次", stats["revenge_count"] > 0),
            (86, "火种拾遗", f"拥有异火 {stats['flame_count']} 种", stats["flame_count"] > 0),
            (69, "寻宝人", f"藏宝图出价 {stats['treasure_bid_count']} 次", stats["treasure_bid_count"] > 0),
            (57, "市井掮客", f"二手市场成交 {stats['market_deals']} 笔", stats["market_deals"] >= 10),
            (73, "散财有道", f"二手市场支出 {money(stats['market_spend'])}", stats["market_spend"] >= 300_000),
        )
        return rules

    @staticmethod
    def _save_valid_titles_conn(
        conn: sqlite3.Connection,
        client_id: str,
        rules: tuple[tuple[int, str, str, bool], ...],
        current: str,
    ) -> list[tuple[int, str]]:
        """写入当前有效称号，并返回可佩戴称号列表。"""

        valid: list[tuple[int, str]] = []
        for score, title, reason, ok in rules:
            if not ok:
                continue
            valid.append((score, title))
            conn.execute(
                """
                INSERT INTO player_titles
                (client_id, title, reason, active, obtained_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                ON CONFLICT(client_id, title)
                DO UPDATE SET reason = excluded.reason, updated_at = excluded.updated_at
                """,
                (client_id, title, reason, current, current),
            )

        return valid


service = TitleService()
