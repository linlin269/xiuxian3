"""修仙后台接口组件。"""

from __future__ import annotations

from launch import C, OnEvent, logger

from .service import service
from .site import router

__all__ = ["router"]


@OnEvent.connect(priority=55)
async def start_admin_backend() -> None:
    """启动后台服务与独立审计库。"""

    service.startup()
    logger.opt(colors=True).info(f"{C.ok('执行 后台接口 启动')}")


@OnEvent.disconnect(priority=55)
async def stop_admin_backend() -> None:
    """关闭后台服务与独立审计库。"""

    service.shutdown()
    logger.opt(colors=True).info(f"{C.warn('执行 后台接口 关闭')}")
