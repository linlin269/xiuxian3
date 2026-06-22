"""修仙物品详情 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("查看修仙物品", "修仙物品查看", "查看"), priority=100, block=True)
async def ws_treasure_info(client_id: str, message: str) -> None:
    """查看任意修仙物品详情。"""

    await send_reply(client_id, service.info(client_id, message), ws_manager, service)
