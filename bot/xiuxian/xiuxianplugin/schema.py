import json
from base64 import b64encode
from io import BytesIO
from typing import Any


VALID_CODES = {202, 404}


def is_ws_code(code: object) -> bool:
    """判断 code 是否为当前协议允许的整数状态码。"""

    return type(code) is int and code in VALID_CODES


def ws_message(
    message: object,
    message_type: str = "text",
    code: int = 202,
    request_id: object = None,
) -> dict:
    """生成统一的 WS 消息格式。

    字段顺序固定，不能调换：
    - code: 202 表示通讯正常返回，404 表示通讯或协议错误。
    - type: 自定义消息类型，默认 text。
    - message: 文本内容。
    - request_id: 请求 id；正常请求和回复都应该携带。
    """

    if not is_ws_code(code):
        raise ValueError("WS code 只允许整数 202 或 404")

    msg_type = str(message_type)
    payload = {
        "code": code,
        "type": msg_type,
        "message": _format_message(message, msg_type),
    }

    if request_id is not None and str(request_id).strip():
        payload["request_id"] = str(request_id).strip()

    return payload


def _format_message(message: object, message_type: str) -> str:
    """按消息类型整理 message。

    text 直接转字符串。
    image 支持 bytes / bytearray / memoryview / BytesIO。
    返回纯 base64 字符串，不带 data:image/... 头。
    """

    if message_type != "image":
        return str(message)

    image_bytes = _read_image_bytes(message)
    if image_bytes is None:
        return str(message)
    return b64encode(image_bytes).decode("utf-8")


def _read_image_bytes(message: object) -> bytes | None:
    """读取图片二进制；读取 BytesIO 后尽量恢复指针位置。"""

    if isinstance(message, bytes):
        return message
    if isinstance(message, bytearray | memoryview):
        return bytes(message)
    if isinstance(message, BytesIO):
        position = message.tell()
        message.seek(0)
        data = message.read()
        message.seek(position)
        return data
    return None


def make_payload(data: Any, request_id: object = None) -> dict:
    """把任意输入转成 WS 统一格式。"""

    if isinstance(data, dict):
        missing = [field for field in ("code", "type", "message") if field not in data]
        if missing:
            raise ValueError("WS 消息缺少 " + "、".join(missing))

        payload_request_id = data.get("request_id") if request_id is None else request_id
        return ws_message(
            code=data["code"],
            message=data["message"],
            message_type=data["type"],
            request_id=payload_request_id,
        )

    return ws_message(data, request_id=request_id)


def loads_message(text: str) -> dict:
    """解析 WS 文本消息，并统一成 code/type/message/request_id 格式。"""

    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return ws_message("Invalid JSON format", code=404)

    if not isinstance(message, dict):
        return ws_message("Invalid WS message format", code=404)

    try:
        return make_payload(message)
    except ValueError as exc:
        return ws_message(str(exc), code=404)
