"""异界虫洞组件服务入口。

命令包只负责挂载 WS 指令。
真正的虫洞业务放在修仙根目录公共服务里，避免二级包互相导入。
"""

from ..wormhole_service import BOSS_POOL, DISCOVERY_CHANCES, WormholeService, service


__all__ = ["BOSS_POOL", "DISCOVERY_CHANCES", "WormholeService", "service"]
