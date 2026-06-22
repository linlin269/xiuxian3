"""修仙模块入口。

作为 ROUTER_GROUPS 根模块时，这里暴露 router 并注册生命周期。
玩法包由 ROUTER_GROUPS 加载器导入，并在各自包内注册 ws 触发器。
"""

from importlib import import_module
from typing import Any

from fastapi import APIRouter
from launch import C, OnEvent, logger

from .sql import db

router = APIRouter()


def _include_child_router(target: APIRouter, child_name: str) -> None:
    """按二级组件名动态加载 router，避免直接从中文路径导入。"""

    module: Any = import_module(f"{__name__}.{child_name}")
    child_router = getattr(module, "router", None)
    if child_router is not None:
        target.include_router(child_router)


_include_child_router(router, "后台接口")
_include_child_router(router, "修仙帮助")
_include_child_router(router, "战斗日志")

__all__ = ["router"]


@OnEvent.connect(priority=50)
async def start_db() -> None:
    """服务启动时准备修仙玩法数据库。"""

    db.init()
    logger.opt(colors=True).info(f"{C.ok('执行 修仙数据库 启动')}")


@OnEvent.disconnect(priority=50)
async def stop_db() -> None:
    """服务关闭时释放修仙玩法数据库连接。"""

    db.close()
    logger.opt(colors=True).info(f"{C.warn('执行 修仙数据库 关闭')}")
