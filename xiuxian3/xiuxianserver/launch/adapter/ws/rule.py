import asyncio
from functools import wraps
from inspect import signature
from collections import deque
from fastapi import WebSocket, status
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Deque, Dict, Optional


class TaskLimiter:
    """异步任务并发限制器。

    用 Semaphore 控制同时执行的 chat 后台任务数量。
    用 max_waiting 控制排队任务数量，避免瞬时消息过多时无限堆内存。
    """

    def __init__(
        self,
        max_concurrent: Optional[int] = 100,
        max_waiting: Optional[int] = None,
    ):
        max_concurrent = max_concurrent or 100
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_waiting = max_waiting or max_concurrent * 5
        self._waiting = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> bool:
        """预占一个排队名额。

        返回 False 表示排队已满，调用方应该直接拒绝本次消息。
        """

        async with self._lock:
            if self._waiting >= self.max_waiting:
                return False

            self._waiting += 1
            return True

    async def release_reserved(self) -> None:
        """释放一个已经预占、但还没进入 run(...) 的排队名额。"""

        async with self._lock:
            if self._waiting > 0:
                self._waiting -= 1

    async def run(self, func: Callable[[], Awaitable]):
        """在并发限制内执行一个已预占名额的协程函数。

        传入函数而不是已经创建好的协程，是为了避免任务排队等待时产生未 await 的协程。
        """

        try:
            async with self.semaphore:
                return await func()
        finally:
            async with self._lock:
                self._waiting -= 1


class RateLimiter:
    """WebSocket 连接频率限制器。

    按 client_id 记录最近 window 秒内的连接时间。
    同一个 client_id 超过 limit 次就拒绝连接，不影响其他 client_id。
    """

    websocket_connected: Dict[str, Deque[datetime]] = {}

    @staticmethod
    def websocket(
        limit: int = 5,
        window: int = 60,
        key_param: str = "client_id",
    ):
        """限制 WebSocket 连接频率。

        limit: 时间窗口内允许的最大连接次数。
        window: 时间窗口，单位秒。
        key_param: 用哪个参数分组限流，默认按 client_id。
        """

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(websocket: WebSocket, *args, **kwargs):
                client_key = RateLimiter._get_limit_key(
                    func,
                    websocket,
                    key_param,
                    args,
                    kwargs,
                )

                if await RateLimiter._is_limited(client_key, limit, window):
                    await websocket.close(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason="连接过于频繁",
                    )
                    return

                return await func(websocket, *args, **kwargs)

            return wrapper

        return decorator

    @staticmethod
    async def _is_limited(client_key: str, limit: int, window: int) -> bool:
        """记录本次连接，并判断是否超出限制。"""

        now = datetime.now()
        expires_before = now - timedelta(seconds=window)
        records = RateLimiter.websocket_connected.setdefault(client_key, deque())

        while records and records[0] < expires_before:
            records.popleft()

        if len(records) >= limit:
            return True

        records.append(now)
        return False

    @staticmethod
    def _get_limit_key(
        func: Callable,
        websocket: WebSocket,
        key_param: str,
        args: tuple,
        kwargs: dict,
    ) -> str:
        """从路由参数里取限流 key，取不到时退回到客户端 IP。"""

        if key_param in kwargs:
            return str(kwargs[key_param])

        try:
            bound = signature(func).bind_partial(websocket, *args, **kwargs)
            if key_param in bound.arguments:
                return str(bound.arguments[key_param])
        except TypeError:
            pass

        if websocket.client:
            return websocket.client.host
        return "unknown"
