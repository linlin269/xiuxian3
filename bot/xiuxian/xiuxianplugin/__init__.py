import ast
import re
import base64
from pathlib import Path

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.exception import FinishedException
from nonebot.plugin.on import on_notice

try:
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from nonebot.adapters.onebot.v11 import MessageSegment as MessageSegmentOneBotV11
    from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot

    ENABLE_ADAPTER_ONEBOT_V11 = True
except ImportError:
    logger.warning("没有成功加载OneBot V11机器人适配器，尝试前往安装nonebot-adapter-onebot")
    GroupMessageEvent = None
    PrivateMessageEvent = None
    MessageSegmentOneBotV11 = None
    ENABLE_ADAPTER_ONEBOT_V11 = False
    OneBotV11Bot = None

try:
    from nonebot.adapters.qq.models import MessageKeyboard
    from nonebot.adapters.qq import (
        GroupAtMessageCreateEvent,
        GroupMessageCreateEvent,
        C2CMessageCreateEvent,
        InteractionCreateEvent)
    from nonebot.adapters.qq import MessageSegment as MessageSegmentQQ
    from nonebot.adapters.qq import Bot as QQBot

    ENABLE_ADAPTER_QQ = True
except ImportError:
    logger.warning("没有成功加载QQ官方机器人适配器，尝试前往安装nonebot-adapter-qq")
    ENABLE_ADAPTER_QQ = False
    GroupAtMessageCreateEvent = None
    GroupMessageCreateEvent = None
    C2CMessageCreateEvent = None
    MessageSegmentQQ = None
    MessageKeyboard = None
    InteractionCreateEvent = None
    QQBot = None

from .api import client

driver = get_driver()


@driver.on_shutdown
async def _():
    await client.close_all()


XiuXianGroup = []
"""使用范围（群聊）"""

ReverseXiuXianGroup = True
"""反转使用范围为黑名单范围，其余开放"""

ENSURE_MARKDOWN = False
"""
onebot v11协议下确认启用markdown传输，
注意：在非官方对接的实现端框架下大概率无法正常发送markdown消息，此时推荐此选项保持False
"""

if (not XiuXianGroup) ^ ReverseXiuXianGroup:
    logger.opt(colors=True).warning(
        f"<yellow>注意: 你没有配置群聊的使用范围，请前往{Path(__file__)}下配置群聊使用范围XiuXianGroup</yellow>")

if not ENABLE_ADAPTER_ONEBOT_V11 and ENABLE_ADAPTER_QQ:
    logger.opt(colors=True).error(
        f"<red>警告: 当前未成功加载任何适配器，将无法正常收/发任何消息，尝试根据提示安装任意支持的适配器</red>")

# 捕获所有消息事件
repeater = on_message(priority=1, block=False)
# 捕获所有消息事件
notice_repeater = on_notice(priority=1, block=False)


@notice_repeater.handle()
async def _(
        event: InteractionCreateEvent, bot: QQBot | OneBotV11Bot):
    # 处理按钮回调消息
    if event.chat_type != 1 or ((str(event.group_openid) in XiuXianGroup) ^ ReverseXiuXianGroup):
        try:
            await bot.put_interaction(interaction_id=event.id, code=0)
            reply = await client.send(event.get_user_id(), event.data.resolved.button_data)
            await handle_ws_reply_qq(reply, repeater)
        except Exception as e:
            if isinstance(e, FinishedException):
                return
            logger.opt(exception=e).debug(f"处理来自adapter qq的按钮回调消息(类型：{event.chat_type})时出现错误")


