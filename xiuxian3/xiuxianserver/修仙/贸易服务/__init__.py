"""贸易服务交易、出售和导航 WS 命令。"""

from __future__ import annotations

from launch.adapter.ws import WsMessageHandler, manager as ws_manager

from ..reply import send_reply
from .service import service


@WsMessageHandler.handler(cmd="商场行情", priority=100, block=True)
async def ws_trade_price(client_id: str, message: str) -> None:
    """查看商品市价。"""

    await send_reply(client_id, service.market_price(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="商场购买", priority=100, block=True)
async def ws_trade_buy(client_id: str, message: str) -> None:
    """购买跑商商品。"""

    await send_reply(client_id, service.buy(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="商场出售", priority=100, block=True)
async def ws_trade_sell(client_id: str, message: str) -> None:
    """出售跑商商品。"""

    await send_reply(client_id, service.sell(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="出售", priority=100, block=True)
async def ws_sell_any(client_id: str, message: str) -> None:
    """统一出售指定物品。"""

    await send_reply(client_id, service.sell_any(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="自动出售", priority=100, block=True)
async def ws_auto_sell(client_id: str, message: str) -> None:
    """自动清空背包可流通物品。"""

    await send_reply(client_id, service.auto_sell(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="出售全部", priority=100, block=True)
async def ws_sell_all(client_id: str, message: str) -> None:
    """批量出售纳戒资产。"""

    await send_reply(client_id, service.sell_all(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="藏宝图", priority=100, block=True)
async def ws_treasure_map(client_id: str, message: str) -> None:
    """查看当前位置或指定城池藏宝图。"""

    await send_reply(client_id, service.treasure_map(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="藏宝图出价", priority=100, block=True)
async def ws_treasure_bid(client_id: str, message: str) -> None:
    """给当前位置城池藏宝图出价。"""

    await send_reply(client_id, service.treasure_bid(client_id, message), ws_manager, service)


@WsMessageHandler.handler(cmd="领取藏宝图", priority=100, block=True)
async def ws_treasure_claim(client_id: str, message: str) -> None:
    """领取已归属或脚下的藏宝图。"""

    await send_reply(client_id, service.treasure_claim(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="商场推荐", priority=100, block=True)
async def ws_trade_recommend(client_id: str, message: str) -> None:
    """推荐跑商路线。"""

    await send_reply(client_id, service.recommend(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="跑商记录", priority=100, block=True)
async def ws_trade_records(client_id: str, message: str) -> None:
    """查看跑商记录。"""

    await send_reply(client_id, service.records(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="跑商限制", priority=100, block=True)
async def ws_trade_curve(client_id: str, message: str) -> None:
    """查看跑商收益曲线。"""

    await send_reply(client_id, service.trade_curve(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd="跑商奖励", priority=100, block=True)
async def ws_trade_daily_reward(client_id: str, message: str) -> None:
    """领取每日跑商奖励。"""

    await send_reply(client_id, service.daily_reward(client_id), ws_manager, service)


@WsMessageHandler.handler(cmd=("导航", "去", "来"), priority=100, block=True)
async def ws_trade_navigate(client_id: str, message: str) -> None:
    """移动到指定地点。"""

    await send_reply(client_id, service.navigate(client_id, message), ws_manager, service)
