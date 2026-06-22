"""玩家对战组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="切磋", priority=100, block=True)
async def ws_spar(client_id: str, message: str) -> None:
    """发起切磋。"""

    await send_reply(client_id, service.spar(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="接受切磋", priority=100, block=True)
async def ws_accept_spar(client_id: str, message: str) -> None:
    """接受切磋。"""

    await send_reply(client_id, service.accept_spar(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="拒绝切磋", priority=100, block=True)
async def ws_reject_spar(client_id: str, message: str) -> None:
    """拒绝切磋。"""

    await send_reply(client_id, service.reject_spar(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="决斗", priority=100, block=True)
async def ws_duel(client_id: str, message: str) -> None:
    """发起押注决斗。"""

    await send_reply(client_id, service.duel(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="接受决斗", priority=100, block=True)
async def ws_accept_duel(client_id: str, message: str) -> None:
    """接受押注决斗。"""

    await send_reply(client_id, service.accept_duel(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="拒绝决斗", priority=100, block=True)
async def ws_reject_duel(client_id: str, message: str) -> None:
    """拒绝押注决斗。"""

    await send_reply(client_id, service.reject_duel(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="决斗记录", priority=100, block=True)
async def ws_duel_records(client_id: str, message: str) -> None:
    """查看切磋和决斗记录。"""

    await send_reply(client_id, service.records(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="抢劫", priority=100, block=True)
async def ws_robbery(client_id: str, message: str) -> None:
    """抢劫正在探险中的玩家。"""

    await send_reply(client_id, service.robbery(client_id, message), ws_manager, service)
