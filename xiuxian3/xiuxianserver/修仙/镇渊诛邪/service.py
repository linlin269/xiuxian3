"""镇渊与诛邪组件服务。

首版只实现成长展示、积分接入、日志记录和玩家侧可读面板，不引入额外玩法。
"""

from __future__ import annotations

import sqlite3
from math import floor
from typing import Any

from ..common import CoreService, dump_json, ts
from ..format_text import T
from ..sql import db


POINT_SOURCE_NORMAL_MONSTER = "normal_monster"
POINT_SOURCE_NPC_KILL = "npc_kill"
POINT_SOURCE_WORMHOLE_BOSS = "wormhole_boss"
POINT_SOURCE_LEADER_BOSS = "leader_boss"
POINT_SOURCE_BOSS_KILL = "boss_kill"

POINT_RATIO_NORMAL_MONSTER = 1.0
POINT_RATIO_NPC_KILL = 10.0
POINT_RATIO_BOSS_KILL = 50.0

POINT_SOURCE_LABELS = {
    POINT_SOURCE_NORMAL_MONSTER: "普通怪",
    POINT_SOURCE_NPC_KILL: "NPC",
    POINT_SOURCE_WORMHOLE_BOSS: "虫洞 Boss",
    POINT_SOURCE_LEADER_BOSS: "首领",
    POINT_SOURCE_BOSS_KILL: "高价值 Boss",
}


ZHENYUAN_STAGE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "stage": 0,
        "name": "未启镇渊",
        "points": 0,
        "hp": 0,
        "defense": 0,
        "attack": 0,
        "spirit": 0,
        "recover_bonus": 0.0,
        "explore_bonus": 0.0,
        "black_market_discount": 0.0,
    },
    {
        "stage": 1,
        "name": "镇渊 1",
        "points": 500,
        "hp": 100,
        "defense": 15,
        "attack": 20,
        "spirit": 10,
        "recover_bonus": 0.02,
        "explore_bonus": 0.01,
        "black_market_discount": 0.005,
    },
    {
        "stage": 2,
        "name": "镇渊 2",
        "points": 2_000,
        "hp": 200,
        "defense": 30,
        "attack": 40,
        "spirit": 20,
        "recover_bonus": 0.04,
        "explore_bonus": 0.02,
        "black_market_discount": 0.01,
    },
    {
        "stage": 3,
        "name": "镇渊 3",
        "points": 5_000,
        "hp": 300,
        "defense": 45,
        "attack": 60,
        "spirit": 30,
        "recover_bonus": 0.06,
        "explore_bonus": 0.03,
        "black_market_discount": 0.015,
    },
    {
        "stage": 4,
        "name": "镇渊 4",
        "points": 10_000,
        "hp": 400,
        "defense": 60,
        "attack": 80,
        "spirit": 40,
        "recover_bonus": 0.08,
        "explore_bonus": 0.04,
        "black_market_discount": 0.02,
    },
    {
        "stage": 5,
        "name": "镇渊 5",
        "points": 20_000,
        "hp": 500,
        "defense": 75,
        "attack": 100,
        "spirit": 50,
        "recover_bonus": 0.10,
        "explore_bonus": 0.05,
        "black_market_discount": 0.025,
    },
    {
        "stage": 6,
        "name": "镇渊 6",
        "points": 40_000,
        "hp": 600,
        "defense": 90,
        "attack": 120,
        "spirit": 60,
        "recover_bonus": 0.12,
        "explore_bonus": 0.06,
        "black_market_discount": 0.03,
    },
    {
        "stage": 7,
        "name": "镇渊 7",
        "points": 70_000,
        "hp": 700,
        "defense": 105,
        "attack": 140,
        "spirit": 70,
        "recover_bonus": 0.14,
        "explore_bonus": 0.07,
        "black_market_discount": 0.035,
    },
    {
        "stage": 8,
        "name": "镇渊 8",
        "points": 120_000,
        "hp": 800,
        "defense": 120,
        "attack": 160,
        "spirit": 80,
        "recover_bonus": 0.16,
        "explore_bonus": 0.08,
        "black_market_discount": 0.04,
    },
    {
        "stage": 9,
        "name": "镇渊 9",
        "points": 200_000,
        "hp": 900,
        "defense": 135,
        "attack": 180,
        "spirit": 90,
        "recover_bonus": 0.18,
        "explore_bonus": 0.09,
        "black_market_discount": 0.045,
    },
    {
        "stage": 10,
        "name": "镇渊 10",
        "points": 350_000,
        "hp": 1_000,
        "defense": 150,
        "attack": 200,
        "spirit": 100,
        "recover_bonus": 0.20,
        "explore_bonus": 0.10,
        "black_market_discount": 0.05,
    },
)

