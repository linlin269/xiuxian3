"""称号组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="称号", priority=100, block=True)
async def ws_title_my(client_id: str, message: str) -> None:
    """查看当前佩戴称号、已拥有数量、入口按钮。"""

    await send_reply(client_id, service.my_titles(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="称号列表", priority=100, block=True)
async def ws_title_list(client_id: str, message: str) -> None:
    """按分类展示全部称号和获取状态。"""

    await send_reply(client_id, service.list_all(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="称号详情", priority=100, block=True)
async def ws_title_detail(client_id: str, message: str) -> None:
    """查看单个称号的获取途径和佩戴状态。"""

    await send_reply(client_id, service.detail(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="称号佩戴", priority=100, block=True)
async def ws_title_equip(client_id: str, message: str) -> None:
    """手动佩戴已拥有的某个称号。"""

    await send_reply(client_id, service.equip(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="称号卸下", priority=100, block=True)
async def ws_title_unequip(client_id: str, message: str) -> None:
    """卸下手动佩戴，恢复自动佩戴模式。"""

    await send_reply(client_id, service.unequip(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="称号图鉴", priority=100, block=True)
async def ws_title_codex(client_id: str, message: str) -> None:
    """全部称号的锁定/解锁状态和获取进度。"""

    await send_reply(client_id, service.codex(client_id), ws_manager, service)
