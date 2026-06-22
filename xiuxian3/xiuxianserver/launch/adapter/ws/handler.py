import re
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple, Union

from .manager import ConnectionManager
from ..base_handler import BaseMessageHandler


Command = Union[str, Pattern]


@dataclass(frozen=True)
class HandlerRule:
    """一条命令规则，由 handler(...) 装饰器注册时创建。

    字段含义：
    - func: 命中后调用的业务函数。
    - priority: 优先级，数字越大越先执行。
    - block: 执行后是否阻断更低优先级规则。
    - order: 注册顺序，同优先级时保持稳定顺序。
    - pattern: 正则规则的 Pattern；精确规则为 None。

    示例：
        @WsMessageHandler.handler(cmd="你好", priority=10, block=False)
        async def hello(...):
            ...
    """

    func: Callable
    priority: int
    block: bool
    order: int
    pattern: Optional[Pattern] = None


class WsMessageHandler(BaseMessageHandler):
    """WebSocket chat 消息分发器。

    一条消息会先拆出 cmd，再同时走精确匹配和正则匹配。
    两类匹配没有天然高低，只按 priority 排序。
    block=True 只阻断更低 priority，不影响同 priority 的其他规则。
    """

    # 精确命令：{"你好": [HandlerRule(...)]}
    func_dict: Dict[str, List[HandlerRule]] = {}
    func_cmd: Set[str] = set()

    # 正则命令按固定前缀分组，能提速就提速。
    regex_dict: Dict[str, List[HandlerRule]] = {}
    # 没有固定前缀的正则放这里，保证任意正则都能注册。
    regex_fallback: List[HandlerRule] = []
    regex_cmd: List[str] = []
    regex_prefix_lengths: Set[int] = set()
    exact_cmd_lengths: Set[int] = set()

    _func_params_cache: Dict[Callable, Optional[Set[str]]] = {}
    _register_order: int = 0

    @staticmethod
    async def run() -> None:
        """启动时整理匹配索引。

        装饰器注册时只收集规则；run() 负责生成运行期索引并排序。
        """

        # 精确命令索引，方便调试查看。
        WsMessageHandler.func_cmd = set(WsMessageHandler.func_dict.keys())
        WsMessageHandler.exact_cmd_lengths = {
            len(cmd)
            for cmd in WsMessageHandler.func_dict
        }

        # 正则源码列表，只用于调试，不参与匹配。
        WsMessageHandler.regex_cmd = [
            rule.pattern.pattern
            for rules in WsMessageHandler.regex_dict.values()
            for rule in rules
            if rule.pattern is not None
        ]
        WsMessageHandler.regex_cmd.extend(
            rule.pattern.pattern
            for rule in WsMessageHandler.regex_fallback
            if rule.pattern is not None
        )

        # 用前缀长度快速定位候选正则组，避免全量遍历。
        WsMessageHandler.regex_prefix_lengths = {
            len(prefix)
            for prefix in WsMessageHandler.regex_dict
        }

        # 每组规则都提前排序，dispatch 时直接按顺序执行。
        for rules in WsMessageHandler.func_dict.values():
            rules.sort(key=lambda rule: (-rule.priority, rule.order))

        for rules in WsMessageHandler.regex_dict.values():
            rules.sort(key=lambda rule: (-rule.priority, rule.order))

        WsMessageHandler.regex_fallback.sort(key=lambda rule: (-rule.priority, rule.order))

    @staticmethod
    async def shutdown() -> None:
        """关闭 WebSocket 后台任务和连接。"""

        from .message import shutdown

        await shutdown()

    @staticmethod
    async def dispatch(
        client_id: str,
        message_data: dict,
        manager: "ConnectionManager",
    ) -> bool:
        """处理一条 WebSocket chat 消息。

        client_id 来自连接路径 /ws/bot/{client_id}，表示本次消息来自哪个客户端。
        先找到所有命中规则，再按 priority 从高到低执行。
        block=True 时，同 priority 继续执行，更低 priority 停止传播。
        返回 True 表示至少命中过一条规则；False 表示没有任何规则处理这条消息。
        """

        raw_message = str(message_data.get("message", ""))
        if not raw_message.strip():
            return False

        clean_message = raw_message.lstrip()
        cmd, message = await WsMessageHandler._split_message(clean_message)
        matched_rules = await WsMessageHandler._match_rules(cmd)
        if not matched_rules:
            return False

        block_priority = None
        for rule, match in matched_rules:
            if block_priority is not None and rule.priority < block_priority:
                break

            # 把命中的规则和消息上下文交给业务函数。
            # message 始终表示“本次触发片段之后”的内容：
            # - 精确命令：第一个空格后的内容。
            # - 正则命令：正则命中部分后面的内容。
            # 完整文本仍放在 raw_message / message_data，供自定义功能使用。
            rule_message = WsMessageHandler._message_after_match(
                clean_message=clean_message,
                split_message=message,
                match=match,
            )

            await WsMessageHandler._call_rule(
                rule,
                client_id=client_id,
                message=rule_message,
                manager=manager,
                cmd=cmd,
                raw_message=raw_message,
                message_data=message_data,
                match=match,
            )

            if rule.block:
                block_priority = rule.priority

        return True

    @staticmethod
    async def has_match(message_data: dict) -> bool:
        """只判断消息能不能命中规则，不执行任何业务函数。

        WS 入口会先调用它：
        - 命中：再创建后台任务，进入真正的 dispatch。
        - 未命中：立刻回复 404，不占用单用户队列和全局任务队列。
        """

        raw_message = str(message_data.get("message", ""))
        if not raw_message.strip():
            return False

        clean_message = raw_message.lstrip()
        cmd, _ = await WsMessageHandler._split_message(clean_message)
        matched_rules = await WsMessageHandler._match_rules(cmd)
        return bool(matched_rules)

    @staticmethod
    def handler(
        cmd: Union[Command, List[Command]],
        priority: int = 0,
        block: bool = False,
    ) -> Callable:
        """注册命令处理函数。

        精确命令：
            @WsMessageHandler.handler(cmd="你好", priority=10, block=True)

        正则命令：
            @WsMessageHandler.handler(cmd=re.compile(r"^查温度(?P<name>\\S+)$"), priority=5)

        priority 越大越先执行；block=True 会阻断更低优先级。
        """

        def wrapper(func: Callable):
            for item in WsMessageHandler._normalize_items(cmd):
                if isinstance(item, str):
                    WsMessageHandler._register_exact_cmd(item, func, priority, block)
                elif isinstance(item, re.Pattern):
                    WsMessageHandler._register_regex_cmd(item, func, priority, block)
                else:
                    raise TypeError("cmd 只支持 str、re.Pattern，或它们组成的 list/tuple/set")

            WsMessageHandler._func_params_cache.pop(func, None)
            return func

        return wrapper

    @staticmethod
    async def _split_message(raw_message: str) -> Tuple[str, str]:
        """按第一个空格拆出 cmd 和 message。

        message 不再 split 成列表，避免打乱业务参数。
        CQ/at 已经在 WS 接收层转成 client_id，这里只负责拆分。
        """

        cmd, separator, message = raw_message.partition(" ")
        if not separator:
            return raw_message, ""
        return cmd, message.strip()

    @staticmethod
    def _message_after_match(
        clean_message: str,
        split_message: str,
        match: Optional[re.Match],
    ) -> str:
        """返回本次触发片段后面的内容。

        精确命令没有 match，直接沿用 _split_message 得到的 message。
        正则命令有 match，取正则命中部分之后的原文作为业务参数。
        """

        if match is None:
            return split_message
        return clean_message[match.end() :].lstrip()

    @staticmethod
    def _register_exact_cmd(cmd: str, func: Callable, priority: int, block: bool) -> None:
        """注册精确命令。"""

        rule = WsMessageHandler._make_rule(func=func, priority=priority, block=block)
        WsMessageHandler.func_dict.setdefault(cmd, []).append(rule)

    @staticmethod
    def _register_regex_cmd(pattern: Pattern, func: Callable, priority: int, block: bool) -> None:
        """注册正则命令。

        有固定前缀的规则会进入索引表。
        没有固定前缀的规则也允许注册，放入兜底列表。
        """

        prefix = WsMessageHandler._extract_literal_prefix(pattern.pattern)
        rule = WsMessageHandler._make_rule(
            func=func,
            priority=priority,
            block=block,
            pattern=pattern,
        )
        if prefix:
            WsMessageHandler.regex_dict.setdefault(prefix.casefold(), []).append(rule)
        else:
            WsMessageHandler.regex_fallback.append(rule)

    @staticmethod
    def _make_rule(
        func: Callable,
        priority: int,
        block: bool,
        pattern: Optional[Pattern] = None,
    ) -> HandlerRule:
        """创建规则对象，并记录注册顺序。"""

        order = WsMessageHandler._register_order
        WsMessageHandler._register_order += 1
        return HandlerRule(
            func=func,
            priority=priority,
            block=block,
            order=order,
            pattern=pattern,
        )

    @staticmethod
    async def _match_rules(cmd: str) -> List[Tuple[HandlerRule, Optional[re.Match]]]:
        """找到所有命中规则，并按 priority/order 排序。"""

        matched: List[Tuple[HandlerRule, Optional[re.Match]]] = []

        for rule in WsMessageHandler.func_dict.get(cmd, []):
            matched.append((rule, None))

        matched.extend(await WsMessageHandler._match_regex_cmd(cmd))
        matched.sort(key=lambda item: (-item[0].priority, item[0].order))
        return matched

    @staticmethod
    async def _match_regex_cmd(cmd: str) -> List[Tuple[HandlerRule, re.Match]]:
        """按固定前缀找到候选正则，再执行 pattern.search。

        search 可以命中文本任意位置；需要从头匹配时，业务侧自己在正则里写 ^。
        """

        matched = []
        key = cmd.casefold()
        seen_rules: Set[int] = set()

        for length in WsMessageHandler.regex_prefix_lengths:
            if length > len(key):
                continue

            for start in range(0, len(key) - length + 1):
                for rule in WsMessageHandler.regex_dict.get(key[start : start + length], []):
                    rule_id = id(rule)
                    if rule_id in seen_rules:
                        continue

                    seen_rules.add(rule_id)
                    match = rule.pattern.search(cmd)
                    if match:
                        matched.append((rule, match))

        for rule in WsMessageHandler.regex_fallback:
            rule_id = id(rule)
            if rule_id in seen_rules:
                continue

            seen_rules.add(rule_id)
            match = rule.pattern.search(cmd)
            if match:
                matched.append((rule, match))

        return matched

    @staticmethod
    def _extract_literal_prefix(source: str) -> str:
        """从正则源码中提取开头固定文字。

        示例：
        - ^查温度(?P<name>\\S+)$ -> 查温度
        - ^cmd\\-(?P<id>\\d+)$ -> cmd-
        """

        index = 1 if source.startswith("^") else 0
        prefix = []
        metacharacters = set(".^$*+?{}[]|()")

        while index < len(source):
            char = source[index]

            if char in metacharacters:
                break

            if char == "\\":
                if index + 1 >= len(source):
                    break

                next_char = source[index + 1]
                if next_char in "AbBdDsSwWZ0123456789":
                    break

                prefix.append(next_char)
                index += 2
                continue

            prefix.append(char)
            index += 1

        return "".join(prefix)

    @staticmethod
    def _normalize_items(value: Any) -> list:
        """把单个 cmd 或多个 cmd 统一成 list。"""

        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    @staticmethod
    async def _call_rule(rule: HandlerRule, **context: Any) -> None:
        """调用命中的业务函数。

        可声明的常用参数：

        - message: 本次触发片段后面的文本，通常作为业务参数。
        - client_id: 当前触发消息的客户端 ID，来自 /ws/bot/{client_id}。
        - manager: 连接管理器，可调用 send(message, client_id) 回复当前客户端。
        - cmd: 第一个空格前的命令部分。
        - raw_message: 完整原始文本。
        - message_data: 完整 JSON 消息对象。
        - match: 正则匹配结果；精确命令时为 None。

        业务函数只写自己要用的参数即可：

            async def hello(client_id, message, manager):
                ...

            async def query(client_id, match, manager):
                ...
        声明 **kwargs 时会收到完整 context。
        """

        params = WsMessageHandler._get_func_params(rule.func)
        kwargs = context if params is None else {
            name: value
            for name, value in context.items()
            if name in params
        }

        result = rule.func(**kwargs)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _get_func_params(func: Callable) -> Optional[Set[str]]:
        """读取并缓存业务函数能接收的参数名。"""

        if func in WsMessageHandler._func_params_cache:
            return WsMessageHandler._func_params_cache[func]

        signature = inspect.signature(func)
        parameters = signature.parameters
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

        if has_kwargs:
            params = None
        else:
            params = {
                name
                for name, parameter in parameters.items()
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }

        WsMessageHandler._func_params_cache[func] = params
        return params