@repeater.handle()
async def _(
        event: GroupMessageEvent
               | PrivateMessageEvent
               | GroupAtMessageCreateEvent
               | C2CMessageCreateEvent
               | GroupMessageCreateEvent):
    # 处理群消息
    if ENABLE_ADAPTER_ONEBOT_V11 and isinstance(event, GroupMessageEvent):
        if (str(event.group_id) in XiuXianGroup) ^ ReverseXiuXianGroup:
            try:
                reply = await client.send(event.get_user_id(), event.get_message())
                await handle_ws_reply_onebot_v11(reply, repeater)
            except Exception as e:
                if isinstance(e, FinishedException):
                    return
                logger.opt(exception=e).debug("处理来自onebot v11的群消息时出现错误")
                pass
    # 处理来自官方机器人的群消息
    if ENABLE_ADAPTER_QQ and isinstance(event, GroupAtMessageCreateEvent | GroupMessageCreateEvent):
        if (str(event.group_openid) in XiuXianGroup) ^ ReverseXiuXianGroup:
            try:
                reply = await client.send(event.get_user_id(), event.get_message())
                await handle_ws_reply_qq(reply, repeater)
            except Exception as e:
                if isinstance(e, FinishedException):
                    return
                logger.opt(exception=e).debug("处理来自官方机器人的群消息时出现错误")
                pass

    # 处理私聊消息
    if ENABLE_ADAPTER_ONEBOT_V11 and isinstance(event, PrivateMessageEvent):
        try:
            reply = await client.send(event.get_user_id(), event.get_message())
            await handle_ws_reply_onebot_v11(reply, repeater)
        except Exception as e:
            if isinstance(e, FinishedException):
                return
            logger.opt(exception=e).debug("处理来自官方机器人的私聊消息时出现错误")
            pass
    # 处理来自官方机器人的私聊消息
    if ENABLE_ADAPTER_QQ and isinstance(event, C2CMessageCreateEvent):
        try:
            reply = await client.send(event.get_user_id(), event.get_message())
            await handle_ws_reply_qq(reply, repeater)
        except Exception as e:
            if isinstance(e, FinishedException):
                return
            logger.opt(exception=e).debug("处理来自onebot v11的私聊消息时出现错误")
            pass


async def handle_ws_reply_qq(reply, matcher):
    if reply["code"] == 202:
        if reply["type"] == "text":
            await matcher.finish(reply["message"])
        if reply["type"] == "image":
            await matcher.finish(MessageSegmentQQ.file_image(base64.b64decode(reply['message'], validate=True)))
        if reply["type"] == "markdown":
            #  这里不是json格式 是{'key': 'value'} 疑似直接str(dict)的，要用ast解析
            message = ast.literal_eval(reply['message'])
            msg = MessageSegmentQQ.markdown(message['content'])
            if "keyboard" in message:
                keyboard = MessageKeyboard.model_validate(message['keyboard'])
                msg += MessageSegmentQQ.keyboard(keyboard)
            await matcher.finish(msg)
    elif reply["code"] == 404:
        logger.error(reply["message"])


async def handle_ws_reply_onebot_v11(reply, matcher):
    if reply["code"] == 202:
        if reply["type"] == "text":
            await matcher.finish(reply["message"])
        if reply["type"] == "image":
            await matcher.finish(MessageSegmentOneBotV11.image(f"base64://{reply['message']}"))
        if reply["type"] == "markdown":
            message = ast.literal_eval(reply['message'])
            if ENSURE_MARKDOWN:
                data = {
                    "markdown": {
                        "content": message['content']
                    }
                }
                if "keyboard" in message:
                    data['markdown']['keyboard'] = message['keyboard']
                msg = MessageSegmentOneBotV11("markdown", {"data": data})
                await matcher.finish(msg)
            else:
                await matcher.finish(clean_markdown(message['content']))
        elif reply["code"] == 404:
            logger.error(reply["message"])


def clean_markdown(text: str) -> str:
    """
    清理文本中影响观感的Markdown语法
    """
    if not text:
        return ""
    # 处理图片（保留alt文本，去掉图片语法）
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)

    # 处理链接（保留链接文本，去掉链接地址）
    text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<([^>]+)>", r"\1", text)  # 自动链接

    # 处理粗体（**内容** 和 __内容__）
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)

    # 处理斜体（*内容* 和 _内容_）
    # 关键：智能排除列表符号（行首的*后面跟空格不会被匹配）
    text = re.sub(
        r"(?<!^)\*([^*]+)\*|^\*([^*]+)\*(?!\s)",
        r"\1\2",
        text
    )
    text = re.sub(
        r"(?<!^)_([^_]+)_|^_([^_]+)_(?!\s)",
        r"\1\2",
        text
    )

    # 处理删除线（~~内容~~）
    text = re.sub(r"~~([^~]+)~~", r"\1", text)

    # 处理标题（# 到 ######）
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # 处理引用（> 开头）
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)

    #  处理转义字符（去掉转义反斜杠）
    text = re.sub(r"\\([\\`*_{}[\]()#+\-.!|])", r"\1", text)

    return text