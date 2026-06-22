import json
import asyncio
from contextvars import ContextVar
from typing import Dict, Optional
from fastapi import WebSocket, status

from launch.log import C, logger
from .schema import make_payload


current_request_id: ContextVar[Optional[str]] = ContextVar(
    "ws_current_request_id",
    default=None,
)


class ConnectionManager:
    """WebSocket 连接管理器。

    每个连接都由 URL 中的 client_id 标识：
    ws://127.0.0.1:7001/ws/bot/{client_id}

    常用方法：
    - connect: 接受连接，并记录为该 client_id 的当前连接。
    - disconnect: 清理指定 client_id 的当前连接。
    - send: 给指定 client_id 的当前连接发送消息。
    - close_all: 关闭所有连接。
    """

    def __init__(self):
        # 保存当前在线连接；一个 client_id 只保留最后一次接入的 WebSocket。
        self.active_connections: Dict[str, WebSocket] = {}

        # 每条连接一个发送锁，避免多个后台任务同时写同一个 WebSocket。
        self._send_locks: Dict[WebSocket, asyncio.Lock] = {}

        # 多个连接可能同时进入/断开，用锁保护 active_connections。
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """接受连接，并用 client_id 保存。

        同一个 client_id 重复连接时，新连接会接管身份。
        旧连接会被关闭，避免同一个用户出现多个实时会话。
        """

        await websocket.accept()

        async with self._lock:
            old_websocket = self.active_connections.get(client_id)
            self.active_connections[client_id] = websocket
            self._send_locks.setdefault(websocket, asyncio.Lock())
            if old_websocket is not None and old_websocket is not websocket:
                self._send_locks.pop(old_websocket, None)

        if old_websocket is not None and old_websocket is not websocket:
            await self._close_websocket(
                old_websocket,
                code=status.WS_1000_NORMAL_CLOSURE,
                reason="同 client_id 新连接已接管",
            )

    async def disconnect(
        self,
        client_id: str,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        """清理指定 client_id 的连接。

        websocket 不为空时，只清理同一个对象。
        这样旧连接关闭时，不会误删刚接管的新连接。
        """

        async with self._lock:
            if websocket is None:
                current = self.active_connections.get(client_id)
                if current is not None:
                    self._send_locks.pop(current, None)
                self.active_connections.pop(client_id, None)
                return

            self._send_locks.pop(websocket, None)
            current = self.active_connections.get(client_id)
            if current is not websocket:
                return

            self.active_connections.pop(client_id, None)

    async def send(
        self,
        message: object,
        client_id: str,
        is_log: Optional[bool] = True,
        request_id: Optional[object] = None,
    ) -> bool:
        """给指定 client_id 发送 WS 消息。

        只发送给该 client_id 当前保留的最后一条连接。
        request_id 不传时，会自动使用当前请求上下文里的 request_id。
        返回 True 表示发送成功。
        返回 False 表示连接不存在或发送失败。
        """

        async with self._lock:
            websocket = self.active_connections.get(client_id)

        if websocket is None:
            if is_log:
                logger.opt(colors=True).warning(
                    f"{C.warn('发送消息失败，连接不存在')} {C.kv('client', client_id)}"
                )
            return False

        current_id = current_request_id.get() if request_id is None else request_id
        data = json.dumps(make_payload(message, request_id=current_id), ensure_ascii=False)
        success = await self._send_text(websocket, client_id, data)

        if is_log and success:
            logger.opt(colors=True).success(
                f"{C.ok('发送消息')} "
                f"{C.kv('client', client_id)} "
                f"{C.kv('success', success)} "
                f"{C.kv('body', data)}"
            )
        elif is_log:
            logger.opt(colors=True).warning(
                f"{C.warn('发送消息失败')} "
                f"{C.kv('client', client_id)} "
                f"{C.kv('body', data)}"
            )
        return success

    async def close_all(self) -> None:
        """关闭并清空所有 WebSocket 连接。"""

        async with self._lock:
            websockets = list(self.active_connections.values())
            self.active_connections.clear()
            self._send_locks.clear()

        await asyncio.gather(
            *(self._close_websocket(websocket) for websocket in websockets),
            return_exceptions=True,
        )

    async def _send_text(self, websocket: WebSocket, client_id: str, data: str) -> bool:
        """向单条连接发送文本；失败时只移除这一条连接。"""

        try:
            async with await self._get_send_lock(websocket):
                await websocket.send_text(data)
        except Exception as exc:
            await self.disconnect(client_id, websocket)
            logger.opt(colors=True, exception=exc).warning(
                f"{C.warn('发送消息失败，已断开')} {C.kv('client', client_id)}"
            )
            return False
        return True

    async def _get_send_lock(self, websocket: WebSocket) -> asyncio.Lock:
        """获取单条连接的发送锁；连接还在时会复用同一把锁。"""

        async with self._lock:
            return self._send_locks.setdefault(websocket, asyncio.Lock())

    @staticmethod
    async def _close_websocket(
        websocket: WebSocket,
        code: int = status.WS_1000_NORMAL_CLOSURE,
        reason: str = "",
    ) -> None:
        """关闭单条连接；关闭失败直接忽略。"""

        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass


manager = ConnectionManager()
