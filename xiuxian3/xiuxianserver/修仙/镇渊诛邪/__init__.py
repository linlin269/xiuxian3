"""镇渊诛邪组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("镇渊", "诛邪"), priority=100, block=True)
async def ws_zhenyuan_overview(client_id: str, message: str) -> None:
    """查看镇渊/诛邪总览。"""

    await send_reply(client_id, service.overview(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("镇渊帮助", "诛邪帮助"), priority=100, block=True)
async def ws_zhenyuan_help(client_id: str, message: str) -> None:
    """查看镇渊/诛邪帮助。"""

    await send_reply(client_id, service.help_info(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("镇渊面板", "诛邪面板"), priority=100, block=True)
async def ws_zhenyuan_panel(client_id: str, message: str) -> None:
    """查看镇渊/诛邪详细面板。"""

    await send_reply(client_id, service.panel(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("镇渊积分", "诛邪积分"), priority=100, block=True)
async def ws_zhenyuan_points(client_id: str, message: str) -> None:
    """查看镇渊/诛邪积分进度。"""

    await send_reply(client_id, service.points(client_id), ws_manager, service)
