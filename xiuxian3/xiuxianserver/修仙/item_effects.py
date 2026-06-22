"""可使用物品效果。

背包和纳戒都会用到“使用物品”，所以效果结算放在根目录公共模块。
"""

from __future__ import annotations

import sqlite3

from .common import CoreService, load_json, money, random
from .format_text import T
from .sql import db


class ItemEffectService(CoreService):
    """结算经验、源石、血气、精神和洗髓这几类使用效果。"""

    def apply(self, client_id: str, item_def: dict, source: str) -> str:
        """使用一个物品，并把效果写回玩家。"""

        with self.db.transaction() as conn:
            return self.apply_conn(conn, client_id, item_def, source)

    def apply_conn(self, conn: sqlite3.Connection, client_id: str, item_def: dict, source: str) -> str:
        """在当前事务里结算物品效果。"""

        return self.apply_many_conn(conn, client_id, item_def, source, 1)

    def apply_many_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        item_def: dict,
        source: str,
        quantity: int,
    ) -> str:
        """在当前事务里批量结算物品效果。"""

        if quantity <= 0:
            return T.hint("使用数量必须大于 0。", "发送：使用 物品名 数量，例如：使用 福袋 5")

        all_texts: list[str] = []
        for _ in range(quantity):
            texts = self._apply_one_conn(conn, client_id, item_def)
            if isinstance(texts, str):
                conn.rollback()
                return texts
            all_texts.extend(texts)

        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '使用物品', ?, datetime('now', 'localtime'))",
            (client_id, f"{source}:{item_def['name']} x{quantity}"),
        )
        if not all_texts:
            conn.rollback()
            return T.hint(f"{item_def['name']} 暂无可生效效果。", "发送：查看修仙物品 物品名 查看用途，或换一个恢复类物品使用。")

        suffix = f" x{quantity}" if quantity > 1 else ""
        return f"使用 {item_def['name']}{suffix} 成功：" + "，".join(self._summarize_texts(all_texts))

    def _apply_one_conn(self, conn: sqlite3.Connection, client_id: str, item_def: dict) -> list[str] | str:
        """结算单个物品，返回效果文本或错误提示。"""

        player = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
        if not player:
            return T.hint("你还没有创建用户。", "发送：创建用户 名称，例如：创建用户 青衫客<指南><探险><修仙帮助>")

        effect = load_json(item_def["effect"], {})
        texts: list[str] = []
        self._apply_exp_conn(conn, client_id, effect, texts)
        self._apply_stones_conn(conn, client_id, effect, int(player["level"]), texts)
        self._apply_recovery_conn(conn, client_id, effect, player, texts)
        wash_error = self._apply_wash_conn(conn, client_id, effect, texts)
        if wash_error:
            return wash_error

        return texts

    def _apply_exp_conn(self, conn: sqlite3.Connection, client_id: str, effect: dict, texts: list[str]) -> None:
        """结算经验类效果。"""

        exp_delta = int(effect.get("exp_delta") or 0)
        if effect.get("random_exp_min") is not None:
            exp_delta += random.randint(int(effect["random_exp_min"]), int(effect["random_exp_max"]))
        if exp_delta:
            old, new = self.add_exp_conn(conn, client_id, exp_delta)
            texts.append(f"经验+{exp_delta}")
            if new > old:
                texts.append(f"等级提升到 {new}")

    def _apply_stones_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        effect: dict,
        player_level: int,
        texts: list[str],
    ) -> None:
        """结算源石类效果。"""

        stones_delta = int(effect.get("source_stones_delta") or 0)
        if effect.get("random_stones_segments"):
            stones_delta += self._random_stones_by_level(effect["random_stones_segments"], player_level)
        if effect.get("random_stones_min") is not None:
            stones_delta += random.randint(int(effect["random_stones_min"]), int(effect["random_stones_max"]))
        if stones_delta:
            conn.execute(
                "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                (stones_delta, client_id),
            )
            texts.append(f"源石+{money(stones_delta)}")

    def _apply_recovery_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        effect: dict,
        player: sqlite3.Row,
        texts: list[str],
    ) -> None:
        """结算血气和精神恢复。"""

        hp_delta = int(effect.get("hp_delta") or 0)
        mp_delta = int(effect.get("mp_delta") or 0)
        hp_ratio = float(effect.get("hp_ratio") or 0)
        mp_ratio = float(effect.get("mp_ratio") or 0)
        if hp_delta or mp_delta or hp_ratio or mp_ratio:
            player = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone() or player
            hp_add = hp_delta + int(player["max_hp"] * hp_ratio)
            mp_add = mp_delta + int(player["max_mp"] * mp_ratio)
            new_hp = min(player["max_hp"], player["hp"] + hp_add)
            new_mp = min(player["max_mp"], player["mp"] + mp_add)
            conn.execute(
                "UPDATE players SET hp = ?, mp = ? WHERE client_id = ?",
                (new_hp, new_mp, client_id),
            )
            if hp_add:
                texts.append(f"血气+{hp_add}")
            if mp_add:
                texts.append(f"精神+{mp_add}")

    def _apply_wash_conn(self, conn: sqlite3.Connection, client_id: str, effect: dict, texts: list[str]) -> str:
        """结算洗髓液体质变化。"""

        if effect.get("wash_physique"):
            ok, text = self._wash_physique_conn(conn, client_id)
            if not ok:
                return T.hint(text, "请先检查体质库配置，再重新使用洗髓液。")
            texts.append(text)
        return ""

    def _wash_physique_conn(self, conn: sqlite3.Connection, client_id: str) -> tuple[bool, str]:
        """使用洗髓液重置体质。

        规则很直接：大概率从更高体质里抽，小概率从更低体质里抽。
        抽完后同步写入 physique_id 和 physique_value，再重算玩家面板数值。
        """
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT physique_id, name, grade, kind, level, physique_value, desc
                FROM physique_defs
                ORDER BY level, physique_value, name
                """
            ).fetchall()
        ]
        if not rows:
            return False, "体质库为空，暂时无法洗髓。"

        player = conn.execute(
            "SELECT physique_id, physique_value FROM players WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if not player:
            return False, "玩家不存在。"

        current = self._find_physique(rows, player["physique_id"]) or rows[0]
        target = self._choose_wash_target(rows, current)
        old_value = int(current["physique_value"])
        new_value = int(target["physique_value"])

        conn.execute(
            "UPDATE players SET physique_id = ?, physique_value = ? WHERE client_id = ?",
            (target["physique_id"], new_value, client_id),
        )
        self.recalc_player_conn(conn, client_id)

        if new_value > old_value:
            trend = "提升"
        elif new_value < old_value:
            trend = "回落"
        else:
            trend = "稳定"

        delta = new_value - old_value
        return True, (
            f"洗髓{trend}："
            f"{current['name']}[{current['grade']}/{current['kind']}] → "
            f"{target['name']}[{target['grade']}/{target['kind']}]，体质{delta:+d}"
        )

    @staticmethod
    def _find_physique(rows: list[dict], physique_id: str) -> dict | None:
        """按 id 找体质定义。"""

        for row in rows:
            if row["physique_id"] == physique_id:
                return row
        return None

    @staticmethod
    def _choose_wash_target(rows: list[dict], current: dict) -> dict:
        """按洗髓概率选择新体质。"""

        current_level = int(current["level"])
        max_level = max(int(row["level"]) for row in rows)
        roll = random.random()
        up_rate, same_rate = ItemEffectService._wash_rates(current_level)

        if roll < up_rate:
            pool = [
                row
                for row in rows
                if current_level < int(row["level"]) <= min(max_level, current_level + 2)
            ]
            if pool:
                return random.choice(pool)
            return random.choice([row for row in rows if int(row["level"]) == max_level])

        if roll < up_rate + same_rate:
            pool = [row for row in rows if int(row["level"]) == current_level]
            return random.choice(pool or rows)

        pool = [
            row
            for row in rows
            if max(0, current_level - 2) <= int(row["level"]) < current_level
        ]
        return random.choice(pool or [current])

    @staticmethod
    def _wash_rates(current_level: int) -> tuple[float, float]:
        """返回洗髓的提升率和稳定率。

        三年服不能让体质太快毕业，所以越高阶越难提升。
        剩下的概率就是回落率：低阶 10%，中阶 15%-20%，高阶 25%-30%。
        """

        if current_level <= 3:
            return 0.35, 0.55
        if current_level <= 6:
            return 0.22, 0.63
        if current_level <= 9:
            return 0.14, 0.66
        if current_level <= 12:
            return 0.07, 0.68
        return 0.0, 0.70

    @staticmethod
    def _random_stones_by_level(segments: object, level: int) -> int:
        """按玩家等级段随机福袋源石。"""

        if not isinstance(segments, list):
            return 0

        player_level = int(level)
        fallback: dict | None = None
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            fallback = segment
            min_level = int(segment.get("min_level", 1))
            max_level = int(segment.get("max_level", 100))
            if min_level <= player_level <= max_level:
                return random.randint(int(segment.get("min", 0)), int(segment.get("max", 0)))

        if fallback:
            return random.randint(int(fallback.get("min", 0)), int(fallback.get("max", 0)))
        return 0

    @staticmethod
    def _summarize_texts(texts: list[str]) -> list[str]:
        """把批量使用的同类数值合并，保留洗髓等描述性文本。"""

        totals = {"经验": 0, "源石": 0, "血气": 0, "精神": 0}
        level_text = ""
        others: list[str] = []

        for text in texts:
            if text.startswith("经验+"):
                totals["经验"] += int(text.removeprefix("经验+").replace(",", ""))
            elif text.startswith("源石+"):
                totals["源石"] += int(text.removeprefix("源石+").replace(",", ""))
            elif text.startswith("血气+"):
                totals["血气"] += int(text.removeprefix("血气+").replace(",", ""))
            elif text.startswith("精神+"):
                totals["精神"] += int(text.removeprefix("精神+").replace(",", ""))
            elif text.startswith("等级提升到 "):
                level_text = text
            else:
                others.append(text)

        result: list[str] = []
        if totals["经验"]:
            result.append(f"经验+{totals['经验']}")
        if level_text:
            result.append(level_text)
        if totals["源石"]:
            result.append(f"源石+{money(totals['源石'])}")
        if totals["血气"]:
            result.append(f"血气+{totals['血气']}")
        if totals["精神"]:
            result.append(f"精神+{totals['精神']}")
        result.extend(others)
        return result


service = ItemEffectService(db)

__all__ = ["ItemEffectService", "service"]
