"""保险箱组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    CoreService,
    computed_weapon_attack,
    computed_weapon_enchant_slots,
    load_json,
    parse_name_level,
    parse_name_quantity_optional,
    parse_weapon_ref,
    split_words,
    ts,
    weapon_id_label,
    weapon_label_name,
)
from ..sql import db

VAULT_SLOT_LIMIT = 18
VAULT_OWNER_PREFIX = "__vault__:"


class InsuranceBoxService(CoreService):
    """冻结保存玩家物品，避免出售、回收或上架误操作。"""

    def list_items(self, client_id: str) -> str:
        """查看保险箱。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        used = self._used_slots(client_id)
        item_rows = self._vault_item_rows(client_id)
        weapon_rows = self._vault_weapon_rows(client_id)
        if not item_rows and not weapon_rows:
            return T.hint(
                f"保险箱为空，容量 {used}/{VAULT_SLOT_LIMIT}。",
                "发送：存入保险箱 物品名 数量，或发送：存入保险箱 武器#ID。",
            )

        panel = T.panel()
        panel.section("保险箱")
        panel.line(f"容量：**{used}/{VAULT_SLOT_LIMIT}**")
        panel.line("箱内物品已冻结，取出前不能使用、出售、回收、上架、升级或附魔。")
        if item_rows:
            panel.hr()
            panel.section("物品")
            for row in item_rows:
                level = f" {row['level']}级" if int(row.get("level") or 0) > 0 else ""
                source = self._source_text(str(row["item_type"]))
                panel.line(f"{source}｜{row['name']}{level} x{row['quantity']}｜{row['category']}")
        if weapon_rows:
            panel.hr()
            panel.section("武器")
            for weapon in weapon_rows:
                panel.line(self._weapon_text(weapon))
        return panel.render() + "<背包><纳戒><武器>"

    def deposit(self, client_id: str, message: str) -> str:
        """存入保险箱。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        text = message.strip()
        if not text:
            return T.hint("存入格式不正确。", "发送：存入保险箱 物品名 数量，或发送：存入保险箱 武器#ID。")
        weapon_id = self._parse_weapon_message(text)
        if weapon_id > 0:
            return self._deposit_weapon(client_id, weapon_id)
        return self._deposit_item(client_id, text)

    def withdraw(self, client_id: str, message: str) -> str:
        """从保险箱取出。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        text = message.strip()
        if not text:
            return T.hint("取出格式不正确。", "发送：取出保险箱 物品名 数量，或发送：取出保险箱 武器#ID。")
        weapon_id = self._parse_weapon_message(text)
        if weapon_id > 0:
            return self._withdraw_weapon(client_id, weapon_id)
        return self._withdraw_item(client_id, text)

    def _deposit_item(self, client_id: str, text: str) -> str:
        """把普通库存物品移入保险箱。"""

        item_text, quantity = parse_name_quantity_optional(text)
        item_name, wanted_level = parse_name_level(item_text)
        if quantity <= 0:
            return T.hint("存入数量必须大于 0。", "发送：存入保险箱 物品名 数量，例如：存入保险箱 古妖丹 10")

        with self.db.transaction() as conn:
            item = conn.execute("SELECT * FROM item_defs WHERE name = ?", (item_name,)).fetchone()
            if item:
                row = conn.execute(
                    "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
                    (client_id, item["item_id"]),
                ).fetchone()
                owned = int(row["quantity"]) if row else 0
                if owned >= quantity:
                    if not self._can_add_slot_conn(conn, client_id, "backpack", item["item_id"], 0):
                        return self._slot_full_text()
                    if not self.remove_backpack_conn(conn, client_id, item["item_id"], quantity):
                        return T.hint("背包库存已变化，存入失败。", "发送：背包 确认后再试。<背包>")
                    self._add_vault_item_conn(conn, client_id, "backpack", item["item_id"], 0, quantity)
                    self._log_conn(conn, client_id, "存入保险箱", f"backpack:{item['item_id']} x{quantity}")
                    return f"已存入保险箱：{item['name']} x{quantity}。"

            equipment = conn.execute(
                "SELECT * FROM ring_item_defs WHERE name = ?",
                (item_name,),
            ).fetchone()
            if not equipment:
                if item:
                    return T.hint(f"背包里 {item['name']} 数量不足。", "发送：背包 确认库存，或减少存入数量。<背包>")
                return T.hint(f"没有找到物品：{item_name}。", "发送：背包、纳戒 或 武器，复制准确名称或武器 ID。<背包><纳戒><武器>")

            if equipment["category"] == "宝石":
                gem_level, level_error = self.resolve_gem_level_conn(
                    conn,
                    client_id,
                    equipment["ring_item_id"],
                    equipment["name"],
                    wanted_level,
                    "存入保险箱 {name} {level}级 1",
                )
                if level_error:
                    return level_error
                assert gem_level is not None
                row = conn.execute(
                    "SELECT quantity FROM gem_items WHERE client_id = ? AND gem_id = ? AND level = ?",
                    (client_id, equipment["ring_item_id"], gem_level),
                ).fetchone()
                owned = int(row["quantity"]) if row else 0
                if owned < quantity:
                    return T.hint(f"纳戒里 {equipment['name']} {gem_level}级 只有 {owned} 个。", "发送：宝石 查看库存后再存。<宝石>")
                if not self._can_add_slot_conn(conn, client_id, "gem", equipment["ring_item_id"], gem_level):
                    return self._slot_full_text()
                if not self.remove_gem_conn(conn, client_id, equipment["ring_item_id"], gem_level, quantity):
                    return T.hint("宝石库存已变化，存入失败。", "发送：宝石 查看当前库存后再试。<宝石>")
                self._add_vault_item_conn(conn, client_id, "gem", equipment["ring_item_id"], gem_level, quantity)
                self._log_conn(conn, client_id, "存入保险箱", f"gem:{equipment['ring_item_id']} lv{gem_level} x{quantity}")
                return f"已存入保险箱：{equipment['name']} {gem_level}级 x{quantity}。"

            row = conn.execute(
                "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
                (client_id, equipment["ring_item_id"]),
            ).fetchone()
            owned = int(row["quantity"]) if row else 0
            if owned < quantity:
                return T.hint(f"纳戒里 {equipment['name']} 只有 {owned} 个。", "发送：纳戒 确认库存，或减少存入数量。<纳戒>")
            if not self._can_add_slot_conn(conn, client_id, "ring", equipment["ring_item_id"], 0):
                return self._slot_full_text()
            if not self.remove_ring_conn(conn, client_id, equipment["ring_item_id"], quantity):
                return T.hint("纳戒库存已变化，存入失败。", "发送：纳戒 查看当前库存后再试。<纳戒>")
            self._add_vault_item_conn(conn, client_id, "ring", equipment["ring_item_id"], 0, quantity)
            self._log_conn(conn, client_id, "存入保险箱", f"ring:{equipment['ring_item_id']} x{quantity}")
            return f"已存入保险箱：{equipment['name']} x{quantity}。"

    def _withdraw_item(self, client_id: str, text: str) -> str:
        """把保险箱物品取回原库存。"""

        item_text, quantity = parse_name_quantity_optional(text)
        item_name, wanted_level = parse_name_level(item_text)
        if quantity <= 0:
            return T.hint("取出数量必须大于 0。", "发送：取出保险箱 物品名 数量，例如：取出保险箱 古妖丹 10")

        with self.db.transaction() as conn:
            item = conn.execute("SELECT * FROM item_defs WHERE name = ?", (item_name,)).fetchone()
            if item:
                row = self._vault_item_conn(conn, client_id, "backpack", item["item_id"], 0)
                if row:
                    if int(row["quantity"]) < quantity:
                        return T.hint(f"保险箱里 {item['name']} 只有 {row['quantity']} 个。", "发送：保险箱 查看库存后再取。<保险箱>")
                    ok, reason = self.can_add_backpack_conn(conn, client_id, item["item_id"], quantity)
                    if not ok:
                        return T.hint("背包空间不足，暂时无法取出。", reason)
                    if not self._remove_vault_item_conn(conn, client_id, "backpack", item["item_id"], 0, quantity):
                        return T.hint("保险箱库存已变化，取出失败。", "发送：保险箱 查看后再试。<保险箱>")
                    self.add_backpack_conn(conn, client_id, item["item_id"], quantity)
                    self._log_conn(conn, client_id, "取出保险箱", f"backpack:{item['item_id']} x{quantity}")
                    return f"已取出到背包：{item['name']} x{quantity}。"

            equipment = conn.execute(
                "SELECT * FROM ring_item_defs WHERE name = ?",
                (item_name,),
            ).fetchone()
            if not equipment:
                if item:
                    return T.hint(f"保险箱里没有 {item['name']}。", "发送：保险箱 查看库存后再取。<保险箱>")
                return T.hint(f"没有找到物品：{item_name}。", "发送：保险箱 查看准确名称。<保险箱>")

            if equipment["category"] == "宝石":
                gem_level, level_error = self._resolve_vault_gem_level_conn(
                    conn,
                    client_id,
                    equipment["ring_item_id"],
                    equipment["name"],
                    wanted_level,
                )
                if level_error:
                    return level_error
                assert gem_level is not None
                row = self._vault_item_conn(conn, client_id, "gem", equipment["ring_item_id"], gem_level)
                if not row or int(row["quantity"]) < quantity:
                    owned = int(row["quantity"]) if row else 0
                    return T.hint(f"保险箱里 {equipment['name']} {gem_level}级 只有 {owned} 个。", "发送：保险箱 查看库存后再取。<保险箱>")
                if not self._remove_vault_item_conn(conn, client_id, "gem", equipment["ring_item_id"], gem_level, quantity):
                    return T.hint("保险箱库存已变化，取出失败。", "发送：保险箱 查看后再试。<保险箱>")
                self.add_gem_conn(conn, client_id, equipment["ring_item_id"], gem_level, quantity)
                self._log_conn(conn, client_id, "取出保险箱", f"gem:{equipment['ring_item_id']} lv{gem_level} x{quantity}")
                return f"已取出到纳戒：{equipment['name']} {gem_level}级 x{quantity}。"

            row = self._vault_item_conn(conn, client_id, "ring", equipment["ring_item_id"], 0)
            if not row or int(row["quantity"]) < quantity:
                owned = int(row["quantity"]) if row else 0
                return T.hint(f"保险箱里 {equipment['name']} 只有 {owned} 个。", "发送：保险箱 查看库存后再取。<保险箱>")
            if not self._remove_vault_item_conn(conn, client_id, "ring", equipment["ring_item_id"], 0, quantity):
                return T.hint("保险箱库存已变化，取出失败。", "发送：保险箱 查看后再试。<保险箱>")
            self.add_ring_conn(conn, client_id, equipment["ring_item_id"], quantity)
            self._log_conn(conn, client_id, "取出保险箱", f"ring:{equipment['ring_item_id']} x{quantity}")
            return f"已取出到纳戒：{equipment['name']} x{quantity}。"

    def _deposit_weapon(self, client_id: str, weapon_id: int) -> str:
        """把备用武器移入保险箱。"""

        with self.db.transaction() as conn:
            weapon = self._player_weapon_conn(conn, client_id, weapon_id)
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
            if int(weapon["equipped"]):
                return T.hint("已装备武器不能存入保险箱。", "先切换到其他武器，再存入这把备用武器。<武器>")
            count = conn.execute(
                "SELECT COUNT(*) AS total FROM player_weapons WHERE holder_id = ?",
                (client_id,),
            ).fetchone()
            if int(count["total"]) <= 1:
                return T.hint("不能把最后一把武器存入保险箱。", "至少保留一把可用武器，避免无法探险战斗。<武器>")
            if not self._can_add_weapon_slot_conn(conn, client_id, weapon_id):
                return self._slot_full_text()
            conn.execute(
                """
                INSERT INTO vault_weapons (client_id, weapon_id, stored_at)
                VALUES (?, ?, ?)
                ON CONFLICT(weapon_id) DO UPDATE SET
                    client_id = excluded.client_id,
                    stored_at = excluded.stored_at
                """,
                (client_id, weapon_id, ts()),
            )
            conn.execute(
                "UPDATE player_weapons SET holder_id = ?, equipped = 0 WHERE holder_id = ? AND weapon_id = ?",
                (self._vault_holder(client_id), client_id, weapon_id),
            )
            self._log_conn(conn, client_id, "存入保险箱", f"weapon:{weapon_id}")
        return f"已存入保险箱：{weapon_id_label(weapon_id)} {weapon_label_name(dict(weapon))}[{weapon['quality']}]。"

    def _withdraw_weapon(self, client_id: str, weapon_id: int) -> str:
        """把保险箱武器取回武器库。"""

        with self.db.transaction() as conn:
            weapon = self._vault_weapon_conn(conn, client_id, weapon_id)
            if not weapon:
                return T.hint("保险箱里没有这把武器。", "发送：保险箱 查看已存武器 ID。<保险箱>")
            has_weapon = conn.execute(
                "SELECT 1 FROM player_weapons WHERE holder_id = ? LIMIT 1",
                (client_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE player_weapons
                SET holder_id = ?, equipped = ?
                WHERE holder_id = ? AND weapon_id = ?
                """,
                (client_id, 0 if has_weapon else 1, self._vault_holder(client_id), weapon_id),
            )
            conn.execute("DELETE FROM vault_weapons WHERE client_id = ? AND weapon_id = ?", (client_id, weapon_id))
            self._log_conn(conn, client_id, "取出保险箱", f"weapon:{weapon_id}")
        return f"已取出到武器库：{weapon_id_label(weapon_id)} {weapon_label_name(dict(weapon))}[{weapon['quality']}]。"

    def _vault_item_rows(self, client_id: str) -> list[dict]:
        """读取保险箱里的普通物品。"""

        backpack = self.db.fetch_all(
            """
            SELECT v.*, i.name, i.category
            FROM vault_items v
            JOIN item_defs i ON i.item_id = v.item_id
            WHERE v.client_id = ? AND v.item_type = 'backpack' AND v.quantity > 0
            ORDER BY i.category, i.name
            """,
            (client_id,),
        )
        ring = self.db.fetch_all(
            """
            SELECT v.*, e.name, e.category
            FROM vault_items v
            JOIN ring_item_defs e ON e.ring_item_id = v.item_id
            WHERE v.client_id = ? AND v.item_type IN ('ring', 'gem') AND v.quantity > 0
            ORDER BY e.category, e.name, v.level
            """,
            (client_id,),
        )
        return backpack + ring

    def _vault_weapon_rows(self, client_id: str) -> list[dict]:
        """读取保险箱里的武器。"""

        return self.db.fetch_all(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM vault_weapons v
            JOIN player_weapons w ON w.weapon_id = v.weapon_id
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE v.client_id = ? AND w.holder_id = ?
            ORDER BY w.weapon_id
            """,
            (client_id, self._vault_holder(client_id)),
        )

    def _player_weapon_conn(self, conn, client_id: str, weapon_id: int):
        """在事务里读取玩家武器。"""

        return conn.execute(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ? AND w.weapon_id = ?
            """,
            (client_id, weapon_id),
        ).fetchone()

    def _vault_weapon_conn(self, conn, client_id: str, weapon_id: int):
        """在事务里读取保险箱武器。"""

        return conn.execute(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM vault_weapons v
            JOIN player_weapons w ON w.weapon_id = v.weapon_id
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE v.client_id = ? AND v.weapon_id = ? AND w.holder_id = ?
            """,
            (client_id, weapon_id, self._vault_holder(client_id)),
        ).fetchone()

    @staticmethod
    def _parse_weapon_message(text: str) -> int:
        """解析保险箱里的武器写法。"""

        parts = split_words(text)
        if not parts:
            return 0
        if len(parts) == 1:
            token = parts[0]
            if token.startswith(("武器", "#")):
                return parse_weapon_ref(token)
        if len(parts) == 2 and parts[0] in {"武器", "武器ID"}:
            return parse_weapon_ref(parts[1])
        return 0

    def _used_slots(self, client_id: str) -> int:
        """读取保险箱已用格子。"""

        with self.db.transaction() as conn:
            return self._used_slots_conn(conn, client_id)

    @staticmethod
    def _used_slots_conn(conn, client_id: str) -> int:
        """在事务里读取保险箱已用格子。"""

        item_row = conn.execute(
            "SELECT COUNT(*) AS total FROM vault_items WHERE client_id = ? AND quantity > 0",
            (client_id,),
        ).fetchone()
        weapon_row = conn.execute(
            "SELECT COUNT(*) AS total FROM vault_weapons WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return int(item_row["total"] if item_row else 0) + int(weapon_row["total"] if weapon_row else 0)

    def _can_add_slot_conn(self, conn, client_id: str, item_type: str, item_id: str, level: int) -> bool:
        """判断加入物品是否会超过保险箱格子。"""

        if self._vault_item_conn(conn, client_id, item_type, item_id, level):
            return True
        return self._used_slots_conn(conn, client_id) < VAULT_SLOT_LIMIT

    def _can_add_weapon_slot_conn(self, conn, client_id: str, weapon_id: int) -> bool:
        """判断加入武器是否会超过保险箱格子。"""

        row = conn.execute(
            "SELECT 1 FROM vault_weapons WHERE client_id = ? AND weapon_id = ?",
            (client_id, weapon_id),
        ).fetchone()
        if row:
            return True
        return self._used_slots_conn(conn, client_id) < VAULT_SLOT_LIMIT

    @staticmethod
    def _vault_item_conn(conn, client_id: str, item_type: str, item_id: str, level: int):
        """读取保险箱物品行。"""

        return conn.execute(
            """
            SELECT * FROM vault_items
            WHERE client_id = ? AND item_type = ? AND item_id = ? AND level = ?
            """,
            (client_id, item_type, item_id, max(0, int(level))),
        ).fetchone()

    @staticmethod
    def _add_vault_item_conn(conn, client_id: str, item_type: str, item_id: str, level: int, quantity: int) -> None:
        """增加保险箱物品。"""

        now_text = ts()
        conn.execute(
            """
            INSERT INTO vault_items
            (client_id, item_type, item_id, level, quantity, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, item_type, item_id, level)
            DO UPDATE SET
                quantity = quantity + excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (client_id, item_type, item_id, max(0, int(level)), quantity, now_text, now_text),
        )

    @staticmethod
    def _remove_vault_item_conn(conn, client_id: str, item_type: str, item_id: str, level: int, quantity: int) -> bool:
        """扣除保险箱物品。"""

        row = conn.execute(
            """
            SELECT quantity FROM vault_items
            WHERE client_id = ? AND item_type = ? AND item_id = ? AND level = ?
            """,
            (client_id, item_type, item_id, max(0, int(level))),
        ).fetchone()
        if not row or int(row["quantity"]) < quantity:
            return False
        left = int(row["quantity"]) - quantity
        if left:
            conn.execute(
                """
                UPDATE vault_items
                SET quantity = ?, updated_at = ?
                WHERE client_id = ? AND item_type = ? AND item_id = ? AND level = ?
                """,
                (left, ts(), client_id, item_type, item_id, max(0, int(level))),
            )
        else:
            conn.execute(
                """
                DELETE FROM vault_items
                WHERE client_id = ? AND item_type = ? AND item_id = ? AND level = ?
                """,
                (client_id, item_type, item_id, max(0, int(level))),
            )
        return True

    def _resolve_vault_gem_level_conn(
        self,
        conn,
        client_id: str,
        gem_id: str,
        gem_name: str,
        wanted_level: int | None,
    ) -> tuple[int | None, str | None]:
        """确定保险箱里的宝石等级。"""

        if wanted_level is not None:
            return wanted_level, None
        rows = conn.execute(
            """
            SELECT level, quantity FROM vault_items
            WHERE client_id = ? AND item_type = 'gem' AND item_id = ? AND quantity > 0
            ORDER BY level
            """,
            (client_id, gem_id),
        ).fetchall()
        if not rows:
            return 1, None
        if len(rows) == 1:
            return int(rows[0]["level"]), None
        options = "、".join(f"{row['level']}级x{row['quantity']}" for row in rows)
        return None, T.hint(
            f"保险箱里有多种等级的 {gem_name}。",
            f"请写清等级，例如：取出保险箱 {gem_name} {rows[-1]['level']}级 1。现有：{options}",
        )

    @staticmethod
    def _vault_holder(client_id: str) -> str:
        """保险箱托管武器使用的临时 holder_id。"""

        return f"{VAULT_OWNER_PREFIX}{client_id}"

    @staticmethod
    def _source_text(item_type: str) -> str:
        """展示原库存来源。"""

        return {
            "backpack": "背包",
            "ring": "纳戒",
            "gem": "纳戒宝石",
        }.get(item_type, item_type)

    @staticmethod
    def _weapon_text(weapon: dict) -> str:
        """展示保险箱武器。"""

        enchant_ids = load_json(weapon.get("enchant_effects"), [])
        enchants = len(enchant_ids) if isinstance(enchant_ids, list) else 0
        return (
            f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}[{weapon['quality']}] "
            f"等级:{weapon['level']}/{weapon['max_level']} 攻击:{computed_weapon_attack(weapon)} "
            f"附魔:{enchants}/{computed_weapon_enchant_slots(weapon)}"
        )

    @staticmethod
    def _slot_full_text() -> str:
        """保险箱满格提示。"""

        return T.hint(f"保险箱格子不足，最多 {VAULT_SLOT_LIMIT} 格。", "先取出一些物品，再继续存入。<保险箱>")

    @staticmethod
    def _log_conn(conn, client_id: str, action: str, detail: str) -> None:
        """写入保险箱操作日志。"""

        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (client_id, action, detail, ts()),
        )


service = InsuranceBoxService(db)

__all__ = ["InsuranceBoxService", "service"]
