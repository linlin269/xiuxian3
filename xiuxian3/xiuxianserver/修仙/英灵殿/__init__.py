"""英灵殿模块 WS 命令注册 + 定时刷新任务。"""

from __future__ import annotations

from launch import Scheduler
from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service

# service 实例在 service.py 底部已创建为 service = HallOfHeroesService(db)


@WsMessageHandler.handler(cmd="英灵殿", priority=100, block=True)
async def ws_hall_of_heroes(client_id: str, message: str) -> None:
    """英灵殿概览。"""

    await send_reply(client_id, service.overview(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("英灵殿列表", "英灵"), priority=100, block=True)
async def ws_hall_npc_list(client_id: str, message: str) -> None:
    """英灵殿 NPC 列表。"""

    await send_reply(client_id, service.npc_list(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="挑战英灵", priority=100, block=True)
async def ws_challenge_npc(client_id: str, message: str) -> None:
    """挑战英灵 NPC。"""

    await send_reply(client_id, service.challenge(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="英灵殿记录", priority=100, block=True)
async def ws_hall_records(client_id: str, message: str) -> None:
    """英灵殿挑战记录。"""

    await send_reply(client_id, service.records(client_id), ws_manager, service)


@Scheduler._async("cron", minute=0, id="hall_of_heroes_refresh_npcs")
async def refresh_npcs():
    """每小时整点刷新英灵殿 NPC。"""
    service.refresh_npcs()
