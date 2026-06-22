import json
import asyncio
import websockets
from uuid import uuid4
from typing import Any, Dict
from contextlib import suppress
from dataclasses import dataclass
from websockets.exceptions import ConnectionClosed

from .schema import loads_message, make_payload


@dataclass
class ClientState:
    """一个 client_id 对应一条 WebSocket 连接。"""

    websocket: Any
    queue: "asyncio.Queue[dict]"
    reader: "asyncio.Task"
    lock: "asyncio.Lock"


class WSClient:
    """WebSocket 测试客户端。

    这个类只做三件事：
    1. 第一次使用 client_id 时，连接 /ws/bot/{client_id}。
    2. 后续相同 client_id 直接复用已有连接。
    3. send(...) 发送一条消息，并等待一条回复。

    注意：当前 ws 驱动器用 code 判断通讯状态。
    code=202 表示通讯正常返回，code=404 表示通讯或协议错误，type 是客户端自定义分类。
    如果 message 没有命中已注册命令，服务端会立即返回 code=404，避免客户端一直等待。
    """

    def __init__(
        self,
        base_url: str = "ws://127.0.0.1:1234/ws/bot",
        #base_url: str = "ws://devalue-nylon-financial.ngrok-free.dev/ws/bot",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.clients: Dict[str, ClientState] = {}
        self._create_lock = asyncio.Lock()

    def _url(self, client_id: str) -> str:
        """生成当前 client_id 的连接地址。"""

        return f"{self.base_url}/{client_id}"

    async def new_client(self, client_id: str) -> ClientState:
        """获取连接；没有就新建，有就复用。"""

        client_id = str(client_id)
        state = self.clients.get(client_id)
        if state and self._is_usable(state):
            return state

        async with self._create_lock:
            state = self.clients.get(client_id)
            if state and self._is_usable(state):
                return state

            if state:
                await self._remove_client(client_id, state)

            websocket = await websockets.connect(self._url(client_id))
            queue: "asyncio.Queue[dict]" = asyncio.Queue()
            reader = asyncio.create_task(self._read_loop(websocket, queue))

            state = ClientState(
                websocket=websocket,
                queue=queue,
                reader=reader,
                lock=asyncio.Lock(),
            )
            self.clients[client_id] = state
            return state

    async def send(self, client_id: str, data: Any) -> dict:
        """发送一条消息，并等待同 request_id 的服务端回复。"""

        client_id = str(client_id)
        request_id = self._request_id()
        request_payload = make_payload(data, request_id=request_id)
        payload = json.dumps(request_payload, ensure_ascii=False)

        for attempt in range(2):
            try:
                state = await self.new_client(client_id)
                async with state.lock:
                    self._clear_queue(state.queue)
                    await state.websocket.send(payload)
                    reply = await self._wait_reply(state.queue, request_id)

                    if reply.get("code") == 404 and state.reader.done():
                        await self._remove_client(client_id, state)

                    return reply
            except asyncio.TimeoutError:
                return {
                    "code": 404,
                    "type": "text",
                    "message": "等待回复超时：服务端可能没有返回任何消息",
                    "request_id": request_id,
                }
            except (ConnectionClosed, OSError, RuntimeError) as exc:
                state = self.clients.get(client_id)
                if state:
                    await self._remove_client(client_id, state)

                if attempt == 0:
                    continue

                return {
                    "code": 404,
                    "type": "text",
                    "message": f"WebSocket 发送失败: {exc}",
                    "request_id": request_id,
                }

        return {
            "code": 404,
            "type": "text",
            "message": "WebSocket 重连失败",
            "request_id": request_id,
        }

    async def close_all(self) -> None:
        """程序结束时关闭全部连接。"""

        states = list(self.clients.values())
        self.clients.clear()

        for state in states:
            state.reader.cancel()
            await state.websocket.close()

        await asyncio.gather(
            *(state.reader for state in states),
            return_exceptions=True,
        )

    async def _remove_client(self, client_id: str, state: ClientState) -> None:
        """移除一条失效连接；只移除当前对象，避免误删新连接。"""

        if self.clients.get(client_id) is state:
            self.clients.pop(client_id, None)

        state.reader.cancel()
        with suppress(Exception):
            await state.websocket.close()
        await asyncio.gather(state.reader, return_exceptions=True)

    @staticmethod
    def _is_usable(state: ClientState) -> bool:
        """读任务还活着，说明这条连接仍可尝试复用。"""

        return not state.reader.done()

    async def _read_loop(self, websocket: Any, queue: "asyncio.Queue[dict]") -> None:
        """持续读取服务端推送，并放入队列。"""

        try:
            async for text in websocket:
                queue.put_nowait(loads_message(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(
                {
                    "code": 404,
                    "type": "text",
                    "message": f"WebSocket 读取失败: {exc}",
                    "request_id": "__connection_error__",
                }
            )

    async def _wait_reply(
        self,
        queue: "asyncio.Queue[dict]",
        request_id: str,
    ) -> dict:
        """只等待同一个 request_id 的回复，其它消息一律忽略。"""

        deadline = asyncio.get_running_loop().time() + self.timeout

        while True:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                raise asyncio.TimeoutError

            reply = await asyncio.wait_for(queue.get(), timeout=left)
            reply_request_id = reply.get("request_id")
            if reply_request_id == request_id:
                return reply

            if reply_request_id == "__connection_error__":
                return {
                    **reply,
                    "request_id": request_id,
                }

    @staticmethod
    def _clear_queue(queue: "asyncio.Queue[dict]") -> None:
        """发送前清理旧回复，避免读到上一轮残留。"""

        while not queue.empty():
            queue.get_nowait()

    @staticmethod
    def _request_id() -> str:
        """生成本次发送的 request_id。"""

        return uuid4().hex


client = WSClient()