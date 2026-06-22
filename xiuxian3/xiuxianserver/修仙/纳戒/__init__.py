"""纳戒组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="纳戒", priority=100, block=True)
async def ws_ring_list(client_id: str, message: str) -> None:
    """查看纳戒。"""

    await send_reply(client_id, service.list_items(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="洗髓", priority=100, block=True)
async def ws_ring_wash(client_id: str, message: str) -> None:
    """消耗洗髓液洗髓体质。"""

    await send_reply(client_id, service.wash(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="武器淬锋", priority=100, block=True)
async def ws_ring_temper_weapon(client_id: str, message: str) -> None:
    """消耗淬锋丹提升武器上限。"""

    await send_reply(client_id, service.temper_weapon(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="开孔", priority=100, block=True)
async def ws_ring_open_equipment_hole(client_id: str, message: str) -> None:
    """消耗开孔器给装备开孔。"""

    await send_reply(client_id, service.open_equipment_hole(client_id, message), ws_manager, service)
