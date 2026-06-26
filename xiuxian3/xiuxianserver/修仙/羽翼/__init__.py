"""羽翼组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="羽翼", priority=100, block=True)
async def ws_wing_my(client_id: str, message: str) -> None:
    """查看当前羽翼信息。"""

    await send_reply(client_id, service.my_wing(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="提升身法", priority=100, block=True)
async def ws_wing_improve(client_id: str, message: str) -> None:
    """通过血气累计提升前 10 点身法。"""

    await send_reply(client_id, service.improve_shenfa(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="羽翼帮助", priority=100, block=True)
async def ws_wing_help(client_id: str, message: str) -> None:
    """查看羽翼帮助。"""

    await send_reply(client_id, service.help_info(client_id), ws_manager, service)
