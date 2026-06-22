"""修仙后台接口组件。

当前先保留空 router，后续后台页面的数据接口都从这里扩展。
"""

from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()

__all__ = ["router"]
