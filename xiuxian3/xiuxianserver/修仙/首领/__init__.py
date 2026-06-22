"""首领组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd=("首领", "岁时情劫"), priority=100, block=True)
async def ws_seasonal_boss_status(client_id: str, message: str) -> None:
    """查看今日岁时情劫。"""

    await send_reply(client_id, service.status(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("首领状态", "岁时情劫状态"), priority=100, block=True)
async def ws_seasonal_boss_state(client_id: str, message: str) -> None:
    """查看今日岁时情劫状态。"""

    await send_reply(client_id, service.status(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("挑战首领", "挑战岁时情劫"), priority=100, block=True)
async def ws_seasonal_boss_challenge(client_id: str, message: str) -> None:
    """挑战今日岁时情劫。"""

    await send_reply(client_id, service.challenge(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("首领排行", "岁时情劫排行"), priority=100, block=True)
async def ws_seasonal_boss_ranking(client_id: str, message: str) -> None:
    """查看岁时情劫伤害排行。"""

    await send_reply(client_id, service.ranking(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("首领奖励", "岁时情劫奖励"), priority=100, block=True)
async def ws_seasonal_boss_reward(client_id: str, message: str) -> None:
    """领取岁时情劫贡献奖励。"""

    await send_reply(client_id, service.reward(client_id), ws_manager, service)
