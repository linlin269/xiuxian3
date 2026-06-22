"""源库组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..common import to_int
from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="源库", priority=100, block=True)
async def ws_source_vault_info(client_id: str, message: str) -> None:
    """查看源库。"""

    await send_reply(client_id, service.info(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="源库结息", priority=100, block=True)
async def ws_source_vault_settle(client_id: str, message: str) -> None:
    """源库结息。"""

    await send_reply(client_id, service.settle(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("升级源库", "源库升级"), priority=100, block=True)
async def ws_source_vault_upgrade(client_id: str, message: str) -> None:
    """升级源库。"""

    await send_reply(client_id, service.upgrade(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("存入源石", "源石存入"), priority=100, block=True)
async def ws_source_vault_deposit(client_id: str, message: str) -> None:
    """存入源石。"""

    await send_reply(client_id, service.deposit(client_id, to_int(message)), ws_manager, service)


@WsMessageHandler.handler(cmd=("取出源石", "源石取出"), priority=100, block=True)
async def ws_source_vault_withdraw(client_id: str, message: str) -> None:
    """取出源石。"""

    await send_reply(client_id, service.withdraw(client_id, to_int(message)), ws_manager, service)
