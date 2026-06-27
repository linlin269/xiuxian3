"""黑市组件服务。"""

from __future__ import annotations

from typing import Any

from ..common import CoreService, business_day, split_words, ts
from ..format_text import T
from ..rules import money
from ..sql import db
from ..镇渊诛邪.service import service as zhenyuan_zhuxie_service


BLACK_MARKET_DEFS: tuple[dict[str, Any], ...] = (
    {
        "market_item_id": "shou_hun_dan",
        "ring_item_id": "shou_hun_dan",
        "display_name": "兽魂丹",
        "item_type": "坐骑材料",
        "sale_price": 500,
        "recycle_rate": 0.8,
        "can_buy": 1,
        "can_recycle": 1,
        "sort_order": 10,
        "usage": "普通坐骑进阶材料，供坐骑系统进阶时消耗。",
        "source": "当前可通过黑市购买获得，购入后直接进入纳戒。",
    },
    {
        "market_item_id": "shou_xue",
        "ring_item_id": "shou_xue",
        "display_name": "兽血",
        "item_type": "坐骑材料",
        "sale_price": 10000,
        "recycle_rate": 0.8,
        "can_buy": 1,
        "can_recycle": 1,
        "sort_order": 20,
        "usage": "普通坐骑升星材料，供坐骑系统升星时消耗。",
        "source": "当前可通过黑市购买获得，购入后直接进入纳戒。",
    },
    {
        "market_item_id": "huan_shou_hun_dan",
        "ring_item_id": "huan_shou_hun_dan",
        "display_name": "幻兽魂丹",
        "item_type": "坐骑材料",
        "sale_price": 1000,
        "recycle_rate": 0.8,
        "can_buy": 1,
        "can_recycle": 1,
        "sort_order": 30,
        "usage": "显化坐骑进阶材料，供显化方向的坐骑进阶时消耗。",
        "source": "当前可通过黑市购买获得，购入后直接进入纳戒。",
    },
    {
        "market_item_id": "huan_shou_xue",
        "ring_item_id": "huan_shou_xue",
        "display_name": "幻兽血",
        "item_type": "坐骑材料",
        "sale_price": 15000,
        "recycle_rate": 0.8,
        "can_buy": 1,
        "can_recycle": 1,
        "sort_order": 40,
        "usage": "显化坐骑升星材料，供显化方向的坐骑升星时消耗。",
        "source": "当前可通过黑市购买获得，购入后直接进入纳戒。",
    },
    {
        "market_item_id": "ji_huan_shou_xue",
        "ring_item_id": "ji_huan_shou_xue",
        "display_name": "极幻兽血",
        "item_type": "坐骑材料",
        "sale_price": 20000,
        "recycle_rate": 0.8,
        "can_buy": 1,
        "can_recycle": 1,
        "sort_order": 50,
        "usage": "极显化坐骑升星材料，供极显化方向的坐骑升星时消耗。",
        "source": "当前可通过黑市购买获得，购入后直接进入纳戒。",
    },
)
"""黑市固定目录。本次先接入 5 个坐骑材料商品。"""


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
        panel.line("支持命令：黑市列表、黑市列表 编号/名称、黑市购买、黑市回收。")
        panel.line(f"默认回收比例：售价的 **{int(self.recycle_rate * 100)}%**，向下取整。")
        panel.line("黑市材料按纳戒物品交易，适配坐骑模块的实际消耗方式。")
        return panel.render() + "<黑市列表><黑市购买><黑市回收>"

    def list_items(self, client_id: str, message: str = "") -> str:
        """查看黑市商品列表或单个商品详情。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        if not BLACK_MARKET_DEFS:
            return T.hint(
                "黑市目录暂时为空。",
                "后续开放商品后，可以发送：黑市 / 黑市列表 / 黑市购买 物品名 数量 / 黑市回收 物品名 数量。<黑市>",
            )

        target = " ".join(split_words(message)).strip()
        if target:
            market_item = self._find_item(target)
            if not market_item:
                return T.hint(
                    f"黑市目录中没有找到：{target}。",
                    "发送：黑市列表 查看编号，或发送：黑市列表 编号 / 黑市列表 物品名 查看单个商品详情。<黑市列表>",
                )
            return self._render_item_detail(market_item)

        panel = T.panel()
        panel.section("黑市列表")
        panel.line(f"回收比例：售价的 **{int(self.recycle_rate * 100)}%**，向下取整。")
        panel.line("输入：黑市列表 编号 或 黑市列表 名称，可查看单个商品详情。")
        for index, item in enumerate(self._ordered_items(), start=1):
            sale_price = int(item["sale_price"])
            recycle_price = self._recycle_price(sale_price, float(item.get("recycle_rate", self.recycle_rate)))
            flags = []
            if int(item.get("can_buy", 1)):
                flags.append("可买")
            if int(item.get("can_recycle", 1)):
                flags.append("可回收")
            flag_text = "｜" + "｜".join(flags) if flags else ""
            panel.line(
                f"{index}. **{item['display_name']}**｜售价 **{money(sale_price)}**｜回收 **{money(recycle_price)}**{flag_text}"
            )
        return panel.render() + "<黑市购买><黑市回收>"

    def buy(self, client_id: str, message: str) -> str:
        """购买黑市商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        item_target, quantity, error_text = self._parse_request(message)
        if error_text:
            return error_text
        if not BLACK_MARKET_DEFS:
            return T.hint(
                f"黑市目录暂时没有商品，无法购买 {item_target}。",
                "先发送：黑市列表 查看目录，或等待黑市商品开放。<黑市列表><黑市>",
            )

        market_item = self._find_item(item_target)
        if not market_item:
            return T.hint(
                f"黑市目录中没有找到：{item_target}。",
                "发送：黑市列表 查看黑市商品目录，或复制准确的物品名称/编号。<黑市列表>",
            )
        if not int(market_item.get("can_buy", 1)):
            return T.hint(
                f"{market_item['display_name']} 当前不允许购买。",
                "请先查看黑市列表，确认该商品的购买状态。<黑市列表>",
            )

        ring_item_id = str(market_item["ring_item_id"])
        ring_item = self.ring_item_def(ring_item_id)
        if not ring_item:
            return T.hint(
                f"黑市商品配置异常：{market_item['display_name']} 对应的纳戒物品不存在。",
                "请联系管理员检查黑市与纳戒物品定义。",
            )

        sale_price = int(market_item["sale_price"])
        discount = float(zhenyuan_zhuxie_service.player_bonus(client_id).get("black_market_discount", 0.0))
        final_unit_price = zhenyuan_zhuxie_service.discounted_price(sale_price, discount)
        total_price = final_unit_price * quantity
        with self.db.transaction() as conn:
            if not self.spend_stones_conn(conn, client_id, total_price):
                return T.hint(
                    f"源石不足，购买 {market_item['display_name']} x{quantity} 需要 {money(total_price)}。",
                    "发送：源库 查看存量，或发送：取出源石 数量。<源库>",
                )
            self.add_ring_conn(conn, client_id, ring_item_id, quantity)
            self._record_trade_conn(conn, client_id, "buy", market_item, quantity, final_unit_price, total_price)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '黑市购买', ?, ?)",
                (client_id, f"item={ring_item_id}, quantity={quantity}, unit={final_unit_price}, total={total_price}, discount={discount:.3f}", ts()),
            )
        return f"购买成功：{market_item['display_name']} x{quantity}，花费 {money(total_price)}，物品已发入纳戒。"

    def recycle(self, client_id: str, message: str) -> str:
        """回收黑市商品。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        item_target, quantity, error_text = self._parse_request(message)
        if error_text:
            return error_text
        if not BLACK_MARKET_DEFS:
            return T.hint(
                f"黑市目录暂时没有可回收物品，无法回收 {item_target}。",
                "先发送：黑市列表 查看目录，或等待黑市商品开放。<黑市列表><黑市>",
            )

        market_item = self._find_item(item_target)
        if not market_item:
            return T.hint(
                f"黑市目录中没有找到：{item_target}。",
                "发送：黑市列表 查看黑市商品目录，或复制准确的物品名称/编号。<黑市列表>",
            )
        if not int(market_item.get("can_recycle", 1)):
            return T.hint(
                f"{market_item['display_name']} 当前不允许回收。",
                "请先查看黑市列表，确认该商品的回收状态。<黑市列表>",
            )

        ring_item_id = str(market_item["ring_item_id"])
        ring_item = self.ring_item_def(ring_item_id)
        if not ring_item:
            return T.hint(
                f"黑市商品配置异常：{market_item['display_name']} 对应的纳戒物品不存在。",
                "请联系管理员检查黑市与纳戒物品定义。",
            )

        unit_price = self._recycle_price(
            int(market_item["sale_price"]),
            float(market_item.get("recycle_rate", self.recycle_rate)),
        )
        total_price = unit_price * quantity
        with self.db.transaction() as conn:
            if not self.remove_ring_conn(conn, client_id, ring_item_id, quantity):
                return T.hint(
                    f"纳戒库存不足，无法回收 {market_item['display_name']} x{quantity}。",
                    "发送：纳戒 查看准确库存，或发送：黑市列表 编号/名称 查看该商品详情。<纳戒><黑市列表>",
                )
            self._add_stones_conn(conn, client_id, total_price)
            self._record_trade_conn(conn, client_id, "recycle", market_item, quantity, unit_price, total_price)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '黑市回收', ?, ?)",
                (client_id, f"item={ring_item_id}, quantity={quantity}, total={total_price}", ts()),
            )
        return f"回收成功：{market_item['display_name']} x{quantity}，获得 {money(total_price)}。"

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
        if not parts:
            return "", 0, T.hint(
                "黑市命令格式不正确。",
                "发送：黑市购买 物品名 [数量]，或发送：黑市回收 物品名 [数量]；编号也可直接使用。<黑市列表>",
            )

        quantity = 1
        item = " ".join(parts).strip()
        if len(parts) >= 2 and parts[-1].lstrip("+-").isdigit():
            quantity = int(parts[-1])
            item = " ".join(parts[:-1]).strip()

        if quantity <= 0:
            return "", 0, T.hint(
                "数量必须大于 0。",
                "发送：黑市购买 物品名 [数量]，或发送：黑市回收 物品名 [数量]。<黑市列表>",
            )
        if not item:
            return "", 0, T.hint(
                "缺少物品名称或编号。",
                "发送：黑市购买 物品名 [数量]，或发送：黑市回收 物品名 [数量]；也可直接使用黑市列表里的编号。<黑市列表>",
            )
        return item, quantity, None

    @staticmethod
    def _normalize_target(text: str) -> str:
        return text.strip().lower()

    def _ordered_items(self) -> list[dict[str, Any]]:
        return sorted(BLACK_MARKET_DEFS, key=self._sort_key)

    def _find_item(self, target: str) -> dict[str, Any] | None:
        normalized = self._normalize_target(target)
        ordered_items = self._ordered_items()
        if normalized.isdigit():
            index = int(normalized)
            if 1 <= index <= len(ordered_items):
                return ordered_items[index - 1]
        for item in ordered_items:
            market_item_id = self._normalize_target(str(item.get("market_item_id", "")))
            ring_item_id = self._normalize_target(str(item.get("ring_item_id", "")))
            display_name = self._normalize_target(str(item.get("display_name", "")))
            if normalized in {market_item_id, ring_item_id, display_name}:
                return item
        return None

    def _item_index(self, market_item: dict[str, Any]) -> int:
        for index, item in enumerate(self._ordered_items(), start=1):
            if str(item.get("market_item_id", "")) == str(market_item.get("market_item_id", "")):
                return index
        return 0

    def _render_item_detail(self, market_item: dict[str, Any]) -> str:
        index = self._item_index(market_item)
        sale_price = int(market_item["sale_price"])
        recycle_price = self._recycle_price(sale_price, float(market_item.get("recycle_rate", self.recycle_rate)))
        panel = T.panel()
        panel.section(f"黑市详情｜{market_item['display_name']}")
        panel.line(f"编号：{index}")
        panel.line(f"类型：{market_item['item_type']}")
        panel.line(f"纳戒物品：{market_item['ring_item_id']}")
        panel.line(f"售价：**{money(sale_price)}**")
        panel.line(f"回收价：**{money(recycle_price)}**")
        panel.line(f"作用：{market_item.get('usage', '暂无说明')}")
        panel.line(f"获取方式：{market_item.get('source', '暂无说明')}")
        panel.line(
            f"状态：{'可购买' if int(market_item.get('can_buy', 1)) else '不可购买'}｜{'可回收' if int(market_item.get('can_recycle', 1)) else '不可回收'}"
        )
        panel.line(f"购买示例：黑市购买 {index} 1")
        panel.line(f"回收示例：黑市回收 {index} 1")
        return panel.render() + "<黑市购买><黑市回收><黑市列表>"

    @staticmethod
    def _add_stones_conn(conn, client_id: str, amount: int) -> None:
        if amount <= 0:
            return
        conn.execute(
            "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
            (amount, client_id),
        )

    def _record_trade_conn(
        self,
        conn,
        client_id: str,
        action: str,
        market_item: dict[str, Any],
        quantity: int,
        unit_price: int,
        total_price: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO black_market_records
            (client_id, action, market_item_id, ring_item_id, item_name, quantity, unit_price, total_price, recycle_rate, business_day, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                action,
                str(market_item["market_item_id"]),
                str(market_item["ring_item_id"]),
                str(market_item["display_name"]),
                quantity,
                unit_price,
                total_price,
                float(market_item.get("recycle_rate", self.recycle_rate)),
                business_day(),
                ts(),
            ),
        )


service = BlackMarketService(db)
