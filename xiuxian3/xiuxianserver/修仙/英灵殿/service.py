"""英灵殿模块核心业务逻辑。

每小时自动刷新10名NPC精英怪，玩家可单人挑战，
胜利后获得镇渊积分、固定恢复药品和经验奖励。
"""

import json
import logging
import random
import math
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

from ..common import CoreService
from ..combat_log_text import markdown_reply as combat_markdown_reply
from ..battle_log_links import battle_log_markdown
from ..combat_log_text import wants_detail as battle_wants_detail
from ..sql import db
from ..rules import exp_need

# ==================== 常量 ====================

HALL_NPC_COUNT = 10
HALL_EXP_RATIO = 0.005
MAX_LEVEL = 100

NPC_TIER_WEIGHTS = [
    ("UR", 1), ("SSSR", 2), ("SSR", 5), ("SR", 10),
    ("S", 18), ("A", 24), ("B", 22), ("R", 18)
]

NPC_TIER_RATIOS = {
    "UR": 0.40, "SSSR": 0.30, "SSR": 0.22, "SR": 0.16,
    "S": 0.11, "A": 0.07, "B": 0.04, "R": 0.02
}

TIER_KIND_MAP = {
    "UR": "古卫", "SSSR": "魔", "SSR": "龙", "SR": "鬼",
    "S": "妖", "A": "兵", "B": "兽", "R": "傀"
}

NPC_TIER_POINTS = {
    "UR": 50, "SSSR": 30, "SSR": 20, "SR": 12,
    "S": 8, "A": 5, "B": 3, "R": 1
}

NPC_RECOVER_ITEMS = {
    (1, 20):  ("xueqidan", "yinmingcao"),
    (21, 50): ("huichunlu", "ningshenlu"),
    (51, 80): ("shenggudan", "yanghundan"),
    (81, 100): ("shenggudan", "yanghundan"),
}

NPC_RECOVER_ITEM_LABELS = {
    "xueqidan": "血契丹",
    "yinmingcao": "阴冥草",
    "huichunlu": "回春露",
    "ningshenlu": "凝神露",
    "shenggudan": "生骨丹",
    "yanghundan": "养魂丹",
}

NPC_ORDER_SQL = (
    "CASE tier WHEN 'UR' THEN 0 WHEN 'SSSR' THEN 1 WHEN 'SSR' THEN 2 "
    "WHEN 'SR' THEN 3 WHEN 'S' THEN 4 WHEN 'A' THEN 5 WHEN 'B' THEN 6 ELSE 7 END, "
    "level DESC, npc_id ASC"
)