ZHUXIE_STAGE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "stage": 0,
        "name": "未启诛邪",
        "points": 0,
        "hp_mult": 1.0,
        "defense_mult": 1.0,
        "attack_mult": 1.0,
        "spirit_mult": 1.0,
        "recover_bonus": 0.0,
        "explore_bonus": 0.0,
        "black_market_discount": 0.0,
    },
    {
        "stage": 1,
        "name": "诛邪 1",
        "points": 500_000,
        "hp_mult": 1.02,
        "defense_mult": 1.02,
        "attack_mult": 1.02,
        "spirit_mult": 1.02,
        "recover_bonus": 0.03,
        "explore_bonus": 0.02,
        "black_market_discount": 0.01,
    },
    {
        "stage": 2,
        "name": "诛邪 2",
        "points": 800_000,
        "hp_mult": 1.04,
        "defense_mult": 1.04,
        "attack_mult": 1.04,
        "spirit_mult": 1.04,
        "recover_bonus": 0.06,
        "explore_bonus": 0.04,
        "black_market_discount": 0.02,
    },
    {
        "stage": 3,
        "name": "诛邪 3",
        "points": 1_200_000,
        "hp_mult": 1.06,
        "defense_mult": 1.06,
        "attack_mult": 1.06,
        "spirit_mult": 1.06,
        "recover_bonus": 0.09,
        "explore_bonus": 0.06,
        "black_market_discount": 0.03,
    },
    {
        "stage": 4,
        "name": "诛邪 4",
        "points": 1_800_000,
        "hp_mult": 1.08,
        "defense_mult": 1.08,
        "attack_mult": 1.08,
        "spirit_mult": 1.08,
        "recover_bonus": 0.12,
        "explore_bonus": 0.08,
        "black_market_discount": 0.04,
    },
    {
        "stage": 5,
        "name": "诛邪 5",
        "points": 2_500_000,
        "hp_mult": 1.10,
        "defense_mult": 1.10,
        "attack_mult": 1.10,
        "spirit_mult": 1.10,
        "recover_bonus": 0.15,
        "explore_bonus": 0.10,
        "black_market_discount": 0.05,
    },
    {
        "stage": 6,
        "name": "诛邪 6",
        "points": 3_500_000,
        "hp_mult": 1.12,
        "defense_mult": 1.12,
        "attack_mult": 1.12,
        "spirit_mult": 1.12,
        "recover_bonus": 0.18,
        "explore_bonus": 0.12,
        "black_market_discount": 0.06,
    },
    {
        "stage": 7,
        "name": "诛邪 7",
        "points": 5_000_000,
        "hp_mult": 1.14,
        "defense_mult": 1.14,
        "attack_mult": 1.14,
        "spirit_mult": 1.14,
        "recover_bonus": 0.21,
        "explore_bonus": 0.14,
        "black_market_discount": 0.07,
    },
    {
        "stage": 8,
        "name": "诛邪 8",
        "points": 7_000_000,
        "hp_mult": 1.16,
        "defense_mult": 1.16,
        "attack_mult": 1.16,
        "spirit_mult": 1.16,
        "recover_bonus": 0.24,
        "explore_bonus": 0.16,
        "black_market_discount": 0.08,
    },
    {
        "stage": 9,
        "name": "诛邪 9",
        "points": 10_000_000,
        "hp_mult": 1.18,
        "defense_mult": 1.18,
        "attack_mult": 1.18,
        "spirit_mult": 1.18,
        "recover_bonus": 0.27,
        "explore_bonus": 0.18,
        "black_market_discount": 0.09,
    },
    {
        "stage": 10,
        "name": "诛邪 10",
        "points": 15_000_000,
        "hp_mult": 1.20,
        "defense_mult": 1.20,
        "attack_mult": 1.20,
        "spirit_mult": 1.20,
        "recover_bonus": 0.30,
        "explore_bonus": 0.20,
        "black_market_discount": 0.10,
    },
)


