"""修仙界历史 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="风云榜", priority=100, block=True)
async def ws_history_leaderboard(client_id: str, message: str) -> None:
    """查看风云榜。"""

    await send_reply(client_id, service.leaderboard(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="修仙早报", priority=100, block=True)
async def ws_history_morning_paper(client_id: str, message: str) -> None:
    """查看今日修仙早报。"""

    await send_reply(client_id, service.newspaper(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="修仙界历史", priority=100, block=True)
async def ws_history_chronicle(client_id: str, message: str) -> None:
    """查看最近修仙界历史。"""

    await send_reply(client_id, service.chronicle(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="人物史榜", priority=100, block=True)
async def ws_history_player_volume(client_id: str, message: str) -> None:
    """查看人物史榜。"""

    await send_reply(client_id, service.history_volume(client_id, "人物史榜"), ws_manager, service)


@WsMessageHandler.handler(cmd="宗门史榜", priority=100, block=True)
async def ws_history_sect_volume(client_id: str, message: str) -> None:
    """查看宗门史榜。"""

    await send_reply(client_id, service.history_volume(client_id, "宗门史榜"), ws_manager, service)


@WsMessageHandler.handler(cmd="城池史榜", priority=100, block=True)
async def ws_history_city_volume(client_id: str, message: str) -> None:
    """查看城池史榜。"""

    await send_reply(client_id, service.history_volume(client_id, "城池史榜"), ws_manager, service)


@WsMessageHandler.handler(cmd="战斗名局", priority=100, block=True)
async def ws_history_battle_volume(client_id: str, message: str) -> None:
    """查看战斗名局。"""

    await send_reply(client_id, service.history_volume(client_id, "战斗名局"), ws_manager, service)


@WsMessageHandler.handler(cmd="商路奇闻", priority=100, block=True)
async def ws_history_trade_volume(client_id: str, message: str) -> None:
    """查看商路奇闻。"""

    await send_reply(client_id, service.history_volume(client_id, "商路奇闻"), ws_manager, service)


@WsMessageHandler.handler(cmd="异界虫洞录", priority=100, block=True)
async def ws_history_wormhole_volume(client_id: str, message: str) -> None:
    """查看异界虫洞录。"""

    await send_reply(client_id, service.history_volume(client_id, "异界虫洞录"), ws_manager, service)


@WsMessageHandler.handler(cmd="人物志", priority=100, block=True)
async def ws_history_profile(client_id: str, message: str, raw_message: str,message_data) -> None:
    """查看公开人物志。"""

    await send_reply(client_id, service.profile(client_id, message), ws_manager, service)
