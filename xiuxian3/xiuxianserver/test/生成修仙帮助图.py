"""按当前实际 WS 命令生成修仙帮助图。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "修仙" / "修仙帮助" / "help.png"

FONT_REGULAR_PATHS = (
    Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)
FONT_BOLD_PATHS = (
    Path(r"C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)

SECTIONS = [
    {
        "title": "帮助",
        "color": "#FFE9A8",
        "lines": [
            "帮助",
            "修仙帮助",
            "指南",
            "指南 成长/行囊/战斗/交易/世界",
        ],
    },
    {
        "title": "玩家",
        "color": "#FFD987",
        "lines": [
            "创建用户 名称",
            "改名 新名称",
            "修仙信息",
            "修仙日记",
            "自动用药 / 自动用药 开启 / 自动用药 关闭",
            "签到",
            "新手礼包",
            "休息",
            "结束休息 / 休息结束",
        ],
    },
    {
        "title": "背包・纳戒・修仙物品",
        "color": "#AFEBC0",
        "lines": [
            "背包",
            "使用 恢复类物品名 数量",
            "纳戒",
            "保险箱 / 查看保险箱",
            "存入保险箱 物品名 数量",
            "取出保险箱 物品名 数量",
            "洗髓",
            "武器淬锋 / 武器淬锋 武器ID",
            "开孔 装备位",
            "查看修仙物品 物品名",
        ],
    },
    {
        "title": "源库",
        "color": "#B5DDF8",
        "lines": [
            "源库",
            "源库结息",
            "升级源库",
            "存入源石 数量",
            "取出源石 数量",
        ],
    },
    {
        "title": "探险",
        "color": "#CDBBFA",
        "lines": [
            "位置",
            "探险列表",
            "探险",
            "探险 地点名",
            "探险状态",
            "结束探险",
            "探险记录",
        ],
    },
    {
        "title": "商场・出售・导航",
        "color": "#FFF09D",
        "lines": [
            "商场行情 商品名",
            "商场购买 商品名 数量",
            "商场出售 商品名 数量",
            "商场推荐",
            "跑商记录",
            "跑商限制",
            "跑商奖励",
            "出售 物品名 数量",
            "自动出售",
            "出售全部 武器/宝石/技能书",
            "导航 地点名或 x y",
        ],
    },
    {
        "title": "二手市场",
        "color": "#F6B8D5",
        "lines": [
            "二手市场",
            "二手市场上架 名称 数量 总价",
            "二手市场上架 武器#ID 总价",
            "二手市场下架",
            "二手市场购买 卖家名称 / 二手市场购买@卖家",
        ],
    },
    {
        "title": "武器",
        "color": "#BBA5F2",
        "lines": [
            "武器",
            "查看武器 / 查看武器 武器ID",
            "武器传奇 武器ID",
            "切换武器 武器ID",
            "升级武器 / 升级武器 武器ID",
            "附魔武器 武器ID 技能书名",
            "出售全部 武器 / 出售全部 技能书",
        ],
    },
    {
        "title": "装备・宝石",
        "color": "#FFC49E",
        "lines": [
            "装备",
            "装备升级 装备位",
            "孔位 装备位",
            "镶嵌 装备位 孔位 宝石名",
            "拆卸 装备位 孔位",
            "宝石升级 装备位 孔位",
            "出售 宝石名 等级 数量 / 出售全部 宝石",
            "宝石",
        ],
    },
    {
        "title": "铭刻",
        "color": "#E7B1D4",
        "lines": [
            "铭刻",
            "铭刻之羽",
            "铭刻装备 装备位 新名",
            "铭刻武器 武器#ID 新名",
            "铭刻技能 武器#ID 新名",
            "铭刻附魔 武器#ID 序号 新名",
        ],
    },
    {
        "title": "对战",
        "color": "#BCE7D0",
        "lines": [
            "切磋 对方名称 / 切磋@对方",
            "接受切磋 发起人名称 / 接受切磋@发起人",
            "拒绝切磋 发起人名称 / 拒绝切磋@发起人",
            "决斗 源石数量 对方名称 / 决斗@对方 源石数量",
            "接受决斗 发起人名称 / 接受决斗@发起人",
            "拒绝决斗 发起人名称 / 拒绝决斗@发起人",
            "抢劫 对方名称 / 抢劫@对方",
            "决斗记录",
        ],
    },
    {
        "title": "异界虫洞",
        "color": "#B9E5EA",
        "lines": [
            "虫洞",
            "虫洞状态",
            "挑战虫洞",
            "虫洞排行",
            "虫洞奖励",
        ],
    },
    {
        "title": "首领",
        "color": "#F7DFA1",
        "lines": [
            "首领",
            "首领状态",
            "挑战首领",
            "首领排行",
            "首领奖励",
        ],
    },
    {
        "title": "修仙界历史",
        "color": "#CBEABF",
        "lines": [
            "风云榜",
            "修仙早报",
            "修仙界历史",
            "人物史榜 / 宗门史榜 / 城池史榜",
            "战斗名局 / 商路奇闻 / 异界虫洞录",
            "人物志 玩家名称 / 人物志@对方",
        ],
    },
]

def load_font(paths: Iterable[Path], size: int) -> ImageFont.FreeTypeFont:
    """从可用字体中加载第一个能显示中文的字体。"""

    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()

def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    """计算文本宽度。"""

    return draw.textlength(text, font=font)

def wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """按真实像素宽度换行，避免命令文字冲出卡片。"""

    if text_width(draw, text, font) <= max_width:
        return [text]

    rows: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and text_width(draw, candidate, font) > max_width:
            rows.append(current)
            current = char
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows

def draw_round_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: str,
    outline: str | None = None,
    width: int = 1,
) -> None:
    """绘制圆角矩形。"""

    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def draw_face(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    """画一个小表情，给卡片一点轻松感。"""

    draw.ellipse((cx - 17, cy - 17, cx + 17, cy + 17), fill="#FFF9D7", outline="#7B573B", width=2)
    draw.ellipse((cx - 7, cy - 5, cx - 3, cy - 1), fill="#6B4B32")
    draw.ellipse((cx + 5, cy - 5, cx + 9, cy - 1), fill="#6B4B32")
    draw.arc((cx - 8, cy - 6, cx + 9, cy + 11), start=25, end=155, fill="#6B4B32", width=2)

def card_height(
    draw: ImageDraw.ImageDraw,
    section: dict[str, object],
    body_font: ImageFont.ImageFont,
    card_width: int,
) -> int:
    """预估卡片高度，让瀑布流布局不会重叠。"""

    max_text_width = card_width - 68
    height = 78
    for line in section["lines"]:  # type: ignore[index]
        wrapped = wrap_line(draw, str(line), body_font, max_text_width)
        height += 28 * len(wrapped) + 8
    return height + 24

def draw_card(
    draw: ImageDraw.ImageDraw,
    section: dict[str, object],
    x: int,
    y: int,
    width: int,
    height: int,
    title_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
) -> None:
    """绘制一个命令分组卡片。"""

    shadow = (x + 7, y + 7, x + width + 7, y + height + 7)
    draw_round_rect(draw, shadow, 18, "#D9BE9F")
    draw_round_rect(draw, (x, y, x + width, y + height), 18, "#FFFDF8", "#E1C09B", 2)
    draw_round_rect(draw, (x, y, x + width, y + 60), 18, str(section["color"]))
    draw.rectangle((x, y + 42, x + width, y + 60), fill=str(section["color"]))
    draw.text((x + 26, y + 15), str(section["title"]), font=title_font, fill="#4B2D1F")
    draw_face(draw, x + width - 43, y + 31)

    text_x = x + 28
    text_y = y + 78
    max_text_width = width - 68
    for line in section["lines"]:  # type: ignore[index]
        wrapped = wrap_line(draw, str(line), body_font, max_text_width)
        draw.text((text_x, text_y), "◆", font=body_font, fill="#7B573B")
        for index, row in enumerate(wrapped):
            draw.text((text_x + 28, text_y + index * 28), row, font=body_font, fill="#4F382C")
        text_y += 28 * len(wrapped) + 8

def main() -> None:
    """生成并压缩帮助图。"""

    width = 1800
    margin = 52
    gap = 28
    columns = 3
    card_width = (width - margin * 2 - gap * (columns - 1)) // columns

    title_font = load_font(FONT_BOLD_PATHS, 52)
    subtitle_font = load_font(FONT_REGULAR_PATHS, 24)
    card_title_font = load_font(FONT_BOLD_PATHS, 30)
    body_font = load_font(FONT_REGULAR_PATHS, 21)
    footer_font = load_font(FONT_REGULAR_PATHS, 22)

    measure_image = Image.new("RGB", (width, 100), "#FFF8EE")
    measure_draw = ImageDraw.Draw(measure_image)

    column_y = [190 for _ in range(columns)]
    placements: list[tuple[dict[str, object], int, int, int]] = []
    for section in SECTIONS:
        height = card_height(measure_draw, section, body_font, card_width)
        column = min(range(columns), key=lambda index: column_y[index])
        x = margin + column * (card_width + gap)
        y = column_y[column]
        placements.append((section, x, y, height))
        column_y[column] += height + 30

    footer_height = 80
    height = max(column_y) + footer_height + 42

    image = Image.new("RGB", (width, height), "#FFF8EE")
    draw = ImageDraw.Draw(image)

    draw.ellipse((-80, 26, 220, 326), fill="#F7D7E7")
    draw.ellipse((width - 205, 10, width + 90, 320), fill="#CFE8F8")
    draw.ellipse((20, height - 230, 280, height + 50), fill="#DDF3D0")
    draw.ellipse((width - 270, height - 230, width + 60, height + 80), fill="#FFE89D")

    title = "修仙世界指令图"
    subtitle = "按当前 WS 实际注册命令整理；名称唯一，涉及对方可写名称或直接@"
    title_x = int((width - text_width(draw, title, title_font)) / 2)
    subtitle_x = int((width - text_width(draw, subtitle, subtitle_font)) / 2)
    draw.text((title_x, 48), title, font=title_font, fill="#5A3828")
    draw.text((subtitle_x, 118), subtitle, font=subtitle_font, fill="#7A6252")

    for section, x, y, card_h in placements:
        draw_card(draw, section, x, y, card_width, card_h, card_title_font, body_font)

    footer = "提示：带“数量、名称、地点名、武器ID、装备位、孔位”的命令需要补参数；涉及对方时可输入名称或直接@。"
    footer_y = height - footer_height
    draw_round_rect(draw, (margin, footer_y, width - margin, footer_y + 46), 18, "#FFFFFF", "#E1C09B", 2)
    footer_x = int((width - text_width(draw, footer, footer_font)) / 2)
    draw.text((footer_x, footer_y + 10), footer, font=footer_font, fill="#7A6252")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    palette_image = image.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    palette_image.save(OUTPUT, optimize=True)
    print(f"saved={OUTPUT}")
    print(f"size={OUTPUT.stat().st_size}")
    print(f"image={image.width}x{image.height}")

if __name__ == "__main__":
    main()
