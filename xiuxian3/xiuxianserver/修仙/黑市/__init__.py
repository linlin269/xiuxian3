"""黑市组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("黑市", "黑市总览"), priority=100, block=True)
async def ws_black_market_overview(client_id: str, message: str) -> None:
    """查看黑市简介。"""

    await send_reply(client_id, service.overview(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("黑市列表", "黑市清单"), priority=100, block=True)
async def ws_black_market_list(client_id: str, message: str) -> None:
    """查看黑市商品列表或单个商品详情。"""

    await send_reply(client_id, service.list_items(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd=("黑市购买", "黑市买入"), priority=100, block=True)
async def ws_black_market_buy(client_id: str, message: str) -> None:
    """购买黑市商品。"""

    await send_reply(client_id, service.buy(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd=("黑市回收", "黑市卖出"), priority=100, block=True)
async def ws_black_market_recycle(client_id: str, message: str) -> None:
    """回收黑市目录物品。"""

    await send_reply(client_id, service.recycle(client_id, message), ws_manager, service)
