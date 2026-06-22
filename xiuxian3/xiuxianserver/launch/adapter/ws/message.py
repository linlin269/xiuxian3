import json
import asyncio
from html import unescape
import re
from collections import deque
from time import monotonic
from typing import Any, Deque, Dict, Optional, Tuple
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .manager import current_request_id, manager
from launch.log import C, logger
from .schema import is_ws_code
from .handler import WsMessageHandler
from .rule import RateLimiter, TaskLimiter

router = APIRouter()

# 通用 WebSocket 入口，通过 URL 中的 client_id 区分连接。
WS_ROUTE = "/ws/bot/{client_id}"

# WebSocket 消息后台任务最大并发数。
WS_MAX_CONCURRENT_TASKS = 1000

# WebSocket 消息后台任务最大排队数，超过后直接拒绝本次消息。
WS_MAX_WAITING_TASKS = 5000

# 单个 client_id 最多保留的待处理消息数。
# 同一用户的消息会串行执行，手快连发时超过这个数量就拒绝，避免单个用户拖住太多资源。
WS_CLIENT_MAX_WAITING_TASKS = 5

# WebSocket 消息后台任务超时时间，超过后取消本次触发器分发。
# 比测试客户端 WSClient 的默认 10 秒略短，避免客户端先自己超时。
WS_MESSAGE_TASK_TIMEOUT = 9.0

# WebSocket 连接频率限制。
WS_CONNECT_LIMIT = 1000
WS_CONNECT_WINDOW_SECONDS = 60

# request_id 短期幂等保护时间。
# 客户端重连/重发通常发生在几十秒内，保留 2 分钟足够挡住重复投递。
WS_REQUEST_ID_TTL_SECONDS = 120.0

# 保存后台任务引用，避免任务还没执行完就被垃圾回收。
background_tasks = set()

# 每个 client_id 一把锁，保证同一个用户的命令按收到顺序串行处理。
client_task_locks: Dict[str, asyncio.Lock] = {}

# 记录每个 client_id 当前已经预占的任务数，用于限制单用户刷屏。
client_task_counts: Dict[str, int] = {}

# 保护 client_task_locks / client_task_counts。
client_task_guard = asyncio.Lock()

# 记录近期已经处理过的 request_id。
# key 是 (client_id, request_id)，保证不同用户可以使用自己的 request_id。
request_id_records: Dict[Tuple[str, str], float] = {}

# 按写入顺序保存 request_id，清理过期记录时只从队首弹出。
request_id_order: Deque[Tuple[float, Tuple[str, str]]] = deque()

# 保护 request_id_records / request_id_order。
request_id_guard = asyncio.Lock()

# 所有正常消息共用一个并发限制器。
task_limiter = TaskLimiter(
    max_concurrent=WS_MAX_CONCURRENT_TASKS,
    max_waiting=WS_MAX_WAITING_TASKS,
)

# 平台 at 码：只取 qq 的值作为内部 client_id。
# 写得宽松一些，兼容不同系统/客户端可能出现的大小写和字段空格：
#   [CQ:at,qq=abc]
#   [cq:at, qq=abc]
#   [CQ:at,qq=abc,name=xxx]
CQ_AT_PATTERN = re.compile(
    r"\[\s*cq\s*:\s*at\s*,\s*qq\s*=\s*([^,\]\s]+)[^\]]*\]",
    re.IGNORECASE,
)


