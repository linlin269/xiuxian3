"""装备组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="装备", priority=100, block=True)
async def ws_equipment_list(client_id: str, message: str) -> None:
    """查看装备。"""

    await send_reply(client_id, service.list_equipment(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("装备升级", "升"), priority=100, block=True)
async def ws_equipment_upgrade(client_id: str, message: str) -> None:
    """升级装备。"""

    await send_reply(client_id, service.upgrade(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="孔位", priority=100, block=True)
async def ws_equipment_holes(client_id: str, message: str) -> None:
    """查看孔位。"""

    await send_reply(client_id, service.holes(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="镶嵌", priority=100, block=True)
async def ws_equipment_inlay(client_id: str, message: str) -> None:
    """镶嵌宝石。"""

    await send_reply(client_id, service.inlay(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="拆卸", priority=100, block=True)
async def ws_equipment_remove_inlay(client_id: str, message: str) -> None:
    """拆卸宝石。"""

    await send_reply(client_id, service.remove_inlay(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="宝石升级", priority=100, block=True)
async def ws_equipment_upgrade_inlay(client_id: str, message: str) -> None:
    """升级宝石。"""

    await send_reply(client_id, service.upgrade_inlay(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="宝石", priority=100, block=True)
async def ws_equipment_my_inlays(client_id: str, message: str) -> None:
    """查看宝石。"""

    await send_reply(client_id, service.my_inlays(client_id), ws_manager, service)
