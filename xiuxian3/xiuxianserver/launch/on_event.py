from dataclasses import dataclass
from itertools import count
from typing import Callable, Iterable, List


@dataclass(frozen=True)
class EventCallback:
    """一个生命周期回调及其执行顺序配置。"""

    priority: int
    order: int
    func: Callable


class OnEvent:
    """启动和关闭回调注册器。"""

    connect_list: List[EventCallback] = []
    disconnect_list: List[EventCallback] = []
    _order_counter = count()

    @staticmethod
    def connect(priority: int = 0) -> Callable:
        """注册服务启动回调，priority 越大越先执行。"""

        def wrapper(func: Callable):
            OnEvent.connect_list.append(
                EventCallback(
                    priority=priority,
                    order=next(OnEvent._order_counter),
                    func=func,
                )
            )
            return func

        return wrapper

    @staticmethod
    def disconnect(priority: int = 0) -> Callable:
        """注册服务关闭回调，priority 越大越先执行。"""

        def wrapper(func: Callable):
            OnEvent.disconnect_list.append(
                EventCallback(
                    priority=priority,
                    order=next(OnEvent._order_counter),
                    func=func,
                )
            )
            return func

        return wrapper

    @staticmethod
    def ordered_callbacks(callbacks: Iterable[EventCallback]) -> List[Callable]:
        """按优先级整理回调；同优先级保持注册顺序。"""

        return [
            callback.func
            for callback in sorted(
                callbacks,
                key=lambda callback: (-callback.priority, callback.order),
            )
        ]
