"""WebSocket 后台任务超时测试。

运行方式：

    python test/ws_后台任务超时测试.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from launch.adapter.ws import message as ws_message_module
from launch.adapter.ws.handler import WsMessageHandler
from launch.adapter.ws.manager import current_request_id
from launch.adapter.ws.rule import TaskLimiter
from launch.adapter.ws.schema import make_payload


class QuietLogger:
    """测试协议错误路径时静音 WS 模块日志，避免写真实 runserver.log。"""

    def opt(self, **_: Any):
        return self

    def success(self, *_: Any, **__: Any) -> None:
        return None

    def warning(self, *_: Any, **__: Any) -> None:
        return None

    def error(self, *_: Any, **__: Any) -> None:
        return None


class FakeManager:
    """收集服务端发送给客户端的消息。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send(self, message: Any, client_id: str, **kwargs: Any) -> bool:
        request_id = kwargs.get("request_id")
        if request_id is None:
            request_id = current_request_id.get()

        self.sent.append((client_id, make_payload(message, request_id=request_id)))
        return True


async def main_async() -> None:
    """运行后台任务超时和同用户串行测试。"""

    old_logger = ws_message_module.logger
    try:
        ws_message_module.logger = QuietLogger()
        await _check_string_code_rejected()
        await _check_missing_request_id_rejected()
        await _check_unmatched_message_rejected_before_task()
        await _check_unmatched_message_replies_quickly()
        await _check_client_queue_full_replies_202()
        await _check_global_queue_full_replies_202()
        await _check_message_timeout()
        await _check_same_client_serial()
        await _check_client_waiting_limit()
    finally:
        ws_message_module.logger = old_logger


async def _check_string_code_rejected() -> None:
    """确认字符串 code 会被当成协议错误，不再做宽松转换。"""

    old_manager = ws_message_module.manager
    old_background_tasks = ws_message_module.background_tasks

    fake_manager = FakeManager()

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.background_tasks = set()

        await ws_message_module._dispatch_message(
            "strict_user",
            {
                "code": "202",
                "type": "text",
                "message": "你好",
                "request_id": "string-code-1",
            },
        )

        assert len(fake_manager.sent) == 1
        client_id, reply = fake_manager.sent[0]
        assert client_id == "strict_user"
        assert reply["code"] == 404
        assert reply["request_id"] == "string-code-1"
        assert "整数" in reply["message"]
        assert ws_message_module.background_tasks == set()
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.background_tasks = old_background_tasks


async def _check_missing_request_id_rejected() -> None:
    """确认正常消息缺少 request_id 时会被直接拒绝，不进入后台任务。"""

    old_manager = ws_message_module.manager
    old_background_tasks = ws_message_module.background_tasks

    fake_manager = FakeManager()

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.background_tasks = set()

        await ws_message_module._dispatch_message(
            "strict_user",
            {
                "code": 202,
                "type": "text",
                "message": "你好",
            },
        )

        assert len(fake_manager.sent) == 1
        client_id, reply = fake_manager.sent[0]
        assert client_id == "strict_user"
        assert reply["code"] == 404
        assert "request_id" not in reply
        assert "request_id" in reply["message"]
        assert ws_message_module.background_tasks == set()
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.background_tasks = old_background_tasks


async def _check_unmatched_message_rejected_before_task() -> None:
    """确认未命中消息在创建后台任务前就返回 404。"""

    old_manager = ws_message_module.manager
    old_background_tasks = ws_message_module.background_tasks
    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts
    old_has_match = WsMessageHandler.has_match

    fake_manager = FakeManager()

    async def no_match(_: dict) -> bool:
        return False

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.background_tasks = set()
        ws_message_module.client_task_locks = {}
        ws_message_module.client_task_counts = {}
        WsMessageHandler.has_match = no_match

        await ws_message_module._dispatch_message(
            "fast_no_match_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "fast-no-match-1",
                "message": "没有这个命令",
            },
        )

        assert len(fake_manager.sent) == 1
        client_id, reply = fake_manager.sent[0]
        assert client_id == "fast_no_match_user"
        assert reply["code"] == 404
        assert reply["request_id"] == "fast-no-match-1"
        assert "未命中" in reply["message"]
        assert ws_message_module.background_tasks == set()
        assert ws_message_module.client_task_counts == {}
        assert ws_message_module.client_task_locks == {}
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.background_tasks = old_background_tasks
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts
        WsMessageHandler.has_match = old_has_match


