"""异火组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="异火", priority=100, block=True)
async def ws_flame_my(client_id: str, message: str) -> None:
    """查看自己持有异火、当前装备异火和入口按钮。"""

    await send_reply(client_id, service.my_flames(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="异火列表", priority=100, block=True)
async def ws_flame_list(client_id: str, message: str) -> None:
    """查看 23 种异火列表、排名和倍率。"""

    await send_reply(client_id, service.list_all(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="异火详情", priority=100, block=True)
async def ws_flame_detail(client_id: str, message: str) -> None:
    """查看单个异火外观、效果、来源和倍率。"""

    await send_reply(client_id, service.detail(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="异火装备", priority=100, block=True)
async def ws_flame_equip(client_id: str, message: str) -> None:
    """装备已拥有异火。"""

    await send_reply(client_id, service.equip(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="异火卸下", priority=100, block=True)
async def ws_flame_unequip(client_id: str, message: str) -> None:
    """卸下当前异火。"""

    await send_reply(client_id, service.unequip(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="异火合成", priority=100, block=True)
async def ws_flame_fuse(client_id: str, message: str) -> None:
    """尝试合成帝炎。"""

    await send_reply(client_id, service.fuse(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="异火交易", priority=100, block=True)
async def ws_flame_trade(client_id: str, message: str) -> None:
    """展示异火交易说明。"""

    await send_reply(client_id, service.trade_info(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="异火帮助", priority=100, block=True)
async def ws_flame_help(client_id: str, message: str) -> None:
    """展示23种异火的获取方式。"""

    await send_reply(client_id, service.help_info(client_id), ws_manager, service)