class ZhenYuanZhuxieService(CoreService):
    """镇渊与诛邪：成长、展示和积分接入。"""

    def ensure_profile(self, client_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return dict(self.ensure_profile_conn(conn, client_id))

    def ensure_profile_conn(self, conn: sqlite3.Connection, client_id: str) -> sqlite3.Row:
        current_ts = ts()
        conn.execute(
            """
            INSERT OR IGNORE INTO player_zhenyuan_zhuxie (
                client_id,
                zhenyuan_stage,
                zhuxie_stage,
                zhenyuan_points,
                zhenyuan_base_hp,
                zhenyuan_base_def,
                zhenyuan_base_atk,
                zhenyuan_base_spirit,
                zhenyuan_recovery_bonus,
                zhenyuan_explore_bonus,
                zhenyuan_black_market_discount,
                zhuxie_hp_multiplier,
                zhuxie_def_multiplier,
                zhuxie_atk_multiplier,
                zhuxie_spirit_multiplier,
                zhuxie_recovery_bonus,
                zhuxie_explore_bonus,
                zhuxie_black_market_discount,
                updated_at
            )
            VALUES (?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, ?)
            """,
            (client_id, current_ts),
        )
        row = conn.execute(
            "SELECT * FROM player_zhenyuan_zhuxie WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        assert row is not None
        return row

    def profile(self, client_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self.profile_conn(conn, client_id)

    def profile_conn(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        row = self.ensure_profile_conn(conn, client_id)
        self._normalize_source_types_conn(conn, client_id)
        points = max(0, int(row["zhenyuan_points"]))
        zhenyuan_stage = self._stage_by_points(points, ZHENYUAN_STAGE_DEFS)
        zhuxie_stage = self._stage_by_points(points, ZHUXIE_STAGE_DEFS) if zhenyuan_stage["stage"] >= 10 else ZHUXIE_STAGE_DEFS[0]
        next_row = self._next_stage_row(zhenyuan_stage, zhuxie_stage)
        base = self._zhenyuan_base(zhenyuan_stage)
        multipliers = self._zhuxie_multipliers(zhuxie_stage)
        effective = self._effective_base_values(base, multipliers)
        bonuses = self._bonus_values(zhenyuan_stage, zhuxie_stage)
        self._sync_profile_snapshot_conn(conn, client_id, points, zhenyuan_stage, zhuxie_stage)
        return {
            "client_id": client_id,
            "points": points,
            "zhenyuan_stage": int(zhenyuan_stage["stage"]),
            "zhuxie_stage": int(zhuxie_stage["stage"]),
            "phase_name": self._phase_name(zhenyuan_stage, zhuxie_stage),
            "next_points": int(next_row["points"]),
            "next_name": str(next_row["name"]),
            "zhenyuan_name": str(zhenyuan_stage["name"]),
            "zhuxie_name": str(zhuxie_stage["name"]),
            "zhenyuan_base": base,
            "zhuxie_multipliers": multipliers,
            "effective_base": effective,
            "bonuses": bonuses,
            "recent_logs": self._recent_logs_conn(conn, client_id, 5),
        }

    def panel(self, client_id: str) -> str:
        profile = self.profile(client_id)
        panel = T.panel()
        panel.section("镇渊 / 诛邪")
        panel.line(f"阶段：**{profile['phase_name']}**")
        panel.line(f"积分：**{profile['points']:,}** / {profile['next_points']:,}（下一级：{profile['next_name']}）")
        panel.hr()
        panel.section("镇渊基础值")
        base = profile["zhenyuan_base"]
        panel.line(f"血量 +**{base['hp']}**｜防御 +**{base['defense']}**｜攻击 +**{base['attack']}**｜精神 +**{base['spirit']}**")
        panel.line(f"恢复 +**{self._percent_text(profile['bonuses']['recover_bonus'])}**｜探险 +**{self._percent_text(profile['bonuses']['explore_bonus'])}**｜黑市折扣 **-{self._percent_text(profile['bonuses']['black_market_discount'])}**")
        panel.hr()
        panel.section("诛邪乘区")
        mult = profile["zhuxie_multipliers"]
        panel.line(f"血量 ×**{mult['hp_mult']:.2f}**｜防御 ×**{mult['defense_mult']:.2f}**｜攻击 ×**{mult['attack_mult']:.2f}**｜精神 ×**{mult['spirit_mult']:.2f}**")
        if profile["zhuxie_stage"] > 0:
            eff = profile["effective_base"]
            panel.line(f"实际追加：血量 +**{eff['hp']}**｜防御 +**{eff['defense']}**｜攻击 +**{eff['attack']}**｜精神 +**{eff['spirit']}**")
        else:
            panel.line("尚未进入诛邪阶段。镇渊满级后才会继续成长。")
        panel.hr()
        panel.section("最近积分来源")
        recent_logs = profile["recent_logs"]
        if not recent_logs:
            panel.line("暂无记录")
        else:
            for row in recent_logs:
                panel.line(
                    f"{row['source_name'] or self._source_type_label(str(row['source_type']))}｜{row['source_module']}｜"
                    f"{row['quantity']} 次 => +**{int(row['point_value'])}**"
                )
        return panel.render()

    def overview(self, client_id: str) -> str:
        profile = self.profile(client_id)
        bonuses = profile["bonuses"]
        display_base = profile["effective_base"] if profile["zhuxie_stage"] > 0 else profile["zhenyuan_base"]
        value_label = "追加" if profile["zhuxie_stage"] > 0 else "基础"
        return (
            f"**{profile['phase_name']}**｜积分 **{profile['points']:,}** / {profile['next_points']:,}，"
            f"{value_label}：血量 +{display_base['hp']} 防御 +{display_base['defense']} 攻击 +{display_base['attack']} 精神 +{display_base['spirit']}，"
            f"恢复 +{self._percent_text(bonuses['recover_bonus'])} 探险 +{self._percent_text(bonuses['explore_bonus'])} "
            f"黑市折扣 -{self._percent_text(bonuses['black_market_discount'])}。<镇渊面板><镇渊积分><镇渊帮助>"
        )

    def points(self, client_id: str) -> str:
        profile = self.profile(client_id)
        panel = T.panel()
        panel.section("镇渊积分")
        panel.line(f"当前：**{profile['points']:,}**｜阶段：**{profile['phase_name']}**")
        panel.line(f"下一阶段：**{profile['next_name']}**｜需求 **{profile['next_points']:,}**")
        panel.hr()
        panel.section("来源记录")
        if not profile["recent_logs"]:
            panel.line("暂无来源记录。")
        else:
            for row in profile["recent_logs"]:
                source_type_text = self._source_type_label(str(row["source_type"]))
                source_name = row["source_name"] or row["source_key"] or source_type_text
                panel.line(
                    f"{row['created_at']}｜{source_type_text}｜{row['source_module']}｜"
                    f"{source_name} => +{int(row['point_value'])}"
                )
        return panel.render()

    def help_info(self, client_id: str | None = None) -> str:
        panel = T.panel()
        panel.section("镇渊与诛邪说明")
        panel.line("镇渊提供固定加值，诛邪只放大镇渊基础值。")
        panel.line("恢复速度只缩短休息耗时，不会再次放大休息恢复倍率。")
        panel.line("黑市折扣只影响购买价，黑市回收价保持原规则，不受镇渊与诛邪折扣影响。")
        panel.hr()
        panel.section("积分换算")
        panel.line("1 积分 = 1 次普通怪击杀")
        panel.line("1 次 NPC 击杀 = 10 积分")
        panel.line("1 次虫洞 Boss 击杀 = 50 积分｜1 次首领击杀 = 50 积分")
        panel.line("等价关系：50 积分 = 50 次普通怪 = 5 次 NPC = 1 次虫洞 Boss = 1 次首领")
        panel.hr()
        panel.section("来源与开放接口原则")
        panel.line("普通怪当前指探险中的普通怪，未来刷怪副本也应复用同一普通怪积分接口。")
        panel.line("积分日志使用稳定类型值：normal_monster / npc_kill / wormhole_boss / leader_boss。")
        panel.line("虫洞 Boss 与首领分开记账，便于审计、统计与后续补偿。")
        panel.line("NPC 击杀接口只保留统一上报入口；当前故意未接任何 NPC 实际玩法。")
        panel.line("镇渊模块只消费外部上报结果，不直接扫描各玩法内部战斗明细。")
        panel.hr()
        panel.section("结算边界")
        panel.line("恢复速度只影响休息指令的耗时，不影响战斗内即时恢复等其他规则。")
        panel.line("探险速度只缩短探险时长，不改变战斗判定、掉落概率与其他探险结算。")
        panel.line("黑市折扣只影响黑市购买价，黑市回收仍按原有回收规则独立结算。")
        panel.hr()
        panel.section("镇渊阶段表")
        for row in ZHENYUAN_STAGE_DEFS[1:]:
            panel.line(
                f"{row['name']}：{row['points']:,}｜血量 +{row['hp']}｜防御 +{row['defense']}｜"
                f"攻击 +{row['attack']}｜精神 +{row['spirit']}｜恢复 +{self._percent_text(row['recover_bonus'])}｜"
                f"探险 +{self._percent_text(row['explore_bonus'])}｜黑市折扣 -{self._percent_text(row['black_market_discount'])}"
            )
        panel.hr()
        panel.section("诛邪阶段表")
        for row in ZHUXIE_STAGE_DEFS[1:]:
            panel.line(
                f"{row['name']}：{row['points']:,}｜血量 ×{row['hp_mult']:.2f}｜防御 ×{row['defense_mult']:.2f}｜"
                f"攻击 ×{row['attack_mult']:.2f}｜精神 ×{row['spirit_mult']:.2f}｜恢复 +{self._percent_text(row['recover_bonus'])}｜"
                f"探险 +{self._percent_text(row['explore_bonus'])}｜黑市折扣 -{self._percent_text(row['black_market_discount'])}"
            )
        return panel.render()

    def add_points(
        self,
        client_id: str,
        quantity: int,
        *,
        source_type: str,
        source_module: str,
        source_key: str = "",
        source_name: str = "",
        point_ratio: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self.add_points_conn(
                conn,
                client_id,
                quantity,
                source_type=source_type,
                source_module=source_module,
                source_key=source_key,
                source_name=source_name,
                point_ratio=point_ratio,
                extra=extra,
            )

    def add_points_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        quantity: int,
        *,
        source_type: str,
        source_module: str,
        source_key: str = "",
        source_name: str = "",
        point_ratio: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if quantity <= 0:
            return self.profile_conn(conn, client_id)
        point_value = max(0, int(round(float(quantity) * float(point_ratio))))
        if point_value <= 0:
            return self.profile_conn(conn, client_id)
        self.ensure_profile_conn(conn, client_id)
        conn.execute(
            """
            UPDATE player_zhenyuan_zhuxie
            SET zhenyuan_points = zhenyuan_points + ?, updated_at = ?
            WHERE client_id = ?
            """,
            (point_value, ts(), client_id),
        )
        conn.execute(
            """
            INSERT INTO player_zhenyuan_zhuxie_point_logs
            (client_id, source_type, source_module, source_key, source_name, quantity, point_ratio, point_value, created_at, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                source_type,
                source_module,
                source_key,
                source_name,
                int(quantity),
                float(point_ratio),
                point_value,
                ts(),
                dump_json(extra or {}),
            ),
        )
        profile = self.profile_conn(conn, client_id)
        self.recalc_player_conn(conn, client_id)
        return profile

    def add_monster_points(self, client_id: str, quantity: int, source_name: str = "普通怪", *, source_key: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points(
            client_id,
            quantity,
            source_type=POINT_SOURCE_NORMAL_MONSTER,
            source_module="探险",
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_NORMAL_MONSTER,
            extra=extra,
        )

    def add_npc_points(self, client_id: str, quantity: int, source_name: str = "NPC", *, source_key: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points(
            client_id,
            quantity,
            source_type=POINT_SOURCE_NPC_KILL,
            source_module="外部模块",
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_NPC_KILL,
            extra=extra,
        )

    def add_boss_points(self, client_id: str, quantity: int, source_name: str = "Boss", *, source_key: str = "", source_module: str = "首领", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points(
            client_id,
            quantity,
            source_type=self._boss_source_type(source_module),
            source_module=source_module,
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_BOSS_KILL,
            extra=extra,
        )

    def grant_monster_points_conn(self, conn: sqlite3.Connection, client_id: str, quantity: int, source_name: str = "普通怪", *, source_key: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points_conn(
            conn,
            client_id,
            quantity,
            source_type=POINT_SOURCE_NORMAL_MONSTER,
            source_module="探险",
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_NORMAL_MONSTER,
            extra=extra,
        )

    def grant_npc_points_conn(self, conn: sqlite3.Connection, client_id: str, quantity: int, source_name: str = "NPC", *, source_key: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points_conn(
            conn,
            client_id,
            quantity,
            source_type=POINT_SOURCE_NPC_KILL,
            source_module="外部模块",
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_NPC_KILL,
            extra=extra,
        )

    def grant_boss_points_conn(self, conn: sqlite3.Connection, client_id: str, quantity: int, source_name: str = "Boss", *, source_key: str = "", source_module: str = "首领", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.add_points_conn(
            conn,
            client_id,
            quantity,
            source_type=self._boss_source_type(source_module),
            source_module=source_module,
            source_key=source_key,
            source_name=source_name,
            point_ratio=POINT_RATIO_BOSS_KILL,
            extra=extra,
        )

    def player_bonus(self, client_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self.player_bonus_conn(conn, client_id)

    def player_bonus_conn(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        profile = self.profile_conn(conn, client_id)
        bonuses = profile["bonuses"]
        effective = profile["effective_base"]
        return {
            "hp_bonus": int(effective["hp"]),
            "defense_bonus": int(effective["defense"]),
            "attack_bonus": int(effective["attack"]),
            "spirit_bonus": int(effective["spirit"]),
            "recover_bonus": float(bonuses["recover_bonus"]),
            "explore_bonus": float(bonuses["explore_bonus"]),
            "trade_bonus": 0.0,
            "black_market_discount": float(bonuses["black_market_discount"]),
        }

    def recent_logs(self, client_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            return self._recent_logs_conn(conn, client_id, limit)

    @staticmethod
    def _stage_by_points(points: int, stage_defs: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        current = stage_defs[0]
        for row in stage_defs:
            if int(points) >= int(row["points"]):
                current = row
            else:
                break
        return current

    @staticmethod
    def _next_stage_row(zhenyuan_stage: dict[str, Any], zhuxie_stage: dict[str, Any]) -> dict[str, Any]:
        if int(zhenyuan_stage["stage"]) < 10:
            return ZHENYUAN_STAGE_DEFS[int(zhenyuan_stage["stage"]) + 1]
        if int(zhuxie_stage["stage"]) < 10:
            return ZHUXIE_STAGE_DEFS[int(zhuxie_stage["stage"]) + 1]
        return ZHUXIE_STAGE_DEFS[-1]

    @staticmethod
    def _zhenyuan_base(row: dict[str, Any]) -> dict[str, int]:
        return {
            "hp": int(row["hp"]),
            "defense": int(row["defense"]),
            "attack": int(row["attack"]),
            "spirit": int(row["spirit"]),
        }

    @staticmethod
    def _zhuxie_multipliers(row: dict[str, Any]) -> dict[str, float]:
        return {
            "hp_mult": float(row["hp_mult"]),
            "defense_mult": float(row["defense_mult"]),
            "attack_mult": float(row["attack_mult"]),
            "spirit_mult": float(row["spirit_mult"]),
        }

    @staticmethod
    def _effective_base_values(base: dict[str, int], multipliers: dict[str, float]) -> dict[str, int]:
        if all(abs(float(value) - 1.0) < 1e-9 for value in multipliers.values()):
            return dict(base)
        return {
            "hp": max(0, int(floor(base["hp"] * multipliers["hp_mult"]))),
            "defense": max(0, int(floor(base["defense"] * multipliers["defense_mult"]))),
            "attack": max(0, int(floor(base["attack"] * multipliers["attack_mult"]))),
            "spirit": max(0, int(floor(base["spirit"] * multipliers["spirit_mult"]))),
        }

    @staticmethod
    def _bonus_values(zhenyuan_stage: dict[str, Any], zhuxie_stage: dict[str, Any]) -> dict[str, float]:
        return {
            "recover_bonus": float(zhenyuan_stage["recover_bonus"]) + float(zhuxie_stage["recover_bonus"]),
            "explore_bonus": float(zhenyuan_stage["explore_bonus"]) + float(zhuxie_stage["explore_bonus"]),
            "black_market_discount": float(zhenyuan_stage["black_market_discount"]) + float(zhuxie_stage["black_market_discount"]),
        }

    def _normalize_source_types_conn(self, conn: sqlite3.Connection, client_id: str) -> None:
        conn.execute(
            """
            UPDATE player_zhenyuan_zhuxie_point_logs
            SET source_type = CASE
                WHEN source_type = '普通怪' THEN ?
                WHEN source_type = 'NPC' THEN ?
                WHEN source_type = 'Boss' AND source_module IN ('虫洞', '异界虫洞') THEN ?
                WHEN source_type = 'Boss' AND source_module = '首领' THEN ?
                ELSE source_type
            END
            WHERE client_id = ?
              AND (
                  source_type IN ('普通怪', 'NPC')
                  OR (source_type = 'Boss' AND source_module IN ('虫洞', '异界虫洞', '首领'))
              )
            """,
            (
                POINT_SOURCE_NORMAL_MONSTER,
                POINT_SOURCE_NPC_KILL,
                POINT_SOURCE_WORMHOLE_BOSS,
                POINT_SOURCE_LEADER_BOSS,
                client_id,
            ),
        )

    @staticmethod
    def _boss_source_type(source_module: str) -> str:
        module = str(source_module or "")
        if module in {"虫洞", "异界虫洞"}:
            return POINT_SOURCE_WORMHOLE_BOSS
        if module == "首领":
            return POINT_SOURCE_LEADER_BOSS
        return POINT_SOURCE_BOSS_KILL

    @staticmethod
    def _source_type_label(source_type: str) -> str:
        return POINT_SOURCE_LABELS.get(str(source_type or ""), str(source_type or "未命名来源"))

    def _sync_profile_snapshot_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        points: int,
        zhenyuan_stage: dict[str, Any],
        zhuxie_stage: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            UPDATE player_zhenyuan_zhuxie
            SET zhenyuan_points = ?,
                zhenyuan_stage = ?,
                zhuxie_stage = ?,
                zhenyuan_base_hp = ?,
                zhenyuan_base_def = ?,
                zhenyuan_base_atk = ?,
                zhenyuan_base_spirit = ?,
                zhenyuan_recovery_bonus = ?,
                zhenyuan_explore_bonus = ?,
                zhenyuan_black_market_discount = ?,
                zhuxie_hp_multiplier = ?,
                zhuxie_def_multiplier = ?,
                zhuxie_atk_multiplier = ?,
                zhuxie_spirit_multiplier = ?,
                zhuxie_recovery_bonus = ?,
                zhuxie_explore_bonus = ?,
                zhuxie_black_market_discount = ?,
                updated_at = ?
            WHERE client_id = ?
            """,
            (
                int(points),
                int(zhenyuan_stage["stage"]),
                int(zhuxie_stage["stage"]),
                int(zhenyuan_stage["hp"]),
                int(zhenyuan_stage["defense"]),
                int(zhenyuan_stage["attack"]),
                int(zhenyuan_stage["spirit"]),
                float(zhenyuan_stage["recover_bonus"]),
                float(zhenyuan_stage["explore_bonus"]),
                float(zhenyuan_stage["black_market_discount"]),
                float(zhuxie_stage["hp_mult"]),
                float(zhuxie_stage["defense_mult"]),
                float(zhuxie_stage["attack_mult"]),
                float(zhuxie_stage["spirit_mult"]),
                float(zhuxie_stage["recover_bonus"]),
                float(zhuxie_stage["explore_bonus"]),
                float(zhuxie_stage["black_market_discount"]),
                ts(),
                client_id,
            ),
        )

    @staticmethod
    def apply_time_bonus(base_seconds: int, bonus: float, floor_seconds: int = 1) -> int:
        """按比例缩短时间，但不低于最小下限。"""

        seconds = max(int(floor_seconds), int(base_seconds))
        rate = max(0.0, min(0.95, float(bonus)))
        return max(int(floor_seconds), int(floor(seconds * (1 - rate))))

    @staticmethod
    def discounted_price(price: int, discount: float) -> int:
        """按折扣计算购买价，只向下取整。"""

        value = max(1, int(price))
        rate = max(0.0, min(0.95, float(discount)))
        return max(1, int(floor(value * (1 - rate))))

    @staticmethod
    def _phase_name(zhenyuan_stage: dict[str, Any], zhuxie_stage: dict[str, Any]) -> str:
        if int(zhuxie_stage["stage"]) > 0:
            return str(zhuxie_stage["name"])
        return str(zhenyuan_stage["name"])

    @staticmethod
    def _percent_text(value: float) -> str:
        return f"{int(round(float(value) * 100))}%"

    def _recent_logs_conn(self, conn: sqlite3.Connection, client_id: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT record_id, client_id, source_type, source_module, source_key, source_name,
                   quantity, point_ratio, point_value, created_at, extra
            FROM player_zhenyuan_zhuxie_point_logs
            WHERE client_id = ?
            ORDER BY record_id DESC
            LIMIT ?
            """,
            (client_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]


service = ZhenYuanZhuxieService(db)


__all__ = [
    "ZHENYUAN_STAGE_DEFS",
    "ZHUXIE_STAGE_DEFS",
    "ZhenYuanZhuxieService",
    "service",
]
