"""武器组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="武器", priority=100, block=True)
async def ws_weapon_list(client_id: str, message: str) -> None:
    """查看武器简表。"""

    await send_reply(client_id, service.list_weapons(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="查看武器", priority=100, block=True)
async def ws_weapon_detail(client_id: str, message: str) -> None:
    """查看单把武器详情。"""

    await send_reply(client_id, service.detail(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="武器传奇", priority=100, block=True)
async def ws_weapon_legend(client_id: str, message: str) -> None:
    """查看单把武器传奇记录。"""

    await send_reply(client_id, service.legend(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="切换武器", priority=100, block=True)
async def ws_weapon_switch(client_id: str, message: str) -> None:
    """切换武器。"""

    await send_reply(client_id, service.switch(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="升级武器", priority=100, block=True)
async def ws_weapon_upgrade(client_id: str, message: str) -> None:
    """升级武器。"""

    await send_reply(client_id, service.upgrade(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="附魔武器", priority=100, block=True)
async def ws_weapon_enchant(client_id: str, message: str) -> None:
    """附魔武器。"""

    await send_reply(client_id, service.enchant(client_id, message), ws_manager, service)
