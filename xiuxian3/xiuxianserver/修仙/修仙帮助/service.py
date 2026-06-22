"""修仙帮助组件服务。"""

from __future__ import annotations

from pathlib import Path

from launch import config

from ..common import CoreService
from ..format_text import T
from ..sql import db

HELP_IMAGE = Path(__file__).with_name("help.png")


def _help_page_url() -> str:
    """按 .env 中配置的公开域名生成帮助页地址。"""

    port = str(config.server.port)
    domain = (config.project.domain or "127.0.0.1").strip().rstrip("/")
    base_url = _with_project_port(domain, port)
    return f"{base_url}/xiuxian/help"


def _with_project_port(domain: str, port: str) -> str:
    """生成项目访问基地址；80 端口不展示，其他端口自动展示。"""

    if domain.startswith(("http://", "https://")):
        scheme, rest = domain.split("://", 1)
    else:
        scheme, rest = "http", domain

    host, _, path = rest.partition("/")
    hostname = host
    explicit_port = ""
    if ":" in host and not host.startswith("["):
        hostname, explicit_port = host.rsplit(":", 1)

    final_port = explicit_port or port
    netloc = hostname if final_port == "80" else f"{hostname}:{final_port}"
    suffix = f"/{path.strip('/')}" if path else ""
    return f"{scheme}://{netloc}{suffix}".rstrip("/")


HELP_PAGE_URL = _help_page_url()
GUIDE_SECTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "成长": (
        "修行成长",
        "角色状态、每日收益、休息恢复和源石管理。",
        ("修仙信息", "状态", "签到", "新手礼包", "休息", "结束休息", "源库", "源库结息", "升级源库"),
    ),
    "行囊": (
        "行囊装备",
        "背包、纳戒、保险箱、武器、装备、宝石和特殊消耗品。",
        ("背包", "纳戒", "保险箱", "武器", "装备", "孔位", "宝石", "洗髓", "武器淬锋", "铭刻之羽"),
    ),
    "战斗": (
        "探险战斗",
        "探险、休整、首领、虫洞和玩家对战。",
        ("地图", "探险列表", "探险状态", "结束探险", "首领", "首领状态", "挑战首领", "首领奖励", "虫洞", "虫洞奖励", "决斗记录"),
    ),
    "交易": (
        "交易流通",
        "跑商、出售、藏宝图、二手市场和背包清理。",
        ("商场推荐", "跑商记录", "跑商限制", "跑商奖励", "自动出售", "出售全部 武器", "出售全部 宝石", "出售全部 技能书", "藏宝图", "领取藏宝图", "二手市场"),
    ),
    "世界": (
        "宗门世界",
        "地图、宗门、世界记录、百科和帮助文档。",
        ("地图", "宗门", "宗门战", "领取宗门战奖励", "风云榜", "修仙早报", "修仙界历史", "修仙百科 武器", "修仙百科 跑商", "帮助", "修仙帮助"),
    ),
}
GUIDE_ALIASES = {
    "主": "",
    "主页": "",
    "总览": "",
    "成长": "成长",
    "玩家": "成长",
    "修炼": "成长",
    "行囊": "行囊",
    "装备": "行囊",
    "物品": "行囊",
    "纳戒": "行囊",
    "战斗": "战斗",
    "探险": "战斗",
    "首领": "战斗",
    "虫洞": "战斗",
    "交易": "交易",
    "商场": "交易",
    "跑商": "交易",
    "出售": "交易",
    "世界": "世界",
    "宗门": "世界",
    "地图": "世界",
    "历史": "世界",
}


class HelpService(CoreService):
    """修仙帮助图片、网页入口和固定导航。"""

    def web_help(self) -> str:
        """返回当前阶段的帮助入口提示。"""

        return f"修仙帮助网页：{HELP_PAGE_URL}\n发送：修仙帮助 查看帮助图，或发送：指南 查看关键入口。"

    def command_guide(self, section: str = "") -> str:
        """返回分方向的关键组件跳转按钮。"""

        key = self._guide_key(section)
        if not key:
            return self._guide_index()
        if key not in GUIDE_SECTIONS:
            return T.hint(
                f"没有这个指南方向：{section.strip()}。",
                "可用方向：成长、行囊、战斗、交易、世界。",
                buttons=self._guide_section_buttons(),
            )
        return self._guide_section(key)

    def _guide_index(self) -> str:
        """返回指南方向首页。"""

        panel = T.panel()
        panel.section("指南")
        panel.line("关键业务已经按方向拆分，先选方向，再进入具体按钮。")
        for key, (title, desc, _commands) in GUIDE_SECTIONS.items():
            panel.line(f"{title}：{desc} 发送：指南 {key}")
        return panel.render() + T.buttons(*self._guide_section_buttons())

    def _guide_section(self, key: str) -> str:
        """返回单个方向的按钮页。"""

        title, desc, commands = GUIDE_SECTIONS[key]
        panel = T.panel()
        panel.section(f"指南·{title}")
        panel.line(desc)
        panel.line("只放固定命令按钮；需要补参数的命令请看帮助网页或对应功能返回文本。")
        return panel.render() + T.buttons(*commands, "指南")

    @staticmethod
    def _guide_key(section: str) -> str:
        """解析指南方向别名。"""

        value = str(section or "").strip()
        if not value:
            return ""
        return GUIDE_ALIASES.get(value, value)

    @staticmethod
    def _guide_section_buttons() -> tuple[str, ...]:
        """主指南方向按钮。"""

        return tuple(f"指南 {key}:{title}" for key, (title, _desc, _commands) in GUIDE_SECTIONS.items())


service = HelpService(db)
