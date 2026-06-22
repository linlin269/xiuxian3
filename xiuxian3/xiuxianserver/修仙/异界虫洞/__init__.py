"""异界虫洞组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="虫洞", priority=100, block=True)
async def ws_wormhole_status(client_id: str, message: str) -> None:
    """查看当前异界虫洞。"""

    await send_reply(client_id, service.status(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="虫洞状态", priority=100, block=True)
async def ws_wormhole_state(client_id: str, message: str) -> None:
    """查看当前异界虫洞状态。"""

    await send_reply(client_id, service.status(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="挑战虫洞", priority=100, block=True)
async def ws_wormhole_challenge(client_id: str, message: str) -> None:
    """挑战当前虫洞 Boss。"""

    await send_reply(client_id, service.challenge(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="虫洞排行", priority=100, block=True)
async def ws_wormhole_ranking(client_id: str, message: str) -> None:
    """查看虫洞伤害排行。"""

    await send_reply(client_id, service.ranking(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="虫洞奖励", priority=100, block=True)
async def ws_wormhole_reward(client_id: str, message: str) -> None:
    """领取虫洞贡献奖励。"""

    await send_reply(client_id, service.reward(client_id), ws_manager, service)