@router.websocket(WS_ROUTE)
@RateLimiter.websocket(limit=WS_CONNECT_LIMIT, window=WS_CONNECT_WINDOW_SECONDS)
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    """WebSocket 连接入口。

    主要流程：
    1. 接受连接，并用 client_id 保存到 manager。
    2. 持续接收客户端文本。
    3. 解析 JSON。
    4. code == 202 时交给 WsMessageHandler.dispatch 做命令分发。
    5. 连接断开后清理当前 client_id。
    """

    await manager.connect(websocket, client_id)

    try:
        while True:
            data = await websocket.receive_text()
            await _handle_raw_message(client_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(client_id, websocket)


async def _handle_raw_message(client_id: str, data: str) -> None:
    """解析客户端原始文本；JSON 无效时只回复当前客户端。"""

    message_data = _loads_message(data)
    if message_data is None:
        await manager.send(
            {
                "code": 404,
                "type": "text",
                "message": "Invalid JSON format",
            },
            client_id,
            is_log=False,
        )
        return

    await _dispatch_message(client_id, message_data)


def _loads_message(data: str) -> Optional[Dict[str, Any]]:
    """把客户端文本解析成 dict；失败时返回 None。

    JSON 成功解析后，立刻把 message 里的 CQ/at 转成内部 client_id。
    后面的匹配和业务分发只处理普通文本，不再理解 CQ 格式。
    """

    try:
        message_data = json.loads(data.lstrip("\ufeff"))
    except json.JSONDecodeError:
        return None

    if not isinstance(message_data, dict):
        return None

    if "message" in message_data:
        message_data["message"] = _normalize_message_text(message_data.get("message"))

    return message_data


def _normalize_message_text(message: Any) -> str:
    """把客户端 message 统一成业务层好处理的普通文本。

    只做三件事：
    1. 非字符串也转成字符串，避免不同客户端传数字/None 时出错。
    2. 把 HTML 转义还原，例如 &#91;CQ:at,qq=abc&#93;。
    3. 把 CQ/at 替换成前后带空格的 id，再压平多余空白。
    """

    text = "" if message is None else str(message)
    text = unescape(text).replace("\ufeff", "")
    text = CQ_AT_PATTERN.sub(lambda match: f" {match.group(1).strip()} ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _dispatch_message(client_id: str, message_data: Dict[str, Any]) -> None:
    """分发客户端消息；客户端请求必须使用 code=202。"""

    protocol_error = _validate_message_data(message_data)
    if protocol_error:
        await _send_protocol_error(client_id, protocol_error, message_data)
        return

    logger.opt(colors=True).success(f"{C.yellow('收到消息')} {C.kv('client', client_id)} {C.kv('body', message_data)}")
    if not await _remember_request_once(client_id, message_data):
        await _send_duplicate_request(client_id, message_data)
        return

    if not await WsMessageHandler.has_match(message_data):
        await _send_unmatched_message(client_id, message_data)
        return

    await _create_message_task(client_id, message_data)

def _validate_message_data(message_data: Dict[str, Any]) -> Optional[str]:
    """校验新的 WS 通讯格式；格式不对就不进入业务分发。

    新格式固定为：
    {
        "code": 202,
        "type": "text",
        "message": "命令内容",
        "request_id": "本次请求唯一 id"
    }

    request_id 是客户端等待回复的凭证，所以正常消息必须携带。
    """

    for field in ("code", "type", "message"):
        if field not in message_data:
            return f"WS 消息缺少 {field}"

    if not is_ws_code(message_data["code"]):
        return "WS 消息 code 必须是整数 202 或 404"

    if message_data["code"] != 202:
        return "客户端请求 code 必须为 202"

    if not str(message_data.get("type") or "").strip():
        return "WS 消息 type 不能为空"

    if not _message_request_id(message_data):
        return "WS 消息缺少 request_id"

    return None


async def _send_protocol_error(
    client_id: str,
    message: str,
    message_data: Dict[str, Any],
) -> None:
    """把协议错误回复给当前连接；能读到 request_id 时就原样带回。"""

    logger.opt(colors=True).warning(
        f"{C.warn('WS 消息格式错误')} {C.kv('client', client_id)} {C.kv('body', message_data)}"
    )
    await manager.send(
        {
            "code": 404,
            "type": "text",
            "message": message,
        },
        client_id,
        is_log=False,
        request_id=_message_request_id(message_data),
    )


async def _send_unmatched_message(client_id: str, message_data: Dict[str, Any]) -> None:
    """命令未命中时立刻结束本次请求，不进入后台队列。"""

    await manager.send(
        {
            "code": 404,
            "type": "text",
            "message": "未命中任何触发器",
        },
        client_id,
        is_log=False,
        request_id=_message_request_id(message_data),
    )


async def _send_duplicate_request(client_id: str, message_data: Dict[str, Any]) -> None:
    """同一个 client_id 重复提交同一个 request_id 时直接返回。"""

    await manager.send(
        {
            "code": 202,
            "type": "text",
            "message": "请求已处理，请勿重复提交。",
        },
        client_id,
        is_log=False,
        request_id=_message_request_id(message_data),
    )


async def _create_message_task(client_id: str, message_data: Dict[str, Any]) -> None:
    """创建后台任务，避免阻塞 WebSocket 接收循环。"""

    if not await _reserve_client_task(client_id):
        logger.opt(colors=True).warning(
            f"{C.warn('WebSocket 单用户消息排队已满')} "
            f"{C.kv('client', client_id)} "
            f"{C.kv('max_waiting', WS_CLIENT_MAX_WAITING_TASKS)}"
        )
        await manager.send(
            {
                "code": 202,
                "type": "text",
                "message": "操作太快，请稍后再试",
            },
            client_id,
            is_log=False,
            request_id=_message_request_id(message_data),
        )
        return

    if not await task_limiter.reserve():
        await _release_client_task(client_id)
        logger.opt(colors=True).warning(
            f"{C.warn('WebSocket 后台任务排队已满')} "
            f"{C.kv('client', client_id)} "
            f"{C.kv('max_waiting', WS_MAX_WAITING_TASKS)}"
        )
        await manager.send(
            {
                "code": 202,
                "type": "text",
                "message": "服务繁忙，请稍后再试",
            },
            client_id,
            is_log=False,
            request_id=_message_request_id(message_data),
        )
        return

    task = asyncio.create_task(_run_message_task(client_id, message_data))
    background_tasks.add(task)
    task.add_done_callback(_on_message_task_done)


async def _run_message_task(client_id: str, message_data: Dict[str, Any]) -> None:
    """在并发限制和超时保护内执行一次消息分发。

    wait_for 包住的是 task_limiter.run(...) 整体：
    - 同一个 client_id 会先等自己的串行锁，避免同用户命令乱序。
    - 如果任务还在等待串行锁或全局并发名额，超时后会释放预占名额。
    - 如果任务已经进入 dispatch，超时后会取消当前触发器协程。
    """

    task_limiter_started = False
    request_token = current_request_id.set(_message_request_id(message_data))

    async def dispatch_with_limit() -> Optional[bool]:
        nonlocal task_limiter_started
        client_lock = await _get_client_task_lock(client_id)
        async with client_lock:
            task_limiter_started = True
            return await task_limiter.run(
                lambda: WsMessageHandler.dispatch(
                    client_id=client_id,
                    message_data=message_data,
                    manager=manager,
                )
            )

    try:
        matched = await asyncio.wait_for(
            dispatch_with_limit(),
            timeout=WS_MESSAGE_TASK_TIMEOUT,
        )
        if matched is False:
            await manager.send(
                {
                    "code": 404,
                    "type": "text",
                    "message": "未命中任何触发器",
                },
                client_id,
                is_log=False,
            )
    except asyncio.TimeoutError:
        logger.opt(colors=True).warning(
            f"{C.warn('WebSocket 后台任务超时，已终止')} "
            f"{C.kv('client', client_id)} "
            f"{C.kv('timeout', WS_MESSAGE_TASK_TIMEOUT)}"
        )
        await manager.send(
            {
                "code": 202,
                "type": "text",
                "message": "触发器执行超时，已终止本次任务",
            },
            client_id,
            is_log=False,
        )
    finally:
        current_request_id.reset(request_token)
        if not task_limiter_started:
            await task_limiter.release_reserved()
        await _release_client_task(client_id)


def _message_request_id(message_data: Dict[str, Any]) -> Optional[str]:
    """读取本次消息的 request_id；没有就返回 None。"""

    request_id = message_data.get("request_id")
    if request_id is None:
        return None

    value = str(request_id).strip()
    return value or None


async def _remember_request_once(client_id: str, message_data: Dict[str, Any]) -> bool:
    """记录本次 request_id，重复请求返回 False。

    这是 WS 通讯层的短期幂等保护：
    - 只使用 client_id + request_id 判断重复。
    - 不写业务数据库，也不要求业务函数传递 request_id。
    - 过期记录会在后续请求到来时顺手清理。
    """

    request_id = _message_request_id(message_data)
    if request_id is None:
        return True

    now_value = monotonic()
    key = (client_id, request_id)
    async with request_id_guard:
        _clear_expired_request_ids(now_value)
        if key in request_id_records:
            return False

        request_id_records[key] = now_value
        request_id_order.append((now_value, key))
        return True


def _clear_expired_request_ids(now_value: float) -> None:
    """清理过期 request_id，避免内存一直增长。"""

    expires_before = now_value - WS_REQUEST_ID_TTL_SECONDS
    while request_id_order and request_id_order[0][0] <= expires_before:
        created_at, key = request_id_order.popleft()
        if request_id_records.get(key) == created_at:
            request_id_records.pop(key, None)


async def _reserve_client_task(client_id: str) -> bool:
    """为单个 client_id 预占一个待处理名额。"""

    async with client_task_guard:
        count = client_task_counts.get(client_id, 0)
        if count >= WS_CLIENT_MAX_WAITING_TASKS:
            return False

        client_task_counts[client_id] = count + 1
        client_task_locks.setdefault(client_id, asyncio.Lock())
        return True


async def _release_client_task(client_id: str) -> None:
    """释放单个 client_id 的待处理名额。"""

    async with client_task_guard:
        count = client_task_counts.get(client_id, 0) - 1
        if count > 0:
            client_task_counts[client_id] = count
            return

        client_task_counts.pop(client_id, None)
        client_task_locks.pop(client_id, None)


async def _get_client_task_lock(client_id: str) -> asyncio.Lock:
    """读取当前 client_id 的串行锁。"""

    async with client_task_guard:
        return client_task_locks.setdefault(client_id, asyncio.Lock())


async def _handle_message_task_result(task: asyncio.Task) -> None:
    """后台任务结束后的清理和异常日志。"""

    background_tasks.discard(task)

    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.opt(colors=True, exception=exc).error(f"{C.fail('WebSocket 后台任务异常')}")


def _on_message_task_done(task: asyncio.Task) -> None:
    """把 asyncio 的同步 done_callback 转到异步清理函数。"""

    asyncio.create_task(_handle_message_task_result(task))


async def shutdown() -> None:
    """取消后台任务，并关闭所有 WebSocket 连接。"""

    tasks = list(background_tasks)
    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    async with request_id_guard:
        request_id_records.clear()
        request_id_order.clear()

    await manager.close_all()
