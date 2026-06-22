"""异火组件服务。

异火独立于背包和纳戒，持有表 player_flames 只允许同名最多一个。
"""

from __future__ import annotations

import json
import math
from typing import Any

from ..format_text import T
from ..common import CoreService, load_json, money, now, random, ts
from ..sql import db


# rank 21~23 可在探险获得；rank 2~23 可在首领/虫洞获得。
# rank 1 帝炎只通过合成。
EXPLORE_FLAME_RANKS = {21, 22, 23}
BOSS_WORMHOLE_FLAME_RANKS = set(range(2, 24))

# rank 23 权重最大，rank 2 权重最小（指数递减）
FLAME_RANK_WEIGHTS: dict[int, float] = {}
for _rank in range(2, 24):
    FLAME_RANK_WEIGHTS[_rank] = max(0.01, 2.0 ** (23 - _rank) * 0.0001)
# 归一化到方便理解的量级
_total_w = sum(FLAME_RANK_WEIGHTS.values())
for _rank in FLAME_RANK_WEIGHTS:
    FLAME_RANK_WEIGHTS[_rank] = FLAME_RANK_WEIGHTS[_rank] / _total_w

EXPLORE_FLAME_WEIGHTS: dict[int, float] = {}
for _rank in EXPLORE_FLAME_RANKS:
    EXPLORE_FLAME_WEIGHTS[_rank] = FLAME_RANK_WEIGHTS[_rank]
_exp_total = sum(EXPLORE_FLAME_WEIGHTS.values())
for _rank in EXPLORE_FLAME_WEIGHTS:
    EXPLORE_FLAME_WEIGHTS[_rank] = EXPLORE_FLAME_WEIGHTS[_rank] / _exp_total

BOSS_WORMHOLE_FLAME_WEIGHTS: dict[int, float] = {}
for _rank in BOSS_WORMHOLE_FLAME_RANKS:
    BOSS_WORMHOLE_FLAME_WEIGHTS[_rank] = FLAME_RANK_WEIGHTS[_rank]
_bw_total = sum(BOSS_WORMHOLE_FLAME_WEIGHTS.values())
for _rank in BOSS_WORMHOLE_FLAME_WEIGHTS:
    BOSS_WORMHOLE_FLAME_WEIGHTS[_rank] = BOSS_WORMHOLE_FLAME_WEIGHTS[_rank] / _bw_total


