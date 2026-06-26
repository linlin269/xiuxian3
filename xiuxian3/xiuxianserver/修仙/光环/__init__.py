"""光环组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="光环", priority=100, block=True)
async def ws_halo_my(client_id: str, message: str) -> None:
    """查看当前光环信息。"""

    await send_reply(client_id, service.my_halo(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="提升心境", priority=100, block=True)
async def ws_halo_improve(client_id: str, message: str) -> None:
    """通过精神累计提升前 10 点心境。"""

    await send_reply(client_id, service.improve_xinjing(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="光环帮助", priority=100, block=True)
async def ws_halo_help(client_id: str, message: str) -> None:
    """查看光环帮助。"""

    await send_reply(client_id, service.help_info(client_id), ws_manager, service)