async def _check_unmatched_message_replies_quickly() -> None:
    """确认未命中任何触发器时会立即返回 404，避免客户端等到超时。"""

    old_timeout = ws_message_module.WS_MESSAGE_TASK_TIMEOUT
    old_manager = ws_message_module.manager
    old_limiter = ws_message_module.task_limiter
    old_dispatch = WsMessageHandler.dispatch

    fake_manager = FakeManager()

    async def no_match_dispatch(**_: Any) -> bool:
        return False

    try:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = 1.0
        ws_message_module.manager = fake_manager
        ws_message_module.task_limiter = TaskLimiter(max_concurrent=1, max_waiting=10)
        WsMessageHandler.dispatch = no_match_dispatch

        assert await ws_message_module.task_limiter.reserve()
        await ws_message_module._run_message_task(
            "no_match_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "no-match-1",
                "message": "没有这个命令",
            },
        )

        assert fake_manager.sent, "未命中时应该立即给客户端一个 404 回复"
        client_id, reply = fake_manager.sent[-1]
        assert client_id == "no_match_user"
        assert reply["code"] == 404
        assert reply["request_id"] == "no-match-1"
        assert "未命中" in reply["message"]
    finally:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = old_timeout
        ws_message_module.manager = old_manager
        ws_message_module.task_limiter = old_limiter
        WsMessageHandler.dispatch = old_dispatch


async def _check_client_queue_full_replies_202() -> None:
    """确认单用户消息排队满时返回 202/text 业务提示。"""

    old_manager = ws_message_module.manager
    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts

    fake_manager = FakeManager()

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.client_task_locks = {"busy_user": asyncio.Lock()}
        ws_message_module.client_task_counts = {
            "busy_user": ws_message_module.WS_CLIENT_MAX_WAITING_TASKS
        }

        await ws_message_module._create_message_task(
            "busy_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "client-full-1",
                "message": "任意命令",
            },
        )

        assert fake_manager.sent, "单用户排队满时应该立即回复"
        client_id, reply = fake_manager.sent[-1]
        assert client_id == "busy_user"
        assert reply["code"] == 202
        assert reply["request_id"] == "client-full-1"
        assert "操作太快" in reply["message"]
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts


async def _check_global_queue_full_replies_202() -> None:
    """确认全局后台任务队列满时返回 202/text 业务提示。"""

    class RejectLimiter:
        async def reserve(self) -> bool:
            return False

    old_manager = ws_message_module.manager
    old_limiter = ws_message_module.task_limiter
    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts

    fake_manager = FakeManager()

    try:
        ws_message_module.manager = fake_manager
        ws_message_module.task_limiter = RejectLimiter()
        ws_message_module.client_task_locks = {}
        ws_message_module.client_task_counts = {}

        await ws_message_module._create_message_task(
            "global_full_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "global-full-1",
                "message": "任意命令",
            },
        )

        assert fake_manager.sent, "全局队列满时应该立即回复"
        client_id, reply = fake_manager.sent[-1]
        assert client_id == "global_full_user"
        assert reply["code"] == 202
        assert reply["request_id"] == "global-full-1"
        assert "服务繁忙" in reply["message"]
        assert ws_message_module.client_task_counts == {}
    finally:
        ws_message_module.manager = old_manager
        ws_message_module.task_limiter = old_limiter
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts


