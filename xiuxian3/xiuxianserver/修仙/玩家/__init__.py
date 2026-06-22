"""玩家组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="创建用户", priority=100, block=True)
async def ws_create_player(client_id: str, message: str) -> None:
    """创建修仙用户。"""

    await send_reply(client_id, service.create(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="改名", priority=100, block=True)
async def ws_rename_player(client_id: str, message: str) -> None:
    """修改展示名称。"""

    await send_reply(client_id, service.rename(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="修仙信息", priority=100, block=True)
async def ws_profile(client_id: str, message: str) -> None:
    """查看玩家详细信息。"""
    
    await send_reply(client_id, service.profile(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="状态", priority=100, block=True)
async def ws_status(client_id: str, message: str) -> None:
    """查看玩家关键状态。"""

    await send_reply(client_id, service.status(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="修仙日记", priority=100, block=True)
async def ws_player_diary(client_id: str, message: str) -> None:
    """查看个人修仙日记。"""

    await send_reply(client_id, service.diary(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="自动用药", priority=100, block=True)
async def ws_auto_medicine(client_id: str, message: str) -> None:
    """查看或修改探险自动用药开关。"""

    await send_reply(client_id, service.auto_medicine(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="战斗日志", priority=100, block=True)
async def ws_battle_log(client_id: str, message: str) -> None:
    """查看或修改战斗日志展示模式。"""

    await send_reply(client_id, service.battle_log(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="签到", priority=100, block=True)
async def ws_sign(client_id: str, message: str) -> None:
    """每日签到。"""

    await send_reply(client_id, service.sign(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="新手礼包", priority=100, block=True)
async def ws_newbie_gift(client_id: str, message: str) -> None:
    """领取新手礼包。"""

    await send_reply(client_id, service.newbie_gift(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="休息", priority=100, block=True)
async def ws_rest(client_id: str, message: str) -> None:
    """进入休息状态。"""

    await send_reply(client_id, service.rest(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("结束休息", "休息结束"), priority=100, block=True)
async def ws_end_rest(client_id: str, message: str) -> None:
    """结束休息并恢复。"""

    await send_reply(client_id, service.end_rest(client_id), ws_manager, service)
