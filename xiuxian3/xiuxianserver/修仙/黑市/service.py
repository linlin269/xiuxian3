"""黑市组件服务。"""

from __future__ import annotations

from typing import Any

from ..common import CoreService, split_words
from ..format_text import T
from ..rules import money
from ..sql import db


BLACK_MARKET_DEFS: tuple[dict[str, Any], ...] = ()
"""黑市固定目录。当前第一版保持为空，后续按设计方案逐步补充。"""


class BlackMarketService(CoreService):
    """系统统一售卖与统一回收的黑市服务。"""

    recycle_rate = 0.8

    def overview(self, client_id: str) -> str:
        """查看黑市总览。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        panel = T.panel()
        panel.section("黑市")
        panel.line("黑市是系统统一售卖与统一回收的特殊商店。")
        panel.line(f"当前目录：**{len(BLACK_MARKET_DEFS)}** 项。")
        panel.line("支持命令：黑市列表、黑市购买、黑市回收。")
        if BLACK_MARKET_DEFS:
            panel.line(f"回收比例：售价的 **{int(self.recycle_rate * 100)}%**，向下取整。")
        else:
            panel.line("当前黑市目录暂未开放，列表保持为空。")
        return panel.render() + "<黑市列表><黑市购买><黑市回收>"

    def list_items(self, client_id: str) -> str:
        """查看黑市商品列表。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        if not BLACK_MARKET_DEFS:
            return T.hint(
                "黑市目录暂时为空。",
                "后续开放商品后，可以发送：黑市 / 黑市列表 / 黑市购买 物品名 数量 / 黑市回收 物品名 数量。<黑市>",
            )

        panel = T.panel()
        panel.section("黑市列表")
        for index, item in enumerate(sorted(BLACK_MARKET_DEFS, key=self._sort_key), start=1):
            sale_price = int(item["sale_price"])
            recycle_price = self._recycle_price(sale_price, float(item.get("recycle_rate", self.recycle_rate)))
            flags = []
            if int(item.get("can_buy", 1)):
                flags.append("可买")
            if int(item.get("can_recycle", 1)):
                flags.append("可回收")
            flag_text = "｜" + "｜".join(flags) if flags else ""
            panel.line(
                f"{index}. **{item['display_name']}**｜{item['item_type']}｜{item['item_id']}｜"
                f"售价 **{money(sale_price)}**｜回收 **{money(recycle_price)}**{flag_text}"
            )
        return panel.render() + "<黑市购买><黑市回收>"

    def buy(self, client_id: str, message: str) -> str:
        """购买黑市商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        item, quantity, error_text = self._parse_request(message)
        if error_text:
            return error_text
        if not BLACK_MARKET_DEFS:
            return T.hint(
                f"黑市目录暂时没有商品，无法购买 {item}。",
                "先发送：黑市列表 查看目录，或等待黑市商品开放。<黑市列表><黑市>",
            )

        market_item = self._find_item(item)
        if not market_item:
            return T.hint(
                f"黑市目录中没有找到：{item}。",
                "发送：黑市列表 查看黑市商品目录，或复制准确的物品名称/编号。<黑市列表>",
            )
        if not int(market_item.get("can_buy", 1)):
            return T.hint(
                f"{market_item['display_name']} 当前不允许购买。",
                "请先查看黑市列表，确认该商品的购买状态。<黑市列表>",
            )

        return T.hint(
            f"黑市功能已接入，但当前目录仍为空，无法购买 {market_item['display_name']}。",
            "后续补充黑市商品后即可按黑市购买 物品名 数量 下单。<黑市列表><黑市>",
        )

    def recycle(self, client_id: str, message: str) -> str:
        """回收黑市商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        item, quantity, error_text = self._parse_request(message)
        if error_text:
            return error_text
        if not BLACK_MARKET_DEFS:
            return T.hint(
                f"黑市目录暂时没有可回收物品，无法回收 {item}。",
                "先发送：黑市列表 查看目录，或等待黑市商品开放。<黑市列表><黑市>",
            )

        market_item = self._find_item(item)
        if not market_item:
            return T.hint(
                f"黑市目录中没有找到：{item}。",
                "发送：黑市列表 查看黑市商品目录，或复制准确的物品名称/编号。<黑市列表>",
            )
        if not int(market_item.get("can_recycle", 1)):
            return T.hint(
                f"{market_item['display_name']} 当前不允许回收。",
                "请先查看黑市列表，确认该商品的回收状态。<黑市列表>",
            )

        unit_price = self._recycle_price(
            int(market_item["sale_price"]),
            float(market_item.get("recycle_rate", self.recycle_rate)),
        )
        total_price = unit_price * quantity
        return T.hint(
            f"黑市功能已接入，但当前目录仍为空，无法回收 {item} x{quantity}。",
            f"若后续开放该商品，回收单价将为 {money(unit_price)}，总价 {money(total_price)}。<黑市列表><黑市>",
        )

    @staticmethod
    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(item.get("sort_order", 0)),
            str(item.get("category", "")),
            str(item.get("display_name", "")),
            str(item.get("market_item_id", "")),
        )

    @staticmethod
    def _recycle_price(sale_price: int, recycle_rate: float) -> int:
        return max(0, int(sale_price * recycle_rate))

    @staticmethod
    def _parse_request(message: str) -> tuple[str, int, str | None]:
        parts = split_words(message)
        if len(parts) < 2:
            return "", 0, T.hint(
                "黑市命令格式不正确。",
                "发送：黑市购买 物品名 数量，或发送：黑市回收 物品名 数量。<黑市列表>",
            )
        quantity = int(parts[-1]) if parts[-1].isdigit() else 0
        if quantity <= 0:
            return "", 0, T.hint(
                "数量必须大于 0。",
                "发送：黑市购买 物品名 数量，或发送：黑市回收 物品名 数量。<黑市列表>",
            )
        item = " ".join(parts[:-1]).strip()
        if not item:
            return "", 0, T.hint(
                "缺少物品名称。",
                "发送：黑市购买 物品名 数量，或发送：黑市回收 物品名 数量。<黑市列表>",
            )
        return item, quantity, None

    @staticmethod
    def _normalize_target(text: str) -> str:
        return text.strip().lower()

    def _find_item(self, target: str) -> dict[str, Any] | None:
        normalized = self._normalize_target(target)
        for item in BLACK_MARKET_DEFS:
            market_item_id = self._normalize_target(str(item.get("market_item_id", "")))
            display_name = self._normalize_target(str(item.get("display_name", "")))
            if normalized in {market_item_id, display_name}:
                return item
        return None


service = BlackMarketService(db)
