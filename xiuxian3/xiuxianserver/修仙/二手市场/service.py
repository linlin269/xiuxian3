"""二手市场组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    CoreService,
    computed_weapon_attack,
    computed_weapon_enchant_slots,
    load_json,
    money,
    parse_name_level,
    parse_weapon_ref,
    split_words,
    to_int,
    ts,
    weapon_id_label,
    weapon_label_name,
)
from ..constants import MARKET_FEE_RATE
from ..sql import db


class SecondHandService(CoreService):
    """玩家之间的一包商品交易。"""

    market_owner_prefix = "__second_hand__:"

    def list_items(self, client_id: str) -> str:
        """查看当前上架。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all(
            """
            SELECT l.*, p.display_name
            FROM second_hand_listings l
            LEFT JOIN players p ON p.client_id = l.seller_id
            ORDER BY l.created_at DESC
            LIMIT 20
            """
        )
        if not rows:
            return T.hint(
                "二手市场暂无上架。",
                "背包/纳戒物品：二手市场上架 名称 数量 总价；宝石可写等级；武器：二手市场上架 武器#ID 总价",
            )
        panel = T.panel()
        panel.section("二手市场")
        for row in rows:
            name = self._item_name(row["item_type"], row["item_id"])
            seller = row["display_name"] or "未知道友"
            quantity = "" if row["item_type"] == "weapon" else f" x{row['quantity']}"
            panel.line(f"{seller}：{name}{quantity}｜总价 **{money(row['total_price'])}**")
        return panel.render()

    def sell(self, client_id: str, message: str) -> str:
        """上架一包商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        weapon_id, weapon_price = self._parse_weapon_listing(parts)
        if weapon_id:
            if weapon_price <= 0:
                return T.hint("武器上架价格必须大于 0。", "发送：二手市场上架 武器#ID 总价，例如：二手市场上架 武器#12 50000")
            return self._sell_weapon(client_id, weapon_id, weapon_price)
        # 异火上架：二手市场上架 异火名 1 总价
        if len(parts) >= 3 and parts[-2] == "1" and parts[-1].isdigit():
            flame_name = " ".join(parts[:-2])
            flame_result = self._try_sell_flame(client_id, flame_name, int(parts[-1]))
            if flame_result is not None:
                return flame_result
        if len(parts) < 3:
            return T.hint(
                "上架格式不正确。",
                "背包/纳戒物品：二手市场上架 名称 数量 总价；宝石可写等级；武器：二手市场上架 武器#ID 总价",
            )
        item_name, wanted_level = parse_name_level(" ".join(parts[:-2]))
        quantity = int(parts[-2]) if parts[-2].isdigit() else 0
        total_price = int(parts[-1]) if parts[-1].isdigit() else 0
        if quantity <= 0 or total_price <= 0:
            return T.hint("数量和总价必须大于 0。", "重新发送：二手市场上架 名称 数量 总价")

        item = self.item_def_by_name(item_name)
        item_type = "backpack"
        item_id = item["item_id"] if item else ""
        if not item:
            item = self.ring_item_def_by_name(item_name)
            item_type = "gem" if item and item["category"] == "宝石" else "ring"
            item_id = item["ring_item_id"] if item else ""
        if not item:
            return T.hint(f"没有找到可上架物品：{item_name}。", "发送：背包、纳戒 或 武器，复制准确名称或武器 ID。")

        with self.db.transaction() as conn:
            exists = conn.execute(
                "SELECT listing_id FROM second_hand_listings WHERE seller_id = ?",
                (client_id,),
            ).fetchone()
            if exists:
                return T.hint("你已经有一包商品在上架。", "先发送：二手市场下架，或等其他玩家购买。<二手市场下架>")
            if item_type == "backpack":
                removed = self.remove_backpack_conn(conn, client_id, item_id, quantity)
                listing_item_id = item_id
                item_label = item["name"]
            elif item_type == "gem":
                gem_level, level_error = self.resolve_gem_level_conn(
                    conn,
                    client_id,
                    item_id,
                    item["name"],
                    wanted_level,
                    "二手市场上架 {name} {level}级 1 5000",
                )
                if level_error:
                    return level_error
                assert gem_level is not None
                removed = self.remove_gem_conn(conn, client_id, item_id, gem_level, quantity)
                listing_item_id = self._gem_listing_id(item_id, gem_level)
                item_label = f"{item['name']} {gem_level}级"
            else:
                removed = self.remove_ring_conn(conn, client_id, item_id, quantity)
                listing_item_id = item_id
                item_label = item["name"]
            if not removed:
                return T.hint(f"库存不足，无法上架 {item_label} x{quantity}。", "发送：背包 或 纳戒 确认库存数量。<背包><纳戒>")
            conn.execute(
                """
                INSERT INTO second_hand_listings
                (seller_id, item_type, item_id, quantity, total_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_id, item_type, listing_item_id, quantity, total_price, ts()),
            )
        return f"上架成功：{item_label} x{quantity}，总价 {money(total_price)}。"

    def cancel(self, client_id: str) -> str:
        """下架自己的商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM second_hand_listings WHERE seller_id = ?",
                (client_id,),
            ).fetchone()
            if not row:
                return T.hint("你当前没有上架商品。", "发送：二手市场 查看当前市场，或发送：二手市场上架 名称 数量 总价。<二手市场>")
            if row["item_type"] == "backpack":
                ok, reason = self.can_add_backpack_conn(conn, client_id, row["item_id"], row["quantity"])
                if not ok:
                    return T.hint("背包空间不足，暂时无法下架。", reason)
                self.add_backpack_conn(conn, client_id, row["item_id"], row["quantity"])
            elif row["item_type"] == "gem":
                gem_id, gem_level = self._split_gem_listing_id(row["item_id"])
                self.add_gem_conn(conn, client_id, gem_id, gem_level, row["quantity"])
            elif row["item_type"] == "ring":
                self.add_ring_conn(conn, client_id, row["item_id"], row["quantity"])
            elif row["item_type"] == "weapon":
                restored = self._restore_market_weapon_conn(conn, client_id, row)
                if not restored:
                    return T.hint("这把武器没有在市场托管中，无法下架。", "请查看二手市场，或重新整理市场数据。<二手市场>")
            elif row["item_type"] == "flame":
                self._restore_flame_cancel(conn, client_id, row["item_id"])
            else:
                return T.hint("该上架类型当前不支持下架。", "请先查看二手市场确认商品类型。")
            conn.execute("DELETE FROM second_hand_listings WHERE listing_id = ?", (row["listing_id"],))
        return "下架成功，物品已退回。"

    def buy(self, client_id: str, message: str) -> str:
        """购买某个卖家的当前商品。"""

        buyer, error = self.require_player(client_id)
        if error:
            return error
        seller_id = self.player_id_from_last_arg(message)
        if not seller_id:
            return T.hint("没有找到这个卖家。", "发送：二手市场 查看卖家名称，再发送：二手市场购买 卖家名称，也可以直接@卖家。<二手市场>")
        if seller_id == client_id:
            return T.hint("不能购买自己的商品。", "想收回商品请发送：二手市场下架<二手市场下架>")

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM second_hand_listings WHERE seller_id = ?",
                (seller_id,),
            ).fetchone()
            if not row:
                return T.hint("该玩家当前没有上架商品。", "发送：二手市场 查看当前可购买列表。<二手市场>")
            if row["item_type"] == "backpack":
                ok, reason = self.can_add_backpack_conn(conn, client_id, row["item_id"], row["quantity"])
                if not ok:
                    return reason
            elif row["item_type"] == "weapon":
                weapon = self._market_weapon_conn(conn, row)
                if not weapon:
                    conn.execute("DELETE FROM second_hand_listings WHERE listing_id = ?", (row["listing_id"],))
                    return T.hint("这把武器已经不在市场托管中。", "该上架已清理，请重新查看二手市场。<二手市场><二手市场下架>")
            elif row["item_type"] == "flame":
                error_text = self._restore_flame_buy(conn, client_id, row["item_id"])
                if error_text:
                    return T.hint(error_text, "异火购买失败，你的资产不受影响。<异火><二手市场>")
            elif row["item_type"] not in {"ring", "gem"}:
                return T.hint("该上架类型当前不支持购买。", "请重新查看二手市场，选择背包物品、纳戒物品或武器。<二手市场><背包><纳戒>")
            if not self.spend_stones_conn(conn, client_id, row["total_price"]):
                return T.hint("随身源石不足。", "发送：源库 查看存量，或发送：取出源石 数量。<源库>")
            fee = int(row["total_price"] * MARKET_FEE_RATE)
            seller_gain = row["total_price"] - fee
            conn.execute(
                "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                (seller_gain, seller_id),
            )
            if row["item_type"] == "backpack":
                self.add_backpack_conn(conn, client_id, row["item_id"], row["quantity"])
            elif row["item_type"] == "gem":
                gem_id, gem_level = self._split_gem_listing_id(row["item_id"])
                self.add_gem_conn(conn, client_id, gem_id, gem_level, row["quantity"])
            elif row["item_type"] == "ring":
                self.add_ring_conn(conn, client_id, row["item_id"], row["quantity"])
            elif row["item_type"] == "weapon":
                buyer_has_weapon = conn.execute(
                    "SELECT 1 FROM player_weapons WHERE holder_id = ? LIMIT 1",
                    (client_id,),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE player_weapons
                    SET holder_id = ?, equipped = ?
                    WHERE weapon_id = ? AND holder_id = ?
                    """,
                    (
                        client_id,
                        0 if buyer_has_weapon else 1,
                        int(row["item_id"]),
                        self._market_holder(row["listing_id"]),
                    ),
                )
                conn.execute(
                    """
                    UPDATE weapon_legends
                    SET current_owner_id = ?, updated_at = ?
                    WHERE weapon_id = ?
                    """,
                    (client_id, ts(), int(row["item_id"])),
                )
            conn.execute("DELETE FROM second_hand_listings WHERE listing_id = ?", (row["listing_id"],))
            conn.execute(
                """
                INSERT INTO second_hand_records
                (buyer_id, seller_id, item_type, item_id, quantity, total_price, fee, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    seller_id,
                    row["item_type"],
                    row["item_id"],
                    row["quantity"],
                    row["total_price"],
                    fee,
                    ts(),
                ),
            )
        name = self._item_name(row["item_type"], row["item_id"])
        quantity = "" if row["item_type"] == "weapon" else f" x{row['quantity']}"
        return f"购买成功：{name}{quantity}，花费 {money(row['total_price'])}。"

    def _sell_weapon(self, client_id: str, weapon_id: int, total_price: int) -> str:
        """按武器实例 ID 上架一把具体武器。"""

        with self.db.transaction() as conn:
            exists = conn.execute(
                "SELECT listing_id FROM second_hand_listings WHERE seller_id = ?",
                (client_id,),
            ).fetchone()
            if exists:
                return T.hint("你已经有一包商品在上架。", "先发送：二手市场下架，或等其他玩家购买。<二手市场下架>")

            weapon = self._seller_weapon_conn(conn, client_id, weapon_id)
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID，再用：二手市场上架 武器#ID 总价<武器>")
            if int(weapon["equipped"]):
                return T.hint("已装备武器不能上架。", "先切换到其他武器，再上架这把备用武器。<武器>")

            count = conn.execute(
                "SELECT COUNT(*) AS total FROM player_weapons WHERE holder_id = ?",
                (client_id,),
            ).fetchone()
            if int(count["total"]) <= 1:
                return T.hint("不能上架最后一把武器。", "至少保留一把自用武器，避免重要家当全部被卖掉。")

            cursor = conn.execute(
                """
                INSERT INTO second_hand_listings
                (seller_id, item_type, item_id, quantity, total_price, created_at)
                VALUES (?, 'weapon', ?, 1, ?, ?)
                """,
                (client_id, str(weapon_id), total_price, ts()),
            )
            listing_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE player_weapons
                SET holder_id = ?, equipped = 0
                WHERE holder_id = ? AND weapon_id = ? AND equipped = 0
                """,
                (self._market_holder(listing_id), client_id, weapon_id),
            )

        return f"上架成功：{self._weapon_label(dict(weapon))}，总价 {money(total_price)}。"

    def _item_name(self, item_type: str, item_id: str) -> str:
        """按库存类型获取名称。"""


        if item_type == "backpack":
            item = self.item_def(item_id)
            return item["name"] if item else item_id
        if item_type == "ring":
            item = self.ring_item_def(item_id)
            return item["name"] if item else item_id
        if item_type == "gem":
            gem_id, gem_level = self._split_gem_listing_id(item_id)
            item = self.ring_item_def(gem_id)
            name = item["name"] if item else gem_id
            return f"{name} {gem_level}级"
        if item_type == "weapon":
            weapon = self._weapon_by_id(to_int(item_id))
            return self._weapon_label(weapon) if weapon else f"武器{weapon_id_label(item_id)}"
        if item_type == "flame":
            flame = self.db.fetch_one("SELECT name FROM flame_defs WHERE flame_id = ?", (item_id,))
            return f"异火 {flame['name']}" if flame else f"异火 {item_id}"
        return item_id

    @staticmethod
    def _gem_listing_id(gem_id: str, level: int) -> str:
        """把宝石 id 和等级打包保存到二手市场。"""

        return f"{gem_id}@{max(1, int(level))}"

    @staticmethod
    def _split_gem_listing_id(value: str) -> tuple[str, int]:
        """从二手市场宝石标识里拆回 id 和等级。"""

        gem_id, sep, level_text = value.rpartition("@")
        if not sep:
            return value, 1
        return gem_id, max(1, to_int(level_text, 1))

    @staticmethod
    def _parse_weapon_listing(parts: list[str]) -> tuple[int, int]:
        """解析武器上架写法：武器#ID 总价 / #ID 总价 / 武器 ID 总价。"""

        if len(parts) == 2:
            weapon_id = parse_weapon_ref(parts[0])
            return weapon_id, to_int(parts[1]) if weapon_id else 0
        if len(parts) == 3 and parts[0] in {"武器", "武器ID"}:
            weapon_id = parse_weapon_ref(parts[1])
            return weapon_id, to_int(parts[2]) if weapon_id else 0
        return 0, 0

    def _seller_weapon_conn(self, conn, client_id: str, weapon_id: int):
        """在事务里读取卖家自己持有的武器。"""

        return conn.execute(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ? AND w.weapon_id = ?
            """,
            (client_id, weapon_id),
        ).fetchone()

    def _market_weapon_conn(self, conn, listing) -> object:
        """读取市场托管中的武器。"""

        return conn.execute(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.weapon_id = ? AND w.holder_id = ?
            """,
            (int(listing["item_id"]), self._market_holder(listing["listing_id"])),
        ).fetchone()

    def _restore_market_weapon_conn(self, conn, client_id: str, listing) -> bool:
        """把市场托管武器归还给卖家。"""

        cursor = conn.execute(
            """
            UPDATE player_weapons
            SET holder_id = ?, equipped = 0
            WHERE weapon_id = ? AND holder_id = ?
            """,
            (client_id, int(listing["item_id"]), self._market_holder(listing["listing_id"])),
        )
        return cursor.rowcount > 0

    def _weapon_by_id(self, weapon_id: int) -> dict | None:
        """按实例 ID 读取武器，不限制当前所有者。"""

        return self.db.fetch_one(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.weapon_id = ?
            """,
            (weapon_id,),
        )

    def _weapon_label(self, weapon: dict) -> str:
        """展示武器差异化属性，避免同名武器混淆。"""

        enchants = len(load_json(weapon["enchant_effects"], []))
        return (
            f"武器{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}[{weapon['quality']}] "
            f"等级:{weapon['level']}/{weapon['max_level']} 攻击:{computed_weapon_attack(weapon)} "
            f"附魔:{enchants}/{computed_weapon_enchant_slots(weapon)}"
        )

    def _try_sell_flame(self, client_id: str, flame_name: str, total_price: int) -> str | None:
        """尝试上架异火；非异火名称时返回 None 让上层继续走普通物品逻辑。"""

        flame_def = self.db.fetch_one("SELECT * FROM flame_defs WHERE name = ?", (flame_name,))
        if not flame_def:
            return None
        if total_price <= 0:
            return T.hint("异火上架价格必须大于 0。", "发送：二手市场上架 异火名 1 总价")
        with self.db.transaction() as conn:
            exists = conn.execute(
                "SELECT listing_id FROM second_hand_listings WHERE seller_id = ?",
                (client_id,),
            ).fetchone()
            if exists:
                return T.hint("你已经有一包商品在上架。", "先发送：二手市场下架，或等其他玩家购买。<二手市场下架>")
            flame = conn.execute(
                "SELECT * FROM player_flames WHERE client_id = ? AND flame_id = ?",
                (client_id, flame_def["flame_id"]),
            ).fetchone()
            if not flame:
                return T.hint(f"你尚未拥有 {flame_name}。", "发送：异火 查看已拥有的异火。<异火>")
            if int(flame["equipped"]):
                return T.hint(f"{flame_name} 正在装备中，不能上架。", "先发送：异火卸下，再上架。<异火卸下>")
            conn.execute(
                "DELETE FROM player_flames WHERE client_id = ? AND flame_id = ?",
                (client_id, flame_def["flame_id"]),
            )
            conn.execute(
                """
                INSERT INTO second_hand_listings
                (seller_id, item_type, item_id, quantity, total_price, created_at)
                VALUES (?, 'flame', ?, 1, ?, ?)
                """,
                (client_id, flame_def["flame_id"], total_price, ts()),
            )
        return f"上架成功：异火 {flame_name}，总价 {money(total_price)}。"

    def _restore_flame_cancel(self, conn, client_id: str, flame_id: str) -> None:
        """下架时把异火退回给卖家。"""

        conn.execute(
            """
            INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at)
            VALUES (?, ?, 0, '二手市场下架', ?, ?)
            """,
            (client_id, flame_id, ts(), ts()),
        )

    def _restore_flame_buy(self, conn, client_id: str, flame_id: str) -> str | None:
        """购买时把异火给买家；已有同名或已有帝炎时返回错误文本。"""

        has_di_yan = conn.execute(
            "SELECT 1 FROM player_flames WHERE client_id = ? AND flame_id = 'di_yan'",
            (client_id,),
        ).fetchone()
        if has_di_yan:
            return "你已拥有帝炎，不能再获得其他异火。"
        existing = conn.execute(
            "SELECT 1 FROM player_flames WHERE client_id = ? AND flame_id = ?",
            (client_id, flame_id),
        ).fetchone()
        if existing:
            return "你已拥有同名异火，不能重复购买。"
        conn.execute(
            """
            INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at)
            VALUES (?, ?, 0, '二手市场购买', ?, ?)
            """,
            (client_id, flame_id, ts(), ts()),
        )
        return None

    def _market_holder(self, listing_id: int) -> str:
        """市场托管武器使用的临时 holder_id。"""

        return f"{self.market_owner_prefix}{listing_id}"


service = SecondHandService(db)

__all__ = ["SecondHandService", "service"]
