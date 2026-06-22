"""宗门组件 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from . import scheduler  # noqa: F401
from .service import service


@WsMessageHandler.handler(cmd="宗门", priority=100, block=True)
async def ws_sect_overview(client_id: str, message: str) -> None:
    """查看宗门信息。"""

    await send_reply(client_id, service.overview(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="建立宗门", priority=100, block=True)
async def ws_create_sect(client_id: str, message: str) -> None:
    """建立宗门。"""

    await send_reply(client_id, service.create(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="加入宗门", priority=100, block=True)
async def ws_join_sect(client_id: str, message: str) -> None:
    """加入当前位置宗门。"""

    await send_reply(client_id, service.join(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="退出宗门", priority=100, block=True)
async def ws_quit_sect(client_id: str, message: str) -> None:
    """退出当前宗门。"""

    await send_reply(client_id, service.quit(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="宗门战", priority=100, block=True)
async def ws_sect_war(client_id: str, message: str) -> None:
    """查看宗门战。"""

    await send_reply(client_id, service.war(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="领取宗门战奖励", priority=100, block=True)
async def ws_claim_sect_war_reward(client_id: str, message: str) -> None:
    """领取宗门战奖励。"""

    await send_reply(client_id, service.claim_war_reward(client_id), ws_manager, service)
