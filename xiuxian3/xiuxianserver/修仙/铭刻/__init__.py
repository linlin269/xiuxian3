"""铭刻组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="铭刻", priority=100, block=True)
async def ws_inscription_guide_or_dispatch(client_id: str, message: str) -> None:
    """查看铭刻格式，或按目标分发铭刻。"""

    await send_reply(client_id, service.dispatch(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="铭刻之羽", priority=100, block=True)
async def ws_inscription_feathers(client_id: str, message: str) -> None:
    """查看未使用的铭刻之羽。"""

    await send_reply(client_id, service.feathers(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="铭刻装备", priority=100, block=True)
async def ws_inscription_fixed_equipment(client_id: str, message: str) -> None:
    """铭刻装备。"""

    await send_reply(client_id, service.fixed_equipment(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="铭刻武器", priority=100, block=True)
async def ws_inscription_weapon(client_id: str, message: str) -> None:
    """铭刻武器。"""

    await send_reply(client_id, service.weapon(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="铭刻附魔", priority=100, block=True)
async def ws_inscription_enchant(client_id: str, message: str) -> None:
    """铭刻武器上已附魔的技能书。"""

    await send_reply(client_id, service.enchant(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="铭刻技能", priority=100, block=True)
async def ws_inscription_skill(client_id: str, message: str) -> None:
    """铭刻武器自带技能；带附魔序号时仍可铭刻附魔。"""

    await send_reply(client_id, service.skill_or_enchant(client_id, message), ws_manager, service)
