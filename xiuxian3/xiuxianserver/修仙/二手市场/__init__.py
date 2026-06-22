"""二手市场组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("二手市场", "小黄鱼"), priority=100, block=True)
async def ws_second_hand_list(client_id: str, message: str) -> None:
    """查看二手市场。"""

    await send_reply(client_id, service.list_items(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("二手市场上架", "小黄鱼上架"), priority=100, block=True)
async def ws_second_hand_sell(client_id: str, message: str) -> None:
    """上架商品。"""

    await send_reply(client_id, service.sell(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd=("二手市场下架", "小黄鱼下架"), priority=100, block=True)
async def ws_second_hand_cancel(client_id: str, message: str) -> None:
    """下架商品。"""

    await send_reply(client_id, service.cancel(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("二手市场购买", "小黄鱼购买"), priority=100, block=True)
async def ws_second_hand_buy(client_id: str, message: str) -> None:
    """购买商品。"""

    await send_reply(client_id, service.buy(client_id, message), ws_manager, service)
