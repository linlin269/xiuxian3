"""修仙 markdown 和按钮工具。"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Literal, TypedDict


MAX_BUTTONS = 25
MAX_BUTTONS_PER_ROW = 3
MAX_BUTTON_ROWS = (MAX_BUTTONS + MAX_BUTTONS_PER_ROW - 1) // MAX_BUTTONS_PER_ROW
BUTTON_TAG_RE = re.compile(r"<([^<>\r\n]+)>")
BUTTON_CMD_SEPARATORS = (":", "：")
BUTTON_LABEL_PREFIX_ALIASES = {
    "指南": "指南",
    "修仙百科": "百科",
}
BUTTON_LABEL_EXACT_ALIASES = {
    "修仙帮助": "帮助图",
    "修仙信息": "修仙信息",
    "探险状态": "探险状态",
    "结束探险": "结束探险",
    "商场推荐": "商路推荐",
    "自动出售": "自动出售",
    "出售全部 武器": "卖武器",
    "出售全部 宝石": "卖宝石",
    "出售全部 技能书": "卖技能书",
    "源库结息": "源库结息",
    "修仙早报": "早报",
    "修仙界历史": "历史",
    "人物史榜": "人物史",
    "宗门史榜": "宗门史",
    "城池史榜": "城池史",
    "战斗名局": "名局",
    "商路奇闻": "奇闻",
    "异界虫洞录": "虫洞录",
    "铭刻之羽": "铭刻羽",
    "二手市场": "二手市场",
}
ButtonCommand = str | dict[str, Any]


class TipsWindow(TypedDict):
    """按钮二次确认弹窗。"""

    content: str
    confirm_text: str
    cancel_text: str


def button(
    text: str,
    cmd_data: str | None = None,
    button_type: Literal[0, 1, 2, 3] = 1,
    button_style: Literal[0, 1, 2, 3, 4] = 1,
    text_after_touch: str | None = None,
    touch_to_send: bool = True,
    refer_msg: bool = False,
    permission_type: Literal[0, 1, 2, 3] = 2,
    arrow_touch_user: str | list[str] | None = None,
    tips_window: TipsWindow | None = None,
    choice_group_id: str | None = None,
) -> dict:
    """生成一个 markdown 按钮。

    默认 button_type=1。只有需要指定按钮类型、跳转链接、权限、二次确认时，
    才传其它参数。
    """

    if text_after_touch is None:
        text_after_touch = text
    if cmd_data is None:
        cmd_data = text

    render_data = {
        "label": text,
        "visited_label": text_after_touch,
        "style": button_style,
    }
    action: dict = {
        "type": button_type,
        "data": cmd_data,
        "permission": {
            "type": permission_type,
        },
        "click_limit": 1,
        "unsupport_tips": "不支持的操作喵！",
    }
    if tips_window:
        action["modal"] = tips_window
    if permission_type == 0 or arrow_touch_user:
        action["permission"]["type"] = 0
        action["permission"]["specify_user_ids"] = [arrow_touch_user] if isinstance(arrow_touch_user, str) else arrow_touch_user
    if button_type == 2:
        action["enter"] = touch_to_send
        action["reply"] = refer_msg
    return {
        "render_data": render_data,
        "action": action,
        "group_id": choice_group_id,
    }


def buttons_from_commands(commands: list[ButtonCommand], limit: int = MAX_BUTTONS) -> list[list[dict]]:
    """把指令列表转成按钮行。"""

    return _buttons_to_rows(normalize_buttons(commands, limit))


def normalize_buttons(commands: list[ButtonCommand], limit: int = MAX_BUTTONS) -> list[dict]:
    """转成按钮对象后，按 action.data 去重并应用数量上限。"""

    result: list[dict] = []
    seen: set[str] = set()
    for command in commands:
        item = _button_from_command(command)
        key = _button_dedupe_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _buttons_to_rows(buttons: list[dict]) -> list[list[dict]]:
    """把按钮对象切成每行最多 3 个。"""

    rows: list[list[dict]] = []
    max_buttons = min(len(buttons), MAX_BUTTONS)
    for index in range(0, max_buttons, MAX_BUTTONS_PER_ROW):
        end_index = min(index + MAX_BUTTONS_PER_ROW, max_buttons)
        rows.append(buttons[index:end_index])
    return rows[:MAX_BUTTON_ROWS]


def markdown_message(content: str, commands: list[ButtonCommand], limit: int = MAX_BUTTONS) -> dict:
    """生成 markdown message 字段。"""

    return {
        "content": content,
        "keyboard": MarkdownKeyboard.from_rows(buttons_from_commands(commands, limit)).to_content(),
    }


def markdown_message_from_text(text: str) -> dict | None:
    """只把尖括号包住的手写命令转成按钮。

    这里不猜测“发送：xxx”，也不判断命令是否需要参数。
    后续要不要生成按钮，完全由业务文本里是否手写尖括号标记决定。
    """

    content, commands = split_button_tags(text)
    if not commands:
        return None
    return markdown_message(content, commands)


def split_button_tags(text: str) -> tuple[str, list[str]]:
    """提取手写按钮，尖括号内文本原样作为按钮命令。"""

    commands: list[str] = []

    def replace_button_tag(match: re.Match[str]) -> str:
        command = match.group(1).strip()
        if not command:
            return ""
        commands.append(command)
        return ""

    content = BUTTON_TAG_RE.sub(replace_button_tag, text)
    if not commands:
        return text, []
    return _clean_button_tag_content(content), commands


def _button_from_command(command: ButtonCommand) -> dict:
    """把字符串命令或完整按钮对象统一转成按钮。"""

    if isinstance(command, dict):
        return deepcopy(command)
    cmd_data, label = parse_button_command(str(command))
    return button(label, cmd_data=cmd_data)


def parse_button_command(text: str) -> tuple[str | None, str]:
    """解析 `<真实命令:显示文字>` 形式的按钮文本。"""

    value = text.strip()
    for separator in BUTTON_CMD_SEPARATORS:
        if separator not in value:
            continue
        cmd_data, label = value.split(separator, 1)
        cmd_data = cmd_data.strip()
        label = label.strip()
        if label and cmd_data:
            return cmd_data, label
        break
    return value, button_label_for_command(value)


def button_label_for_command(command: str) -> str:
    """生成按钮展示名；发送命令仍保留完整文本。"""

    value = command.strip()
    alias = BUTTON_LABEL_EXACT_ALIASES.get(value)
    if alias:
        return alias
    for prefix, short_prefix in BUTTON_LABEL_PREFIX_ALIASES.items():
        if value == prefix:
            return short_prefix
        if value.startswith(f"{prefix} "):
            return f"{short_prefix} {value[len(prefix):].strip()}"
    return value


def _button_dedupe_key(item: dict) -> str:
    """按钮唯一键：优先 action.data，异常结构再退回展示名。"""

    action = item.get("action", {})
    if isinstance(action, dict):
        raw_data = action.get("data")
        if raw_data is not None:
            data = str(raw_data).strip()
            if data:
                return data
    render_data = item.get("render_data", {})
    if isinstance(render_data, dict):
        label = str(render_data.get("label", "")).strip()
        if label:
            return label
    return str(item).strip()


def _clean_button_tag_content(text: str) -> str:
    """清理移除手写按钮标记后留下的多余空白。"""

    content = re.sub(r"[ \t]{2,}", " ", text)
    content = re.sub(r"[ \t]+\n", "\n", content)
    content = re.sub(r"\n[ \t]+", "\n", content)
    return content.strip()


def _set_buttons_id(button_row: list[dict] | None, storage_dict: dict[str, int]) -> list[dict] | None:
    """给一行按钮补充 id。"""

    if not button_row:
        return None
    for item in button_row[:MAX_BUTTONS_PER_ROW]:
        item["id"] = str(storage_dict["id"])
        storage_dict["id"] += 1
    return button_row[:MAX_BUTTONS_PER_ROW]


class MarkdownKeyboard:
    """markdown 键盘，最多 25 个按钮，每行最多 3 个。"""

    def __init__(
        self,
        row_1: list[dict] | None = None,
        row_2: list[dict] | None = None,
        row_3: list[dict] | None = None,
        row_4: list[dict] | None = None,
        row_5: list[dict] | None = None,
        *extra_rows: list[dict] | None,
    ) -> None:
        self.rows = [row for row in (row_1, row_2, row_3, row_4, row_5, *extra_rows) if row]

    @classmethod
    def from_rows(cls, rows: list[list[dict]]) -> "MarkdownKeyboard":
        """从按钮行列表创建键盘。"""

        fixed_rows = rows[:MAX_BUTTON_ROWS]
        fixed_rows += [[] for _ in range(MAX_BUTTON_ROWS - len(fixed_rows))]
        return cls(*fixed_rows)

    @classmethod
    def from_commands(cls, commands: list[ButtonCommand], limit: int = MAX_BUTTONS) -> "MarkdownKeyboard":
        """从指令列表创建键盘。"""

        return cls.from_rows(buttons_from_commands(commands, limit))

    def to_content(self) -> dict:
        """转成客户端需要的 keyboard 内容。"""

        storage_dict = {"id": 1}
        rows = []
        for row in self.rows[:MAX_BUTTON_ROWS]:
            button_row = _set_buttons_id(row, storage_dict)
            if button_row:
                rows.append({"buttons": button_row})
        return {"content": {"rows": rows}}
