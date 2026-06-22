"""背包组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import CoreService, load_json, ring_item_use_hint, parse_name_quantity_optional
from ..item_effects import service as item_effects
from ..sql import db


class BackpackService(CoreService):
    """背包库存。背包物品占负重。"""

    def list_items(self, client_id: str) -> str:
        """查看背包。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        rows = self.backpack_rows(client_id)
        weight = self.backpack_weight(client_id)
        if not rows:
            return T.hint(f"背包为空。负重 {weight}/{player['weight_limit']}。", "<自动出售><商场推荐>")
        panel = T.panel()
        panel.section("背包")
        panel.line(f"负重：**{weight}/{player['weight_limit']}**")
        panel.hr()
        panel.section("物品")
        for row in rows:
            panel.line(f"{row['name']} x{row['quantity']}｜单重 {row['weight']}｜{row['category']}")
        return panel.render() + "<自动出售><商场推荐>"

    def use_item(self, client_id: str, item_message: str) -> str:
        """使用恢复类物品。

        这个入口只允许消耗恢复类；宝石、技能书、洗髓液等都必须走自己的命令。
        """

        player, error = self.require_player(client_id)
        if error:
            return error
        item_name, quantity = parse_name_quantity_optional(item_message)
        if quantity <= 0:
            return T.hint("使用数量必须大于 0。", "发送：使用 物品名 数量，例如：使用 福袋 5")
        item = self.item_def_by_name(item_name)
        if not item:
            ring_item = self.ring_item_def_by_name(item_name)
            if not ring_item:
                return T.hint(f"没有找到物品：{item_name}。", "发送：背包 或 纳戒，复制准确物品名。<背包><纳戒>")
            if ring_item["name"] == "淬锋丹":
                return T.hint("淬锋丹不能直接使用。", "淬锋丹由纳戒承接消耗；发送：武器淬锋，或发送：武器淬锋 武器ID。<纳戒><武器>")
            if ring_item["category"] != "恢复类":
                return T.hint(f"{ring_item['name']} 不能直接使用。", ring_item_use_hint(ring_item))
            with self.db.transaction() as conn:
                if not self.remove_ring_conn(conn, client_id, ring_item["ring_item_id"], quantity):
                    return T.hint(f"纳戒里没有足够的 {ring_item['name']} x{quantity}。", "发送：纳戒 确认库存，或继续探险获取。<纳戒>")
                return item_effects.apply_many_conn(conn, client_id, ring_item, "纳戒", quantity)
        if item["category"] != "恢复类":
            return T.hint(f"{item['name']} 不能直接使用。", self._backpack_item_hint(item))

        with self.db.transaction() as conn:
            if not self.remove_backpack_conn(conn, client_id, item["item_id"], quantity):
                return T.hint(f"背包里没有足够的 {item['name']} x{quantity}。", "发送：背包 确认库存，或继续探险/跑商获取。<背包><商场推荐>")
            return item_effects.apply_many_conn(conn, client_id, item, "背包", quantity)

    @staticmethod
    def _backpack_item_hint(item: dict) -> str:
        """按世界物资大类给出正确处理入口。"""

        effect = load_json(item.get("effect"), {})
        category = str(effect.get("world_category") or item.get("category") or "")
        if category == "纯经济":
            return "这是跑商货物；做价差用 商场出售 商品名 数量，清包可发送：出售 物品名 数量 或 自动出售。<商场推荐><自动出售>"
        if category == "战利品":
            return "这是战利品，可以发送：出售 物品名 数量，或直接自动出售清理背包。<出售><自动出售>"
        if category in {"药路", "民生", "建设", "古物"}:
            return "这是世界物资，可以发送：出售 物品名 数量，或直接自动出售清理背包。<出售><自动出售>"
        return "背包物品请按所属玩法处理；宝石、技能书、洗髓液等在纳戒或对应组件使用。<背包><纳戒>"


service = BackpackService(db)

__all__ = ["BackpackService", "service"]
