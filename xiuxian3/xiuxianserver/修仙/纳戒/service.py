"""纳戒组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    CoreService,
    computed_weapon_potential_slots,
    fixed_equipment_label,
    load_json,
    parse_name_quantity_optional,
    parse_weapon_ref,
    ring_item_use_hint,
    ts,
    weapon_id_label,
    weapon_label_name,
)
from ..constants import EQUIPMENT_DEFAULT_HOLES, EQUIPMENT_MAX_HOLES, EQUIPMENT_SLOTS
from ..item_effects import service as item_effects
from ..sql import db

HOLE_ITEM_ID = "kaikongqi"


class RingService(CoreService):
    """纳戒库存和专属纳戒物品消耗。"""

    def list_items(self, client_id: str) -> str:
        """查看纳戒。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.ring_rows(client_id)
        if not rows:
            return T.hint("纳戒为空。", "发送：新手礼包 领取初始恢复物，或发送：探险 获取恢复物、宝石和技能书。<探险>")
        panel = T.panel()
        panel.section("纳戒")
        for row in rows:
            level = f" {row['level']}级" if row.get("level") else ""
            panel.line(f"{row['name']}{level} x{row['quantity']}｜{row['category']}")
        return panel.render()

    def use_item(self, client_id: str, item_message: str) -> str:
        """使用纳戒中的恢复类物品。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        item_name, quantity = parse_name_quantity_optional(item_message)
        if quantity <= 0:
            return T.hint("使用数量必须大于 0。", "发送：使用 物品名 数量，例如：使用 福袋 5")
        item = self.ring_item_def_by_name(item_name)
        if not item:
            return T.hint(f"没有找到纳戒物品：{item_name}。", "发送：纳戒 查看已拥有的物品。<纳戒>")
        if item["category"] != "恢复类":
            return T.hint(f"{item['name']} 不能直接使用。", ring_item_use_hint(item))

        with self.db.transaction() as conn:
            if not self.remove_ring_conn(conn, client_id, item["ring_item_id"], quantity):
                return T.hint(f"纳戒里没有足够的 {item['name']} x{quantity}。", "发送：纳戒 确认库存，或继续探险获取。<纳戒><探险>")
            return item_effects.apply_many_conn(conn, client_id, item, "纳戒", quantity)

    def wash(self, client_id: str) -> str:
        """消耗洗髓液洗髓体质。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        item = self.ring_item_def_by_name("洗髓液")
        if not item:
            return T.hint("洗髓液配置不存在。", "请先检查纳戒物品配置。")
        with self.db.transaction() as conn:
            if not self.remove_ring_conn(conn, client_id, item["ring_item_id"], 1):
                return T.hint("纳戒里没有洗髓液。", "洗髓液可从岁时情劫首领或异界虫洞奖励中获得，获得后发送：洗髓<洗髓>")
            return item_effects.apply_conn(conn, client_id, item, "洗髓")

    def temper_weapon(self, client_id: str, message: str) -> str:
        """消耗淬锋丹提升武器等级上限。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.ensure_player_weapon(client_id)
        weapon_id = parse_weapon_ref(message)
        if weapon_id <= 0 and message.strip():
            return T.hint("武器淬锋格式不正确。", "发送：武器淬锋，或发送：武器淬锋 武器ID。<纳戒><武器>")

        with self.db.transaction() as conn:
            if weapon_id <= 0:
                equipped = conn.execute(
                    """
                    SELECT weapon_id
                    FROM player_weapons
                    WHERE holder_id = ? AND equipped = 1
                    ORDER BY weapon_id
                    LIMIT 1
                    """,
                    (client_id,),
                ).fetchone()
                weapon_id = int(equipped["weapon_id"]) if equipped else 0
            weapon = conn.execute(
                """
                SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
                FROM player_weapons AS w
                JOIN weapon_defs AS d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到可淬锋的武器。", "发送：武器 查看自己的武器列表。<武器>")

            item = conn.execute(
                "SELECT * FROM ring_item_defs WHERE ring_item_id = 'cuifengdan'",
            ).fetchone()
            if not item:
                return T.hint("淬锋丹配置不存在。", "请先检查纳戒物品配置。")
            effect = load_json(item["effect"], {})
            delta = max(1, int(effect.get("weapon_max_level_delta") or 1))
            cap = max(1, int(effect.get("weapon_max_level_cap") or 100))
            old_max = int(weapon["max_level"])
            if old_max >= cap:
                return T.hint(
                    f"{weapon_id_label(weapon_id)} {weapon_label_name(weapon)} 已达到上限 {cap}。",
                    "淬锋丹无法继续提升这把武器。",
                    buttons=("纳戒", "武器"),
                )
            if not self.remove_ring_conn(conn, client_id, "cuifengdan", 1):
                return T.hint("纳戒里没有淬锋丹。", "淬锋丹只能从宗门战奖励获得。<宗门战>")

            new_max = min(cap, old_max + delta)
            next_weapon = dict(weapon)
            next_weapon["max_level"] = new_max
            conn.execute(
                "UPDATE player_weapons SET max_level = ? WHERE holder_id = ? AND weapon_id = ?",
                (new_max, client_id, weapon_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '武器淬锋', ?, ?)",
                (client_id, f"weapon_id={weapon_id}, max_level={old_max}->{new_max}", ts()),
            )

        return (
            f"淬锋成功：{weapon_id_label(weapon_id)} {weapon_label_name(weapon)} "
            f"等级上限 {old_max}->{new_max}，潜力附魔栏 {computed_weapon_potential_slots(next_weapon)}。"
        )

    def open_equipment_hole(self, client_id: str, slot: str) -> str:
        """消耗开孔器，为装备增加 1 个孔位。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        slot = slot.strip()
        if slot not in EQUIPMENT_SLOTS:
            return T.hint(f"装备位只能是：{'、'.join(EQUIPMENT_SLOTS)}", "发送：装备 查看已有装备位。<装备>")
        self.db.ensure_fixed_equipment(client_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM fixed_equipment WHERE client_id = ? AND slot = ?",
                (client_id, slot),
            ).fetchone()
            hole_count = int(row["hole_count"]) if row else EQUIPMENT_DEFAULT_HOLES
            if hole_count >= EQUIPMENT_MAX_HOLES:
                return T.hint(f"{slot} 已经达到 {EQUIPMENT_MAX_HOLES} 孔上限。", "可以给其他装备开孔，或继续镶嵌、升级宝石。")
            if not self.remove_ring_conn(conn, client_id, HOLE_ITEM_ID, 1):
                return T.hint("纳戒里没有开孔器。", "开孔器通过岁时情劫首领奖励获得，获得后发送：开孔 装备位。<首领><纳戒>")
            conn.execute(
                """
                UPDATE fixed_equipment
                SET hole_count = hole_count + 1
                WHERE client_id = ? AND slot = ?
                """,
                (client_id, slot),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '装备开孔', ?, ?)",
                (client_id, f"slot={slot}, holes={hole_count + 1}", ts()),
            )
        self.recalc_player(client_id)
        equipment = self._equipment_row(client_id, slot)
        return f"开孔成功：{fixed_equipment_label(equipment) if equipment else slot} 当前孔位 {hole_count + 1}/{EQUIPMENT_MAX_HOLES}。"

    def _equipment_row(self, client_id: str, slot: str) -> dict | None:
        """读取某个装备位。"""

        self.db.ensure_fixed_equipment(client_id)
        return self.db.fetch_one(
            "SELECT * FROM fixed_equipment WHERE client_id = ? AND slot = ?",
            (client_id, slot),
        )


service = RingService(db)

__all__ = ["RingService", "service"]
