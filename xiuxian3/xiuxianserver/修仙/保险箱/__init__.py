"""保险箱组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("保险箱", "查看保险箱"), priority=100, block=True)
async def ws_vault_list(client_id: str, message: str) -> None:
    """查看保险箱。"""

    await send_reply(client_id, service.list_items(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("存入保险箱", "存保险箱", "放入保险箱"), priority=100, block=True)
async def ws_vault_deposit(client_id: str, message: str) -> None:
    """存入保险箱。"""

    await send_reply(client_id, service.deposit(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd=("取出保险箱", "取保险箱"), priority=100, block=True)
async def ws_vault_withdraw(client_id: str, message: str) -> None:
    """从保险箱取出。"""

    await send_reply(client_id, service.withdraw(client_id, message), ws_manager, service)
