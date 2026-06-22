from abc import ABC, abstractmethod
from typing import Callable


class BaseAdapter(ABC):
    """通信或外部系统适配器基类。

    后台模板默认不携带具体驱动。后续接入 WS、MQ、机器人协议、
    webhook 等能力时，实现本基类并在 launch.mount.AdapterMount 中挂载。
    """

    @staticmethod
    @abstractmethod
    async def run() -> None:
        """启动适配器或整理运行期索引。"""

    @staticmethod
    @abstractmethod
    async def dispatch(*args, **kwargs) -> None:
        """分发消息、事件或外部请求。"""

    @staticmethod
    @abstractmethod
    def handler(*args, **kwargs) -> Callable:
        """注册处理函数。"""

    @staticmethod
    @abstractmethod
    async def shutdown() -> None:
        """关闭适配器并清理资源。"""


class BaseMessageHandler(BaseAdapter):
    """消息处理器基类。

    保留这个名称给 WS、机器人协议、MQ 等消息型 Adapter 使用。
    """