async def _check_message_timeout() -> None:
    """确认后台任务超时后会取消触发器并释放任务名额。"""

    old_timeout = ws_message_module.WS_MESSAGE_TASK_TIMEOUT
    old_manager = ws_message_module.manager
    old_limiter = ws_message_module.task_limiter
    old_dispatch = WsMessageHandler.dispatch

    fake_manager = FakeManager()
    cancelled = False

    async def slow_dispatch(**_: Any) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    try:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = 0.05
        ws_message_module.manager = fake_manager
        ws_message_module.task_limiter = TaskLimiter(max_concurrent=1, max_waiting=10)
        WsMessageHandler.dispatch = slow_dispatch

        assert await ws_message_module.task_limiter.reserve()
        await ws_message_module._run_message_task(
            "timeout_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "timeout-1",
                "message": "慢命令",
            },
        )

        assert cancelled is True
        assert ws_message_module.task_limiter._waiting == 0
        assert fake_manager.sent, "超时后应该给客户端发送提示"

        client_id, reply = fake_manager.sent[-1]
        assert client_id == "timeout_user"
        assert reply["code"] == 202
        assert reply["request_id"] == "timeout-1"
        assert "超时" in reply["message"]
    finally:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = old_timeout
        ws_message_module.manager = old_manager
        ws_message_module.task_limiter = old_limiter
        WsMessageHandler.dispatch = old_dispatch


async def _check_same_client_serial() -> None:
    """确认同一个 client_id 的消息会按顺序串行执行。"""

    old_timeout = ws_message_module.WS_MESSAGE_TASK_TIMEOUT
    old_manager = ws_message_module.manager
    old_limiter = ws_message_module.task_limiter
    old_background_tasks = ws_message_module.background_tasks
    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts
    old_dispatch = WsMessageHandler.dispatch

    fake_manager = FakeManager()
    active = 0
    max_active = 0
    started: list[str] = []
    finished: list[str] = []

    async def slow_dispatch(client_id: str, message_data: dict, **_: Any) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.append(message_data["message"])
        await asyncio.sleep(0.05)
        await fake_manager.send(message_data["message"], client_id)
        finished.append(message_data["message"])
        active -= 1

    try:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = 1.0
        ws_message_module.manager = fake_manager
        ws_message_module.task_limiter = TaskLimiter(max_concurrent=10, max_waiting=10)
        ws_message_module.background_tasks = set()
        ws_message_module.client_task_locks = {}
        ws_message_module.client_task_counts = {}
        WsMessageHandler.dispatch = slow_dispatch

        await ws_message_module._create_message_task(
            "same_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "serial-1",
                "message": "第一条",
            },
        )
        await ws_message_module._create_message_task(
            "same_user",
            {
                "code": 202,
                "type": "text",
                "request_id": "serial-2",
                "message": "第二条",
            },
        )

        await asyncio.gather(*list(ws_message_module.background_tasks))

        assert max_active == 1, max_active
        assert started == ["第一条", "第二条"], started
        assert finished == ["第一条", "第二条"], finished
        assert [item[1]["request_id"] for item in fake_manager.sent] == ["serial-1", "serial-2"]
        assert ws_message_module.client_task_counts == {}
    finally:
        ws_message_module.WS_MESSAGE_TASK_TIMEOUT = old_timeout
        ws_message_module.manager = old_manager
        ws_message_module.task_limiter = old_limiter
        ws_message_module.background_tasks = old_background_tasks
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts
        WsMessageHandler.dispatch = old_dispatch


async def _check_client_waiting_limit() -> None:
    """确认单个 client_id 最多只能预占 5 条待处理消息。"""

    old_client_locks = ws_message_module.client_task_locks
    old_client_counts = ws_message_module.client_task_counts

    try:
        ws_message_module.client_task_locks = {}
        ws_message_module.client_task_counts = {}

        results = [
            await ws_message_module._reserve_client_task("limit_user")
            for _ in range(ws_message_module.WS_CLIENT_MAX_WAITING_TASKS + 1)
        ]

        expected = [True] * ws_message_module.WS_CLIENT_MAX_WAITING_TASKS + [False]
        assert results == expected, results

        for _ in range(ws_message_module.WS_CLIENT_MAX_WAITING_TASKS):
            await ws_message_module._release_client_task("limit_user")

        assert ws_message_module.client_task_counts == {}
        assert ws_message_module.client_task_locks == {}
    finally:
        ws_message_module.client_task_locks = old_client_locks
        ws_message_module.client_task_counts = old_client_counts


def main() -> None:
    asyncio.run(main_async())
    print("WebSocket 后台任务超时测试通过")


if __name__ == "__main__":
    main()
