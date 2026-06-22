"""修仙百科组件 WS 命令。"""

from __future__ import annotations

from launch import C, OnEvent, logger
from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@OnEvent.connect(priority=40)
async def start_encyclopedia() -> None:
    """服务启动时缓存修仙百科知识。"""

    entries = service.load()
    logger.opt(colors=True).info(f"{C.ok('执行 修仙百科 启动')} 知识 {len(entries)} 条")


@WsMessageHandler.handler(cmd="修仙百科", priority=100, block=True)
async def ws_xiuxian_encyclopedia(client_id: str, message: str) -> None:
    """查询修仙百科。"""

    await send_reply(client_id, service.ask(client_id, message), ws_manager, service)