logger = logging.getLogger(__name__)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    """兼容 sqlite3.Row、dict 和其他映射对象的安全取值。"""

    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    getter = getattr(row, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            pass
    try:
        return row[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return default


def _inline_command_link(label: str, command: str) -> str:
    """生成 QQ markdown 无框命令链接。"""

    safe_label = str(label).strip() or str(command).strip()
    command_text = quote(str(command).strip(), safe="")
    return f"[{safe_label}](mqqapi://aio/inlinecmd?command={command_text}&enter=false&reply=false)"


class HallOfHeroesService(CoreService):
    """英灵殿业务逻辑服务。"""

    def _random_tier(self) -> str:
        """按权重随机抽取稀有度。"""
        population = [t[0] for t in NPC_TIER_WEIGHTS]
        weights = [t[1] for t in NPC_TIER_WEIGHTS]
        return random.choices(population, weights, k=1)[0]

    def _random_level_conn(self, conn) -> tuple[int, int, int]:
        """从数据库获取等级范围，返回 (low, high, median_level)。"""
        rows = conn.execute("SELECT level FROM players").fetchall()
        if not rows:
            return (1, 10, 5)
        levels = sorted(r["level"] for r in rows)
        n = len(levels)
        if n % 2 == 1:
            median_level = levels[n // 2]
        else:
            median_level = int((levels[n // 2 - 1] + levels[n // 2]) / 2)
        max_player_level = levels[-1]
        low = max(1, median_level - 10)
        high = min(MAX_LEVEL, max_player_level)
        return (low, high, median_level)

    def _random_level(self, low: int, high: int) -> int:
        """按偏向高端分布随机生成等级。"""
        if random.random() < 0.7:
            return random.randint((low + high) // 2, high)
        else:
            return random.randint(low, high)

    def _world_snapshot_conn(self, conn) -> dict:
        """获取服务器生态快照。"""

        player_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(players)").fetchall()
        }
        query_candidates: list[tuple[str, str | None, str | None]] = []
        if "base_attack" in player_columns:
            query_candidates.append(("base_attack", "SELECT level, hp, base_attack FROM players", "base_attack"))
        if "attack" in player_columns:
            query_candidates.append(("attack", "SELECT level, hp, attack FROM players", "attack"))
        query_candidates.append(("derived", "SELECT level, hp FROM players", None))

        rows = []
        attack_source = "derived"
        for source_name, sql_text, attack_field in query_candidates:
            try:
                rows = conn.execute(sql_text).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such column" not in str(exc):
                    raise
                logger.warning("英灵殿生态快照查询失败，准备切换兼容 SQL：source=%s, error=%s", source_name, exc)
                continue

            attack_source = source_name
            if rows:
                break
        if not rows:
            return {"median_level": 5, "median_hp": 200, "median_attack": 20}

        levels = sorted(int(r["level"]) for r in rows)
        hps = sorted(int(r["hp"]) for r in rows)
        if attack_source == "derived":
            attacks = sorted(max(5, 5 + int(r["level"]) // 10) for r in rows)
        else:
            attack_field = "base_attack" if attack_source == "base_attack" else "attack"
            attacks = sorted(int(r[attack_field]) for r in rows)

        n = len(rows)

        def median(sorted_list):
            if n % 2 == 1:
                return sorted_list[n // 2]
            else:
                return int((sorted_list[n // 2 - 1] + sorted_list[n // 2]) / 2)

        snapshot = {
            "median_level": median(levels),
            "median_hp": median(hps),
            "median_attack": median(attacks),
        }
        logger.info(
            "英灵殿生态快照完成：players_rows=%s, attack_source=%s, median_level=%s, median_hp=%s, median_attack=%s",
            len(rows),
            attack_source,
            snapshot["median_level"],
            snapshot["median_hp"],
            snapshot["median_attack"],
        )
        return snapshot

    def _generate_npc(self, level: int, tier: str, snapshot: dict) -> dict:
        """生成单个NPC的完整数据。"""
        # 固定基值
        base_hp = 20 + level * 28
        base_attack = 5 + level * 1.9
        base_defense = 3 + level * 1.1

        # 首领公式组件
        boss_attack = snapshot["median_hp"] / 22 + level * 1.5
        boss_defense = snapshot["median_attack"] * 0.42
        boss_hp = snapshot["median_hp"] / 18 + level * 12

        # 稀有度比例
        ratio = NPC_TIER_RATIOS[tier]
        raw_hp = base_hp + boss_hp * ratio
        raw_attack = base_attack + boss_attack * ratio
        raw_defense = base_defense + boss_defense * ratio

        # 多样性扰动
        hp = max(1, int(raw_hp * random.uniform(0.981, 1.019)))
        attack = max(1, int(raw_attack * random.uniform(0.981, 1.019)))
        defense = max(1, int(raw_defense * random.uniform(0.981, 1.019)))

        # 命名：使用 chinesename 生成 2~4 字中文名，旧版库接口不一致时兜底
        from chinesename import ChineseName

        cn = ChineseName()
        name = ""
        if hasattr(cn, "getName"):
            for _ in range(16):
                name = str(cn.getName()).strip()
                if 2 <= len(name) <= 4:
                    break
        if not (2 <= len(name) <= 4):
            logger.warning("chinesename 库接口不兼容或返回异常，英灵名称改用内置兜底生成")
            name = f"英灵{random.randint(1000, 9999)}"

        # 族群
        kind = TIER_KIND_MAP[tier]

        return {
            "name": name, "tier": tier, "level": level,
            "hp": hp, "max_hp": hp, "attack": attack, "defense": defense,
            "kind": kind,
        }

    def _current_batch_npcs_conn(self, conn, batch_id: str) -> list[sqlite3.Row]:
        """读取当前批次英灵，按列表展示顺序排序。"""

        return conn.execute(
            f"""SELECT * FROM hall_of_heroes_npcs
               WHERE batch_id = ?
               ORDER BY {NPC_ORDER_SQL}""",
            (batch_id,),
        ).fetchall()

    def _build_npc_snapshot(self, npc: dict[str, Any], batch_id: str, generated_at: str) -> dict[str, Any]:
        """构建可持久化的英灵快照。"""

        return {
            "npc_id": int(npc["npc_id"]),
            "name": str(npc["name"]),
            "tier": str(npc["tier"]),
            "level": int(npc["level"]),
            "hp": int(npc["hp"]),
            "max_hp": int(npc["max_hp"]),
            "attack": int(npc["attack"]),
            "defense": int(npc["defense"]),
            "kind": str(npc["kind"]),
            "batch_id": str(batch_id),
            "generated_at": str(generated_at),
        }

    def _format_actions_brief(self, actions: list[dict[str, Any]], monster_name: str) -> str:
        """把战斗 actions 格式化成逐次出手的文本摘要。"""

        if not actions:
            return "无行动记录"
        lines: list[str] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            round_no = int(action.get("round", 0))
            actor = str(action.get("actor") or "")
            if actor == "player":
                skill_name = str(action.get("skill_name") or "")
                attack = f"技能「{skill_name}」" if action.get("skill_used") else "普通攻击"
                damage = int(action.get("player_total_damage", action.get("damage", 0)))
                combo_damage = int(action.get("combo_damage", 0))
                life_steal = int(action.get("life_steal", 0))
                parts = [f"第 {round_no} 次：我方{attack}，造成 {damage} 伤害"]
                if combo_damage > 0:
                    parts.append(f"连击 {combo_damage}")
                if life_steal > 0:
                    parts.append(f"吸血 +{life_steal}")
                lines.append("，".join(parts))
            else:
                skill_name = str(action.get("monster_skill_name") or action.get("boss_skill_name") or "")
                attack = f"技能「{skill_name}」" if action.get("monster_skill_used") or action.get("boss_skill_used") else "普通攻击"
                damage = int(action.get("monster_damage", 0))
                if action.get("dodged"):
                    lines.append(f"第 {round_no} 次：{monster_name}{attack}，被我方避开")
                else:
                    lines.append(f"第 {round_no} 次：{monster_name}{attack}，造成 {damage} 伤害")
        return "\n".join(lines) if lines else "无行动记录"

    def _format_recover_items(self, items: list[dict[str, Any]]) -> str:
        """格式化恢复道具明细。"""

        parts = []
        for item in items:
            name = str(item.get("item_name") or item.get("item_id") or "").strip()
            quantity = max(1, int(item.get("quantity") or 1))
            if name:
                parts.append(f"{name}×{quantity}")
        return "、".join(parts) if parts else "无"

    def _decode_recover_items(self, raw: Any) -> list[dict[str, Any]]:
        """把 JSON 化的恢复道具字段还原成明细。"""

        text = str(raw or "").strip()
        if not text:
            return []

        payload: Any = None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

        def normalize_item(item_id: str, item_name: str, quantity: Any) -> dict[str, Any]:
            return {
                "item_id": item_id,
                "item_name": item_name,
                "quantity": max(1, int(quantity or 1)),
            }

        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                normalized: list[dict[str, Any]] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("item_id") or item.get("id") or "").strip()
                    if not item_id:
                        continue
                    item_name = str(item.get("item_name") or NPC_RECOVER_ITEM_LABELS.get(item_id, item_id)).strip()
                    normalized.append(normalize_item(item_id, item_name, item.get("quantity")))
                if normalized:
                    return normalized

        if isinstance(payload, list):
            normalized = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("item_id") or item.get("id") or "").strip()
                if not item_id:
                    continue
                item_name = str(item.get("item_name") or NPC_RECOVER_ITEM_LABELS.get(item_id, item_id)).strip()
                normalized.append(normalize_item(item_id, item_name, item.get("quantity")))
            if normalized:
                return normalized

        legacy_items = []
        for item_id in text.split(","):
            item_id = item_id.strip()
            if not item_id:
                continue
            legacy_items.append(
                {
                    "item_id": item_id,
                    "item_name": NPC_RECOVER_ITEM_LABELS.get(item_id, item_id),
                    "quantity": 1,
                }
            )
        return legacy_items

    def _read_npc_snapshot(self, conn, npc_id: int, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        """读取挑战记录中的 NPC 快照，兼容旧记录。"""

        candidate = fallback or {}
        raw = str(_row_value(candidate, "npc_snapshot", "") or "").strip()
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload:
                return payload

        row = conn.execute(
            "SELECT npc_id, name, tier, level, hp, max_hp, attack, defense, kind, batch_id, generated_at FROM hall_of_heroes_npcs WHERE npc_id = ?",
            (npc_id,),
        ).fetchone()
        if row:
            return dict(row)

        return {
            "npc_id": int(npc_id),
            "name": f"英灵#{npc_id}",
            "tier": "",
            "level": 0,
            "hp": 0,
            "max_hp": 0,
            "attack": 0,
            "defense": 0,
            "kind": "",
            "batch_id": "",
            "generated_at": "",
        }

    def _resolve_npc_by_name_seq(
        self,
        npcs: list[Any],
        npc_name: str,
        npc_seq: int,
    ) -> dict[str, Any] | sqlite3.Row | None:
        """按英灵名称 + 序号定位当前批次目标 NPC。"""

        target_name = str(npc_name).strip()
        target_seq = max(1, int(npc_seq))
        matched = 0
        for npc in npcs:
            if str(_row_value(npc, "name", "")).strip() != target_name:
                continue
            matched += 1
            if matched == target_seq:
                return npc
        return None

    def refresh_npcs(self) -> str:
        """整点刷新NPC的主方法（由定时任务调用）。"""
        from ..sql import db

        batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with db.transaction() as conn:
            # 生态快照
            snapshot = self._world_snapshot_conn(conn)
            # 等级范围
            low, high, _median = self._random_level_conn(conn)
            # 当前批次清理：旧英灵全部移除，历史由挑战快照保留
            conn.execute("DELETE FROM hall_of_heroes_npcs")
            # 生成新一批NPC
            for _ in range(HALL_NPC_COUNT):
                level = self._random_level(low, high)
                tier = self._random_tier()
                npc = self._generate_npc(level, tier, snapshot)
                conn.execute(
                    """INSERT INTO hall_of_heroes_npcs
                       (name, tier, level, hp, max_hp, attack, defense, kind,
                        defeated, batch_id, generated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (npc["name"], npc["tier"], npc["level"],
                     npc["hp"], npc["max_hp"], npc["attack"], npc["defense"],
                     npc["kind"], batch_id, now_str),
                )
            # 记录批次
            conn.execute(
                "INSERT INTO hall_of_heroes_batches (batch_id, generated_at, npc_count) VALUES (?, ?, ?)",
                (batch_id, now_str, HALL_NPC_COUNT),
            )

        return f"英灵殿已刷新，本批 {HALL_NPC_COUNT} 名英灵已降临。（批次 {batch_id}）"

    def overview(self, client_id: str) -> str:
        """返回英灵殿概览。"""
        from ..sql import db

        with db.transaction() as conn:
            _, error = self.require_player(client_id)
            if error:
                return error

            # 当前批次信息
            batch_row = conn.execute(
                "SELECT batch_id, npc_count FROM hall_of_heroes_batches ORDER BY batch_id DESC LIMIT 1"
            ).fetchone()
            if not batch_row:
                return "英灵殿暂无英灵降临，请等待整点刷新。"

            batch_id = batch_row["batch_id"]
            npc_count = batch_row["npc_count"]

            # 剩余可挑战数
            remaining = conn.execute(
                "SELECT COUNT(*) AS cnt FROM hall_of_heroes_npcs WHERE batch_id = ? AND defeated = 0",
                (batch_id,),
            ).fetchone()["cnt"]

            lines = [
                "══ 英灵殿 ══",
                f"当前批次：{batch_id}",
                f"本批英灵：{npc_count} 名",
                f"剩余可挑战：{remaining} 名",
                "",
                "规则说明：",
                "· 每小时整点刷新 10 名英灵（精英NPC）",
                "· 每名英灵每位玩家只能挑战 1 次",
                "· 胜利可获得镇渊积分、恢复药品和经验奖励",
                "· 战败后精神归零，需休息恢复",
                "· 英灵在稀有度 UR/SSSR/SSR/SR/S/A/B/R 中随机",
                "",
                "可用命令：",
                "· 英灵殿列表 / 英灵 — 查看当前英灵列表",
                "· 挑战英灵 名字 序号 — 挑战指定英灵",
                "· 英灵殿记录 — 查看挑战记录",
            ]
            return "\n".join(lines)

    def npc_list(self, client_id: str) -> str:
        """展示当前批次所有NPC列表。"""
        from ..sql import db

        with db.transaction() as conn:
            _, error = self.require_player(client_id)
            if error:
                return error

            batch_row = conn.execute(
                "SELECT batch_id FROM hall_of_heroes_batches ORDER BY batch_id DESC LIMIT 1"
            ).fetchone()
            if not batch_row:
                return "英灵殿暂无英灵降临，请等待整点刷新。"

            batch_id = batch_row["batch_id"]
            npcs = self._current_batch_npcs_conn(conn, batch_id)
            if not npcs:
                return "英灵殿暂无英灵降临，请等待整点刷新。"

            tier_icons = {
                "UR": "★UR★", "SSSR": "◆SSSR", "SSR": "◇SSR", "SR": "◎SR",
                "S": "●S", "A": "○A", "B": "△B", "R": "▽R",
            }

            lines = [f"══ 英灵殿 · 当前英灵（批次 {batch_id}） ══", ""]
            name_counts: dict[str, int] = {}
            for npc in npcs:
                icon = tier_icons.get(npc["tier"], npc["tier"])
                name = str(npc["name"])
                name_counts[name] = name_counts.get(name, 0) + 1
                name_seq = name_counts[name]
                npc_label = f"{name}·{name_seq}"
                if npc["defeated"]:
                    display_name = npc_label
                else:
                    display_name = _inline_command_link(npc_label, f"挑战英灵 {name} {name_seq}")
                defeated_mark = " （已陨落）" if npc["defeated"] else ""
                lines.append(
                    f"{icon} {display_name}{defeated_mark}  Lv.{npc['level']}  {npc['kind']}族"
                )
                lines.append(
                    f"   血气：{npc['hp']}/{npc['max_hp']}  攻击：{npc['attack']}  防御：{npc['defense']}"
                )
                lines.append("")

            lines.append(f"共 {len(npcs)} 名英灵，发送「挑战英灵 名字 序号」即可挑战。")
            return "\n".join(lines)

    def challenge(self, client_id: str, message: str) -> str:
        """处理挑战英灵命令。"""
        from ..sql import db
        from ..combat_core import service as combat_service
        from ..镇渊诛邪.service import service as zhenyuan_service
        from ..format_text import T

        raw_message = message.strip()
        for prefix in ("挑战英灵", "挑戰英靈"):
            if raw_message.startswith(prefix):
                raw_message = raw_message[len(prefix):].strip()
                break

        if not raw_message:
            return T.hint("请指定要挑战的英灵名称和序号。", "发送：挑战英灵 名字 序号")

        parts = raw_message.split()
        npc_seq = 1
        if len(parts) >= 2 and parts[-1].isdigit():
            npc_seq = max(1, int(parts[-1]))
            npc_name = " ".join(parts[:-1]).strip()
        else:
            npc_name = raw_message

        if not npc_name:
            return T.hint("请指定要挑战的英灵名称和序号。", "发送：挑战英灵 名字 序号")

        with db.transaction() as conn:
            player, error = self.require_player(client_id)
            if error:
                return error

            if str(player["status"]) != "空闲":
                if str(player["status"]) == "探险中":
                    return T.hint(
                        "本体正在探险，不能挑战英灵。",
                        "先发送：探险状态，结束后再发送：挑战英灵",
                    )
                return T.hint(
                    f"当前状态为 {player['status']}，不能挑战英灵。",
                    "先结束当前状态再挑战。",
                )

            if int(player["hp"]) <= 0:
                return T.hint("你的血气已耗尽，无法挑战英灵。", "请先休息恢复血气。")

            batch_row = conn.execute(
                "SELECT batch_id FROM hall_of_heroes_batches ORDER BY batch_id DESC LIMIT 1"
            ).fetchone()
            if not batch_row:
                return "英灵殿暂无英灵降临，请等待整点刷新。"

            batch_id = batch_row["batch_id"]
            npcs = self._current_batch_npcs_conn(conn, batch_id)
            npc = self._resolve_npc_by_name_seq(npcs, npc_name, npc_seq)
            challenge_label = f"{npc_name}·{npc_seq}"

            if not npc:
                same_name_count = sum(1 for item in npcs if str(_row_value(item, "name", "")).strip() == npc_name)
                if same_name_count:
                    return T.hint(
                        f"英灵「{challenge_label}」不存在，请检查名称或序号是否正确。",
                        "发送：英灵殿列表 查看当前英灵，并直接点击英灵名称发起挑战。",
                    )
                return T.hint(
                    f"英灵「{challenge_label}」不存在，请检查名称是否正确。",
                    "发送：英灵殿列表 查看可挑战的英灵。",
                )

            already = conn.execute(
                "SELECT 1 FROM hall_of_heroes_challenges WHERE npc_id = ? AND client_id = ?",
                (npc["npc_id"], client_id),
            ).fetchone()
            if already:
                return T.hint(f"你已经挑战过英灵「{challenge_label}」了，每名英灵只能挑战一次。")

            monster = {
                "name": npc["name"],
                "type": npc["kind"],
                "hp": npc["hp"],
                "max_hp": npc["max_hp"],
                "attack": npc["attack"],
                "defense": npc["defense"],
                "level": npc["level"],
                "boss_panel": True,
            }

            result = combat_service.fight_monster(client_id, monster)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_summary = f"英灵挑战：{challenge_label}｜{'胜利' if result['win'] else '战败'}｜最高伤害 {int(result.get('highest_damage', 0))}｜行动 {len(result.get('actions', []))} 次"
            conn.execute(
                "INSERT INTO combat_logs (client_id, target, summary, created_at) VALUES (?, ?, ?, ?)",
                (client_id, challenge_label, log_summary, now_str),
            )

            win = result["win"]
            hp_left = max(0, int(result["hp_left"]))
            mp_left = max(0, int(result["mp_left"]))
            actions = result.get("actions", [])
            total_damage = max(0, int(npc["max_hp"]) - max(0, int(result.get("monster_hp_left", 0))))
            highest_damage = int(result.get("highest_damage", 0))
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if win:
                conn.execute(
                    "UPDATE hall_of_heroes_npcs SET defeated = 1, defeated_at = ? WHERE npc_id = ?",
                    (now_str, npc["npc_id"]),
                )

                points = NPC_TIER_POINTS[npc["tier"]]
                zhenyuan_service.grant_npc_points_conn(conn, client_id, points, source_name=npc["name"])

                recover_items: list[dict[str, Any]] = []
                quantity = 1
                for (lo, hi), (hp_item, mp_item) in NPC_RECOVER_ITEMS.items():
                    if lo <= int(npc["level"]) <= hi:
                        quantity = 2 if lo >= 81 else 1
                        self.add_ring_conn(conn, client_id, hp_item, quantity)
                        self.add_ring_conn(conn, client_id, mp_item, quantity)
                        recover_items.append(
                            {
                                "item_id": hp_item,
                                "item_name": NPC_RECOVER_ITEM_LABELS.get(hp_item, hp_item),
                                "quantity": quantity,
                            }
                        )
                        recover_items.append(
                            {
                                "item_id": mp_item,
                                "item_name": NPC_RECOVER_ITEM_LABELS.get(mp_item, mp_item),
                                "quantity": quantity,
                            }
                        )
                        break

                exp_reward = max(1, int(exp_need(int(player["level"])) * HALL_EXP_RATIO))
                self.add_exp_conn(conn, client_id, exp_reward)

                conn.execute(
                    "UPDATE players SET hp = ?, mp = ? WHERE client_id = ?",
                    (hp_left, mp_left, client_id),
                )

                npc_snapshot = self._build_npc_snapshot(npc, batch_id, now_str)
                recover_payload = {"items": recover_items}
                conn.execute(
                    """INSERT INTO hall_of_heroes_challenges
                       (npc_id, client_id, win, damage_dealt, hp_left, mp_left,
                        zhenyuan_points, exp_gained, recover_item_id, recover_quantity,
                        npc_snapshot, actions, challenged_at)
                       VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        npc["npc_id"],
                        client_id,
                        total_damage,
                        hp_left,
                        mp_left,
                        points,
                        exp_reward,
                        json.dumps(recover_payload, ensure_ascii=False),
                        sum(item["quantity"] for item in recover_items),
                        json.dumps(npc_snapshot, ensure_ascii=False),
                        json.dumps(actions, ensure_ascii=False),
                        now_str,
                    ),
                )
                challenge_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                recover_text = self._format_recover_items(recover_items)
                log_link = battle_log_markdown("英灵殿挑战", "hall", challenge_id, detail=battle_wants_detail(player))
                lines = [
                    f"⚔ 挑战英灵「{challenge_label}」—— 胜利！",
                    "━━━━━━━━━━━━━━━━",
                    f"最高伤害：{highest_damage}",
                    f"剩余血气：{hp_left}",
                    f"剩余精神：{mp_left}",
                    "━━━━━━━━━━━━━━━━",
                    f"获得镇渊积分：+{points}",
                    f"获得恢复药品：{recover_text}",
                    f"获得经验：+{exp_reward}",
                    "━━━━━━━━━━━━━━━━",
                    f"> 战斗日志：{log_link}",
                ]
                return combat_markdown_reply("\n".join(lines))

            conn.execute(
                "UPDATE players SET hp = ?, mp = 0 WHERE client_id = ?",
                (hp_left, client_id),
            )

            npc_snapshot = self._build_npc_snapshot(npc, batch_id, now_str)
            conn.execute(
                """INSERT INTO hall_of_heroes_challenges
                   (npc_id, client_id, win, damage_dealt, hp_left, mp_left,
                    zhenyuan_points, exp_gained, recover_item_id, recover_quantity,
                    npc_snapshot, actions, challenged_at)
                   VALUES (?, ?, 0, ?, ?, ?, 0, 0, ?, 0, ?, ?, ?)""",
                (
                    npc["npc_id"],
                    client_id,
                    total_damage,
                    hp_left,
                    0,
                    json.dumps({"items": []}, ensure_ascii=False),
                    json.dumps(npc_snapshot, ensure_ascii=False),
                    json.dumps(actions, ensure_ascii=False),
                    now_str,
                ),
            )
            challenge_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            log_link = battle_log_markdown("英灵殿挑战", "hall", challenge_id, detail=battle_wants_detail(player))
            lines = [
                f"💀 挑战英灵「{challenge_label}」—— 战败！",
                "━━━━━━━━━━━━━━━━",
                f"最高伤害：{highest_damage}",
                f"剩余血气：{hp_left}",
                "精神归零，需要休息恢复。",
                "━━━━━━━━━━━━━━━━",
                f"> 战斗日志：{log_link}",
                "━━━━━━━━━━━━━━━━",
                "提示：先恢复精神再挑战，或选择更低稀有度的英灵。",
            ]
            return combat_markdown_reply("\n".join(lines))

    def records(self, client_id: str) -> str:
        """展示玩家的英灵殿挑战记录。"""
        from ..sql import db

        with db.transaction() as conn:
            _, error = self.require_player(client_id)
            if error:
                return error

            rows = conn.execute(
                """SELECT c.*
                   FROM hall_of_heroes_challenges c
                   WHERE c.client_id = ?
                   ORDER BY c.challenged_at DESC
                   LIMIT 20""",
                (client_id,),
            ).fetchall()

            if not rows:
                return "你还没有挑战过英灵。发送「英灵殿列表」查看可挑战的英灵。"

            lines = ["══ 英灵殿 · 挑战记录 ══", ""]
            for r in rows:
                npc_snapshot = self._read_npc_snapshot(conn, int(r["npc_id"]), r)
                npc_name = str(npc_snapshot.get("name") or f"英灵#{r['npc_id']}")
                tier_mark = f"[{npc_snapshot.get('tier')}]" if npc_snapshot.get("tier") else ""
                batch_id = str(npc_snapshot.get("batch_id") or "")
                snapshot_note = f" 批次：{batch_id}" if batch_id else ""
                win_label = "✓胜利" if r["win"] else "✗战败"
                lines.append(
                    f"{win_label} {tier_mark} {npc_name}{snapshot_note}  "
                    f"伤害：{r['damage_dealt']}  积分：+{r['zhenyuan_points']}  "
                    f"经验：+{r['exp_gained']}"
                )
                lines.append(f"   挑战时间：{r['challenged_at']}")
                recover_items = self._decode_recover_items(r["recover_item_id"])
                if recover_items:
                    lines.append(f"   获得药品：{self._format_recover_items(recover_items)}")
                lines.append("")

            return "\n".join(lines)


service = HallOfHeroesService(db)
