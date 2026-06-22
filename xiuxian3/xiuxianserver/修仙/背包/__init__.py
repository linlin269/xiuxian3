"""背包组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="背包", priority=100, block=True)
async def ws_backpack_list(client_id: str, message: str) -> None:
    """查看背包。"""

    await send_reply(client_id, service.list_items(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="使用", priority=100, block=True)
async def ws_backpack_use(client_id: str, message: str) -> None:
    """使用物品。"""

    await send_reply(client_id, service.use_item(client_id, message), ws_manager, service)
