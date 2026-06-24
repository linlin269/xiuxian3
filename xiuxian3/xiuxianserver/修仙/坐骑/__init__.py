"""坐骑组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="坐骑", priority=100, block=True)
async def ws_mount_my(client_id: str, message: str) -> None:
    """查看当前坐骑信息。"""
    await send_reply(client_id, service.my_mount(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="坐骑升星", priority=100, block=True)
async def ws_mount_star(client_id: str, message: str) -> None:
    """使用升星物品提升1星。"""
    await send_reply(client_id, service.star_up(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="坐骑进阶", priority=100, block=True)
async def ws_mount_advance(client_id: str, message: str) -> None:
    """批量使用进阶物品尝试进阶。"""
    await send_reply(client_id, service.advance(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="一键升星", priority=100, block=True)
async def ws_mount_star_all(client_id: str, message: str) -> None:
    """一键消耗全部升星物品。"""
    await send_reply(client_id, service.star_up_all(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="一键进阶", priority=100, block=True)
async def ws_mount_advance_all(client_id: str, message: str) -> None:
    """一键消耗全部进阶物品。"""
    await send_reply(client_id, service.advance_all(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="确认进阶", priority=100, block=True)
async def ws_mount_confirm_advance(client_id: str, message: str) -> None:
    """确认批量进阶。"""
    await send_reply(client_id, service.confirm_advance(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="确认一键进阶", priority=100, block=True)
async def ws_mount_confirm_advance_all(client_id: str, message: str) -> None:
    """确认一键进阶。"""
    await send_reply(client_id, service.confirm_advance_all(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="坐骑帮助", priority=100, block=True)
async def ws_mount_help(client_id: str, message: str) -> None:
    """展示坐骑帮助信息。"""
    await send_reply(client_id, service.help_info(client_id), ws_manager, service)


# 显化方向选择命令（4个方向）
@WsMessageHandler.handler(cmd="显化·东方青龙", priority=100, block=True)
async def ws_manifest_east(client_id: str, message: str) -> None:
    """选择东方·青龙显化方向。"""
    await send_reply(client_id, service.choose_manifest(client_id, "manifest_east"), ws_manager, service)


@WsMessageHandler.handler(cmd="显化·西方白虎", priority=100, block=True)
async def ws_manifest_west(client_id: str, message: str) -> None:
    """选择西方·白虎显化方向。"""
    await send_reply(client_id, service.choose_manifest(client_id, "manifest_west"), ws_manager, service)


@WsMessageHandler.handler(cmd="显化·南方朱雀", priority=100, block=True)
async def ws_manifest_south(client_id: str, message: str) -> None:
    """选择南方·朱雀显化方向。"""
    await send_reply(client_id, service.choose_manifest(client_id, "manifest_south"), ws_manager, service)


@WsMessageHandler.handler(cmd="显化·北方玄武", priority=100, block=True)
async def ws_manifest_north(client_id: str, message: str) -> None:
    """选择北方·玄武显化方向。"""
    await send_reply(client_id, service.choose_manifest(client_id, "manifest_north"), ws_manager, service)
