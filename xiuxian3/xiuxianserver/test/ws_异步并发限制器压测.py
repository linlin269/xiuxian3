"""WebSocket 异步并发限制器压测。

运行方式：

    python test/ws_异步并发限制器压测.py

这个脚本不启动真实 WebSocket 服务，只压测 ws 驱动器后台任务层：

1. _create_message_task 会先做单用户排队限制。
2. 然后进入全局 TaskLimiter，也就是本次要比较的异步并发限制器。
3. 触发器 dispatch 用假的慢任务替代，只睡眠一小段时间并回复 ok。

统计里的“丢失”指客户端发出了 request_id，但服务端没有任何回复。
“服务繁忙”和“操作太快”都是驱动器主动回复，不算丢失。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from launch.adapter.ws import message as ws_message_module
from launch.adapter.ws.handler import WsMessageHandler
from launch.adapter.ws.manager import current_request_id
from launch.adapter.ws.rule import TaskLimiter
from launch.adapter.ws.schema import make_payload


class FakeManager:
    """收集 ws 驱动器发给客户端的回复。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, message: Any, client_id: str, **kwargs: Any) -> bool:
        request_id = kwargs.get("request_id") or current_request_id.get()
        self.sent.append((client_id, make_payload(message, request_id=request_id)))
        return True


class NoLimitTaskLimiter:
    """关闭全局异步并发限制器时使用。

    reserve 永远成功，run 不经过 Semaphore，所有后台任务会直接并发执行。
    """

    async def reserve(self) -> bool:
        return True

    async def release_reserved(self) -> None:
        return None

    async def run(self, func):
        return await func()


class QuietLogger:
    """压测时静音驱动器日志，只保留最终统计。"""

    def opt(self, **_: Any):
        return self

    def success(self, *_: Any, **__: Any) -> None:
        return None

    def warning(self, *_: Any, **__: Any) -> None:
        return None

    def error(self, *_: Any, **__: Any) -> None:
        return None


@dataclass
class BenchResult:
    """一次压测的统计结果。"""

    name: str
    total: int
    replied: int
    lost: int
    ok: int
    busy: int
    too_fast: int
    timeout: int
    other: int
    seconds: float
    max_active: int


async def run_case(
    *,
    name: str,
    total: int,
    client_count: int,
    dispatch_delay: float,
    limiter: Any,
    timeout: float,
) -> BenchResult:
    """运行一组压测。

    total: 总请求数。
    client_count: client_id 数量；等于 1 时模拟同一个用户手快连发。
    dispatch_delay: 假触发器单次耗时。
    limiter: TaskLimiter 或 NoLimitTaskLimiter。
    timeout: ws 后台任务超时时间。
    """

    old_manager = ws_message_module.manager
    old_limiter = ws_message_module.task_limiter
    old_timeout = ws_message_module.WS_MESSAGE_TASK_TIMEOUT
    old_background_tasks = ws_message_module.background_tasks
    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts
    old_dispatch = WsMessageHandler.dispatch
    old_logger = ws_message_module.logger

    fake_manager = FakeManager()
    active = 0
    max_active = 0

    async def fake_dispatch(client_id: str, message_data: dict[str, Any], manager: FakeManager, **_: Any) -> bool:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(dispatch_delay)
            await manager.send(
                {
                    "code": 202,
                    "type": "text",
                    "message": "ok",
                },
                client_id,
            )
            return True
        finally:
            active -= 1

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.task_limiter = limiter
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = timeout
        ws_message_module.background_tasks = set()
        ws_message_module.client_task_locks = {}
        ws_message_module.client_task_counts = {}
        ws_message_module.logger = QuietLogger()
        WsMessageHandler.dispatch = fake_dispatch

        started = perf_counter()
        for index in range(total):
            client_id = f"user_{index % client_count}"
            await ws_message_module._create_message_task(
                client_id,
                {
                    "code": 202,
                    "type": "text",
                    "message": "压测",
                    "request_id": f"{name}-{index}",
                },
            )

        tasks = list(ws_message_module.background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        seconds = perf_counter() - started

        replies = {reply.get("request_id"): reply for _client_id, reply in fake_manager.sent}
        lost = total - len(replies)
        messages = [str(reply.get("message", "")) for reply in replies.values()]
        ok = sum(message == "ok" for message in messages)
        busy = sum("服务繁忙" in message for message in messages)
        too_fast = sum("操作太快" in message for message in messages)
        timeout_count = sum("超时" in message for message in messages)
        other = len(replies) - ok - busy - too_fast - timeout_count

        return BenchResult(
            name=name,
            total=total,
            replied=len(replies),
            lost=lost,
            ok=ok,
            busy=busy,
            too_fast=too_fast,
            timeout=timeout_count,
            other=other,
            seconds=seconds,
            max_active=max_active,
        )
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.task_limiter = old_limiter
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = old_timeout
        ws_message_module.background_tasks = old_background_tasks
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts
        ws_message_module.logger = old_logger
        WsMessageHandler.dispatch = old_dispatch


def print_result(result: BenchResult) -> None:
    """打印一行压测结果。"""

    print(
        f"{result.name:<24} "
        f"total={result.total:<6} "
        f"reply={result.replied:<6} "
        f"lost={result.lost:<4} "
        f"ok={result.ok:<6} "
        f"busy={result.busy:<6} "
        f"too_fast={result.too_fast:<5} "
        f"timeout={result.timeout:<5} "
        f"other={result.other:<4} "
        f"max_active={result.max_active:<6} "
        f"seconds={result.seconds:.3f}"
    )


async def main_async() -> None:
    """分别压测开启和关闭全局异步并发限制器。"""

    cases = [
        await run_case(
            name="limit_many_clients",
            total=8000,
            client_count=8000,
            dispatch_delay=0.02,
            limiter=TaskLimiter(max_concurrent=1000, max_waiting=5000),
            timeout=3.0,
        ),
        await run_case(
            name="nolimit_many_clients",
            total=8000,
            client_count=8000,
            dispatch_delay=0.02,
            limiter=NoLimitTaskLimiter(),
            timeout=3.0,
        ),
        await run_case(
            name="limit_same_client",
            total=200,
            client_count=1,
            dispatch_delay=0.05,
            limiter=TaskLimiter(max_concurrent=1000, max_waiting=5000),
            timeout=3.0,
        ),
        await run_case(
            name="nolimit_same_client",
            total=200,
            client_count=1,
            dispatch_delay=0.05,
            limiter=NoLimitTaskLimiter(),
            timeout=3.0,
        ),
    ]

    print("WS 异步并发限制器压测结果")
    print("说明：lost 是无回复丢失；busy/too_fast 是驱动器主动回复，不算丢失。")
    for case in cases:
        print_result(case)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