class FlameService(CoreService):
    """异火列表、持有、装备、合成、发放和补偿。"""

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #

    def list_all(self, client_id: str) -> str:
        """查看 23 种异火列表。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all("SELECT * FROM flame_defs ORDER BY rank")
        owned = {
            row["flame_id"]
            for row in self.db.fetch_all(
                "SELECT flame_id FROM player_flames WHERE client_id = ?", (client_id,)
            )
        }
        equipped = self.db.fetch_one(
            "SELECT flame_id FROM player_flames WHERE client_id = ? AND equipped = 1",
            (client_id,),
        )
        equipped_id = equipped["flame_id"] if equipped else ""

        panel = T.panel()
        panel.section("异火总览")
        for row in rows:
            mark = ""
            if row["flame_id"] == equipped_id:
                mark = " ✦已装备"
            elif row["flame_id"] in owned:
                mark = " ✓已拥有"
            panel.line(
                f"第{row['rank']}名 **{row['name']}**｜"
                f"倍率 x{float(row['attack_multiplier']):.3f}{mark}"
            )
        return panel.render() + T.buttons("异火", "异火交易")

    def detail(self, client_id: str, message: str) -> str:
        """查看单个异火详情。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        name = message.strip()
        if not name:
            return T.hint("缺少异火名称。", "发送：异火详情 玄黄炎")
        flame = self.db.fetch_one(
            "SELECT * FROM flame_defs WHERE name = ?", (name,)
        )
        if not flame:
            return T.hint(f"没有找到异火：{name}。", "发送：异火列表 查看全部异火。<异火列表>")
        owned = self.db.fetch_one(
            "SELECT * FROM player_flames WHERE client_id = ? AND flame_id = ?",
            (client_id, flame["flame_id"]),
        )
        equipped = self.db.fetch_one(
            "SELECT flame_id FROM player_flames WHERE client_id = ? AND equipped = 1",
            (client_id,),
        )
        status_text = "未拥有"
        if owned:
            status_text = "已装备" if owned["flame_id"] == (equipped["flame_id"] if equipped else "") else "已拥有"

        source_map = {
            "fusion": "合成（帝炎专属）",
            "boss_wormhole": "首领奖励 / 虫洞奖励",
            "explore_low": "探险结算 / 首领奖励 / 虫洞奖励",
        }
        source_text = source_map.get(flame["source_type"], flame["source_type"])

        panel = T.panel()
        panel.section(f"异火·{flame['name']}")
        panel.line(f"排名：第 **{flame['rank']}** 名｜状态：{status_text}")
        panel.line(f"攻击倍率：**x{float(flame['attack_multiplier']):.3f}**")
        panel.line(f"外观：{flame['appearance']}")
        panel.line(f"效果：{flame['effect_desc']}")
        panel.line(f"来源：{source_text}")
        return panel.render() + T.buttons("异火列表", "异火")

    def my_flames(self, client_id: str) -> str:
        """查看自己持有的异火和当前装备异火。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all(
            """
            SELECT pf.*, fd.name, fd.rank, fd.attack_multiplier, fd.appearance
            FROM player_flames pf
            JOIN flame_defs fd ON fd.flame_id = pf.flame_id
            WHERE pf.client_id = ?
            ORDER BY fd.rank
            """,
            (client_id,),
        )
        panel = T.panel()
        panel.section("我的异火")
        if not rows:
            panel.line("尚未拥有任何异火。")
            panel.line("异火可通过探险、首领奖励或虫洞奖励获得。")
            return panel.render() + T.buttons("异火列表", "异火交易", "异火合成")

        equipped_row = None
        for row in rows:
            mark = " ✦装备中" if row["equipped"] else ""
            if row["equipped"]:
                equipped_row = row
            panel.line(
                f"第{row['rank']}名 **{row['name']}**｜"
                f"倍率 x{float(row['attack_multiplier']):.3f}{mark}"
            )

        panel.hr()
        if equipped_row:
            panel.line(f"当前装备：**{equipped_row['name']}**（攻击 x{float(equipped_row['attack_multiplier']):.3f}）")
        else:
            panel.line("当前未装备异火。")
        has_di_yan = any(r["flame_id"] == "di_yan" for r in rows)
        if not has_di_yan:
            panel.line(f"持有 {len(rows)}/22 种（集齐 22 种可合成帝炎）")
        else:
            panel.line("已拥有帝炎，异火体系圆满。")
        return panel.render() + T.buttons("异火装备", "异火卸下", "异火合成", "异火列表")

    # ------------------------------------------------------------------ #
    #  装备 / 卸下
    # ------------------------------------------------------------------ #

    def equip(self, client_id: str, message: str) -> str:
        """装备已拥有异火。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        name = message.strip()
        if not name:
            return T.hint("缺少异火名称。", "发送：异火装备 玄黄炎")
        flame_def = self.db.fetch_one(
            "SELECT * FROM flame_defs WHERE name = ?", (name,)
        )
        if not flame_def:
            return T.hint(f"没有找到异火：{name}。", "发送：异火列表 查看全部异火。<异火列表>")
        row = self.db.fetch_one(
            "SELECT * FROM player_flames WHERE client_id = ? AND flame_id = ?",
            (client_id, flame_def["flame_id"]),
        )
        if not row:
            return T.hint(f"你尚未拥有 {name}。", "发送：异火 查看已拥有的异火。<异火>")

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE player_flames SET equipped = 0, updated_at = ? WHERE client_id = ?",
                (ts(), client_id),
            )
            conn.execute(
                "UPDATE player_flames SET equipped = 1, updated_at = ? WHERE client_id = ? AND flame_id = ?",
                (ts(), client_id, flame_def["flame_id"]),
            )
        return f"已装备异火 **{name}**，攻击倍率 x{float(flame_def['attack_multiplier']):.3f}。<异火>"

    def unequip(self, client_id: str) -> str:
        """卸下当前异火。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        row = self.db.fetch_one(
            "SELECT pf.*, fd.name FROM player_flames pf JOIN flame_defs fd ON fd.flame_id = pf.flame_id WHERE pf.client_id = ? AND pf.equipped = 1",
            (client_id,),
        )
        if not row:
            return T.hint("你当前没有装备异火。", "发送：异火装备 装备一个异火。<异火装备>")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE player_flames SET equipped = 0, updated_at = ? WHERE client_id = ? AND equipped = 1",
                (ts(), client_id),
            )
        return f"已卸下异火 **{row['name']}**，攻击倍率回到 1.0。<异火>"

    # ------------------------------------------------------------------ #
    #  合成帝炎
    # ------------------------------------------------------------------ #

    def fuse(self, client_id: str) -> str:
        """尝试合成帝炎。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        # 已有帝炎
        existing = self.db.fetch_one(
            "SELECT 1 FROM player_flames WHERE client_id = ? AND flame_id = 'di_yan'",
            (client_id,),
        )
        if existing:
            return T.hint("你已拥有帝炎，无需重复合成。", "发送：异火 查看异火信息。<异火>")

        # 检查 rank 2~23
        all_defs = self.db.fetch_all(
            "SELECT * FROM flame_defs WHERE rank BETWEEN 2 AND 23 ORDER BY rank"
        )
        owned_map: dict[str, dict] = {}
        for row in self.db.fetch_all(
            "SELECT * FROM player_flames WHERE client_id = ?", (client_id,)
        ):
            owned_map[row["flame_id"]] = row

        missing_names: list[str] = []
        for flame_def in all_defs:
            if flame_def["flame_id"] not in owned_map:
                missing_names.append(flame_def["name"])

        if missing_names:
            missing_text = "、".join(missing_names)
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO flame_fusion_records
                    (client_id, target_flame_id, consumed_flames, result, missing_flames, created_at)
                    VALUES (?, 'di_yan', '[]', 'failed', ?, ?)
                    """,
                    (client_id, json.dumps(missing_names, ensure_ascii=False), ts()),
                )
            return T.hint(f"帝炎合成失败，缺少异火：{missing_text}。", "继续收集缺失异火后再尝试合成。<异火列表>")

        # 全部满足，消耗 rank 2~23，插入帝炎
        consumed_ids = [flame_def["flame_id"] for flame_def in all_defs]
        consumed_names = [flame_def["name"] for flame_def in all_defs]
        with self.db.transaction() as conn:
            for flame_id in consumed_ids:
                conn.execute(
                    "DELETE FROM player_flames WHERE client_id = ? AND flame_id = ?",
                    (client_id, flame_id),
                )
            conn.execute(
                """
                INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at)
                VALUES (?, 'di_yan', 1, '合成', ?, ?)
                """,
                (client_id, ts(), ts()),
            )
            conn.execute(
                """
                INSERT INTO flame_fusion_records
                (client_id, target_flame_id, consumed_flames, result, missing_flames, created_at)
                VALUES (?, 'di_yan', ?, 'success', '[]', ?)
                """,
                (client_id, json.dumps(consumed_names, ensure_ascii=False), ts()),
            )
        return (
            f"帝炎合成成功！消耗 22 种异火，获得 **帝炎**（攻击 x2.000）。\n"
            f"帝炎已自动装备。合成后再无法获得 rank 2~23 异火。<异火>"
        )

    # ------------------------------------------------------------------ #
    #  交易说明
    # ------------------------------------------------------------------ #

    @staticmethod
    def trade_info(client_id: str) -> str:
        """展示异火交易说明。"""

        panel = T.panel()
        panel.section("异火交易")
        panel.line("异火可通过二手市场交易，不支持直接赠送或商城出售。")
        panel.line("")
        panel.section("上架")
        panel.line("二手市场上架 异火名 1 总价")
        panel.line("已装备的异火需要先卸下才能上架。")
        panel.line("")
        panel.section("购买")
        panel.line("二手市场购买 卖家名称")
        panel.line("如果买家已有同名异火或已有帝炎，则购买失败。")
        return panel.render() + T.buttons("异火", "二手市场")

    # ------------------------------------------------------------------ #
    #  异火发放（探险/首领/虫洞 统一入口）
    # ------------------------------------------------------------------ #

    def try_grant_flame(
        self,
        conn,
        client_id: str,
        source: str,
        pool_ranks: set[int] | None = None,
    ) -> dict[str, Any]:
        """尝试发放异火，返回 {"granted": bool, "text": str, "compensation": ...}。

        pool_ranks: 候选 rank 集合；为 None 时按 source 自动选择。
        """

        if pool_ranks is None:
            if source == "explore":
                pool_ranks = EXPLORE_FLAME_RANKS
            else:
                pool_ranks = BOSS_WORMHOLE_FLAME_RANKS

        # 按权重抽一个候选 rank
        weights = {}
        for rank in pool_ranks:
            if source == "explore":
                weights[rank] = EXPLORE_FLAME_WEIGHTS.get(rank, 0.0)
            else:
                weights[rank] = BOSS_WORMHOLE_FLAME_WEIGHTS.get(rank, 0.0)
        total = sum(weights.values())
        if total <= 0:
            return {"granted": False, "text": "", "compensation": None}

        roll = random.random() * total
        current = 0.0
        selected_rank = min(pool_ranks)
        for rank, weight in sorted(weights.items()):
            current += weight
            if roll <= current:
                selected_rank = rank
                break

        flame_def = conn.execute(
            "SELECT * FROM flame_defs WHERE rank = ?", (selected_rank,)
        ).fetchone()
        if not flame_def:
            return {"granted": False, "text": "", "compensation": None}

        flame_def = dict(flame_def)
        flame_id = flame_def["flame_id"]

        # 检查是否已有帝炎
        has_di_yan = conn.execute(
            "SELECT 1 FROM player_flames WHERE client_id = ? AND flame_id = 'di_yan'",
            (client_id,),
        ).fetchone()
        if has_di_yan:
            # 帝炎合成后不能再获得 rank 2~23
            return {
                "granted": False,
                "text": "",
                "compensation": {
                    "flame_name": flame_def["name"],
                    "reason": "已有帝炎",
                },
            }

        # 检查是否已有同名异火
        existing = conn.execute(
            "SELECT 1 FROM player_flames WHERE client_id = ? AND flame_id = ?",
            (client_id, flame_id),
        ).fetchone()
        if existing:
            return {
                "granted": False,
                "text": "",
                "compensation": {
                    "flame_name": flame_def["name"],
                    "reason": "已拥有此异火",
                },
            }

        # 发放
        conn.execute(
            """
            INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at)
            VALUES (?, ?, 0, ?, ?, ?)
            """,
            (client_id, flame_id, source, ts(), ts()),
        )
        return {
            "granted": True,
            "text": f"获得异火 **{flame_def['name']}**（第{flame_def['rank']}名，倍率 x{float(flame_def['attack_multiplier']):.3f}）",
            "flame_name": flame_def["name"],
            "compensation": None,
        }

    def compensation_text(self, flame_name: str, compensation_item: str) -> str:
        """生成固定补偿文案。"""

        return f"本次掉落异火为{flame_name}，您已拥有此异火，本次奖励补偿为{compensation_item}。"

    def roll_explore_flame(self, conn, client_id: str) -> dict[str, Any]:
        """探险结算时尝试掉落异火（rank 21~23）。"""

        return self.try_grant_flame(conn, client_id, "explore", EXPLORE_FLAME_RANKS)

    def roll_boss_wormhole_flame(self, conn, client_id: str) -> dict[str, Any]:
        """首领/虫洞奖励时尝试掉落异火（rank 2~23）。"""

        return self.try_grant_flame(conn, client_id, "boss_wormhole", BOSS_WORMHOLE_FLAME_RANKS)

    # ------------------------------------------------------------------ #
    #  攻击倍率
    # ------------------------------------------------------------------ #

# equipped_multiplier 和 equipped_flame_name 已由 CoreService（common.py）提供，
# FlameService 通过继承即可使用，无需重复定义。

service = FlameService(db)

__all__ = [
    "FlameService",
    "EXPLORE_FLAME_RANKS",
    "BOSS_WORMHOLE_FLAME_RANKS",
    "service",
]
