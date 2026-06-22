"""修仙模块架构和基础业务自查。

运行方式：

    python test/修仙_架构业务自查.py

这个脚本检查“能启动”以外的规则：
- 修仙根目录不能反向导入中文玩法包；HTTP 路由组件例外。
- 中文二级包之间不能互相导入。
- 已删除的虫洞历史入口不能再被引用。
- 基础配置都能落到真实数据库表。
- WS 精确命令不能重复挂到不同函数。
- 战斗效果公共函数只能在 common.py 定义。
- 二级组件必须有说明文档，并写清命令/HTTP/回调入口和组件关联。
"""

from __future__ import annotations

import ast
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from launch.config import config
from 修仙.sql import TRADE_FORBIDDEN_SPECIALTY_TYPES, XiuxianDB
from 修仙.修仙物品.service import ItemInfoService


XIUXIAN_ROOT = PROJECT_ROOT / "修仙"
ROOT_ROUTER_COMPONENTS = {"后台接口", "修仙帮助"}


def main() -> None:
    """执行所有自查项。"""

    _check_import_boundaries()
    _check_component_docs()
    _check_no_removed_entry()
    _check_seed_data()
    _check_project_timezone()
    _check_treasure_detail_coverage()
    _check_known_effect_keys()
    _check_ws_command_duplicates()
    _check_deprecated_commands_removed()
    _check_shared_combat_helpers()
    print("修仙架构业务自查通过")


def _check_import_boundaries() -> None:
    """检查修仙根目录和中文二级包的导入边界。"""

    child_dirs = _child_dirs()
    violations: list[str] = []

    for file in XIUXIAN_ROOT.glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        violations.extend(_root_reverse_imports(file, tree, child_dirs))

    for file in XIUXIAN_ROOT.rglob("*.py"):
        rel = file.relative_to(XIUXIAN_ROOT)
        if len(rel.parts) < 2 or rel.parts[0] not in child_dirs:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        violations.extend(_child_cross_imports(file, tree, rel.parts[0], child_dirs))

    assert not violations, "修仙导入边界违规：\n" + "\n".join(violations)


def _check_component_docs() -> None:
    """检查二级组件说明文档是可被帮助站和百科消费的稳定文档。"""

    offenders: list[str] = []
    for child in sorted(_documented_child_dirs(), key=lambda item: item.name):
        doc = child / "说明.md"
        if not doc.exists():
            offenders.append(f"{child.name}: 缺少 说明.md")
            continue
        text = doc.read_text(encoding="utf-8")
        if not _has_heading(text, "组件关联"):
            offenders.append(f"{child.name}: 说明.md 缺少 ## 组件关联")
        if not any(_has_heading(text, heading) for heading in ("命令", "HTTP", "回调")):
            offenders.append(f"{child.name}: 说明.md 需要 ## 命令 / ## HTTP / ## 回调 之一")
        if _has_heading(text, "命令") and "```" not in _heading_section(text, "命令"):
            offenders.append(f"{child.name}: ## 命令 下需要代码块，供帮助站生成主要命令")
    assert not offenders, "组件说明文档不完整：\n" + "\n".join(offenders)


def _documented_child_dirs() -> list[Path]:
    """需要维护说明文档的二级中文组件目录。"""

    result: list[Path] = []
    for path in XIUXIAN_ROOT.iterdir():
        if not path.is_dir() or path.name.startswith("__"):
            continue
        if not any("\u4e00" <= char <= "\u9fff" for char in path.name):
            continue
        if (path / "__init__.py").exists() or (path / "service.py").exists():
            result.append(path)
    return result


def _has_heading(text: str, title: str) -> bool:
    """判断 Markdown 是否有指定标题。"""

    return any(line.strip() == f"## {title}" for line in text.splitlines())


def _heading_section(text: str, title: str) -> str:
    """截取指定二级标题下的正文。"""

    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {title}":
            start = index + 1
            break
    if start is None:
        return ""
    result: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        result.append(line)
    return "\n".join(result)


def _root_reverse_imports(file: Path, tree: ast.AST, child_dirs: set[str]) -> list[str]:
    """根目录公共文件不能导入中文二级包。"""

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            target_child = module.split(".", 1)[0]
            if node.level == 1 and target_child in child_dirs and target_child not in ROOT_ROUTER_COMPONENTS:
                violations.append(f"{file}:{node.lineno}: from .{module} import ...")
            if node.level == 0 and _absolute_child_module(module, child_dirs):
                violations.append(f"{file}:{node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _absolute_child_module(alias.name, child_dirs):
                    violations.append(f"{file}:{node.lineno}: import {alias.name}")
    return violations


def _child_cross_imports(file: Path, tree: ast.AST, current_child: str, child_dirs: set[str]) -> list[str]:
    """中文二级包只能导入根目录公共模块，不能导入其他中文二级包。"""

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 2:
                target_child = module.split(".", 1)[0]
                if target_child in child_dirs and target_child != current_child:
                    violations.append(f"{file}:{node.lineno}: from ..{module} import ...")
            if node.level == 0 and _absolute_child_module(module, child_dirs, current_child):
                violations.append(f"{file}:{node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _absolute_child_module(alias.name, child_dirs, current_child):
                    violations.append(f"{file}:{node.lineno}: import {alias.name}")
    return violations


def _absolute_child_module(module: str, child_dirs: set[str], current_child: str = "") -> bool:
    """判断绝对导入是否指向其他中文二级包。"""

    if not module.startswith("修仙."):
        return False
    parts = module.split(".")
    if len(parts) < 3 or parts[2] not in child_dirs:
        return False
    if not current_child and parts[2] in ROOT_ROUTER_COMPONENTS:
        return False
    return not current_child or parts[2] != current_child


def _check_no_removed_entry() -> None:
    """检查虫洞历史入口没有继续被引用。"""

    offenders = []
    current_file = Path(__file__).resolve()
    removed_tokens = ("wormhole" + "_core", "Wormhole" + "Core")
    for file in [*XIUXIAN_ROOT.rglob("*.py"), *PROJECT_ROOT.glob("test/修仙_*.py")]:
        if file.resolve() == current_file:
            continue
        text = file.read_text(encoding="utf-8")
        if any(token in text for token in removed_tokens):
            offenders.append(str(file))
    assert not offenders, "旧虫洞入口仍被引用：\n" + "\n".join(offenders)


def _check_seed_data() -> None:
    """检查基础配置能落表，且关键引用都能对上。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_audit.db")
        try:
            db.init()
            assert db.conn is not None
            conn = db.conn
            counts = {
                "item_defs": _count(conn, "item_defs"),
                "ring_item_defs": _count(conn, "ring_item_defs"),
                "trade_locations": _count(conn, "trade_locations"),
                "trade_goods": _count(conn, "trade_goods"),
                "monster_defs": _count(conn, "monster_defs"),
                "weapon_defs": _count(conn, "weapon_defs"),
                "weapon_skill_defs": _count(conn, "weapon_skill_defs"),
                "weapon_enchants": _count(conn, "weapon_enchants"),
            }
            empty = [name for name, count in counts.items() if count <= 0]
            assert not empty, "基础表为空：" + "、".join(empty)

            _assert_no_rows(
                conn,
                """
                SELECT g.item_id
                FROM trade_goods g
                LEFT JOIN item_defs i ON i.item_id = g.item_id
                WHERE i.item_id IS NULL
                """,
                "跑商商品没有对应背包物品定义",
            )
            _assert_no_rows(
                conn,
                """
                SELECT name, specialties
                FROM trade_locations
                WHERE (length(specialties) - length(replace(specialties, ',', '')) + 1) != 3
                """,
                "跑商地点必须正好 3 个特产",
            )
            _assert_no_rows(
                conn,
                """
                SELECT t.name
                FROM trade_locations t
                LEFT JOIN exploration_locations e ON e.name = t.name
                WHERE e.name IS NULL OR t.name = '太虚秘境'
                """,
                "跑商地点必须与普通探险地点重合，且不能包含太虚秘境",
            )
            _assert_no_rows(
                conn,
                """
                SELECT e.name
                FROM exploration_locations e
                LEFT JOIN trade_locations t ON t.name = e.name
                WHERE e.name != '太虚秘境' AND t.name IS NULL
                """,
                "普通探险地点必须都有跑商入口",
            )
            _assert_no_rows(
                conn,
                """
                SELECT name, json_extract(effect, '$.trade_type') AS trade_type
                FROM item_defs
                WHERE tradeable = 1
                  AND category = '纯经济'
                  AND json_extract(effect, '$.trade_group') != '纯经济'
                """,
                "跑商特产必须统一为纯经济商品",
            )
            forbidden_placeholders = ",".join("?" for _ in TRADE_FORBIDDEN_SPECIALTY_TYPES)
            _assert_no_rows(
                conn,
                f"""
                SELECT name, json_extract(effect, '$.trade_type') AS trade_type
                FROM item_defs
                WHERE tradeable = 1
                  AND category = '纯经济'
                  AND json_extract(effect, '$.trade_type') IN ({forbidden_placeholders})
                """,
                "跑商特产不能使用旧民生类或药路类",
                tuple(sorted(TRADE_FORBIDDEN_SPECIALTY_TYPES)),
            )
            _assert_no_rows(
                conn,
                """
                SELECT m.monster_id, m.drop_item_id
                FROM monster_defs m
                LEFT JOIN item_defs i ON i.item_id = m.drop_item_id
                WHERE m.drop_item_id IS NOT NULL
                  AND m.drop_item_id != ''
                  AND i.item_id IS NULL
                """,
                "怪物掉落没有对应背包物品定义",
            )
            _assert_no_rows(
                conn,
                """
                SELECT w.weapon_def_id, w.skill_id
                FROM weapon_defs w
                LEFT JOIN weapon_skill_defs s ON s.skill_id = w.skill_id
                WHERE s.skill_id IS NULL
                """,
                "武器没有对应自带技能",
            )
            _assert_no_rows(
                conn,
                """
                SELECT e.ring_item_id, e.name
                FROM ring_item_defs e
                LEFT JOIN weapon_enchants w ON w.enchant_id = json_extract(e.effect, '$.enchant_id')
                WHERE e.category = '技能书'
                  AND w.enchant_id IS NULL
                """,
                "技能书没有对应附魔定义",
            )
            _check_weapon_enchant_effect_keys(conn)
            _assert_no_rows(
                conn,
                """
                SELECT e.name
                FROM exploration_locations e
                LEFT JOIN world_locations w ON w.name = e.name
                WHERE w.name IS NULL
                """,
                "探险地点不能导航到",
            )
        finally:
            db.close()


def _check_project_timezone() -> None:
    """确认项目时区已经同步到当前进程，避免 Linux 日志时间跟本地时间错位。"""

    assert os.environ.get("TZ") == config.project.timezone, "PROJECT_TIMEZONE 没有同步到进程 TZ"
    if hasattr(time, "tzset"):
        expected = datetime.fromtimestamp(0, ZoneInfo(config.project.timezone))
        actual = time.localtime(0)
        actual_tuple = (actual.tm_year, actual.tm_mon, actual.tm_mday, actual.tm_hour)
        expected_tuple = (expected.year, expected.month, expected.day, expected.hour)
        assert actual_tuple == expected_tuple, "进程 localtime 没有按 PROJECT_TIMEZONE 生效"


def _check_treasure_detail_coverage() -> None:
    """检查所有基础资料都能通过 查看修仙物品 查到。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_detail_audit.db")
        try:
            db.init()
            service = ItemInfoService(db)
            service.create_player("audit_user", "自查道友")
            tables = (
                "item_defs",
                "ring_item_defs",
                "weapon_defs",
                "weapon_skill_defs",
                "weapon_enchants",
                "physique_defs",
            )
            offenders: list[str] = []
            for table in tables:
                for row in db.fetch_all(f"SELECT name FROM {table}"):
                    text = service.info("audit_user", row["name"])
                    if row["name"] not in text or "没有找到修仙物品" in text:
                        offenders.append(f"{table}/{row['name']}: {text}")
            assert not offenders, "修仙物品详情缺失：\n" + "\n".join(offenders)
        finally:
            db.close()


def _check_known_effect_keys() -> None:
    """检查物品、宝石、体质的效果字段都已经接入业务。"""

    allowed = {
        "base_enchant_id",
        "crit_resist_bonus",
        "defense_bonus",
        "dodge_bonus",
        "enchant_id",
        "explore_bonus",
        "home_location",
        "hp_delta",
        "hp_ratio",
        "max_hp_bonus",
        "max_mp_bonus",
        "mp_bonus",
        "mp_delta",
        "mp_ratio",
        "random_exp_max",
        "random_exp_min",
        "random_stones_max",
        "random_stones_min",
        "random_stones_segments",
        "recover_bonus",
        "source_stones_delta",
        "trade_bonus",
        "trade_group",
        "trade_type",
        "weapon_max_level_cap",
        "weapon_max_level_delta",
        "wash_physique",
        "world_category",
        "world_subtype",
    }
    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_effect_audit.db")
        try:
            db.init()
            offenders: list[str] = []
            for table in ("item_defs", "ring_item_defs", "physique_defs"):
                for row in db.fetch_all(f"SELECT name, effect FROM {table}"):
                    try:
                        effect = json.loads(row["effect"] or "{}")
                    except json.JSONDecodeError:
                        offenders.append(f"{table}/{row['name']}: effect 不是 JSON")
                        continue
                    for key in effect:
                        if key not in allowed:
                            offenders.append(f"{table}/{row['name']}: 未知效果字段 {key}")
            assert not offenders, "存在未接入业务的效果字段：\n" + "\n".join(offenders)
        finally:
            db.close()


def _check_weapon_enchant_effect_keys(conn) -> None:
    """检查技能书附魔字段都能被公共战斗公式消费。"""

    allowed = {
        "hit_bonus",
        "pierce_bonus",
        "life_steal",
        "shield_bonus",
        "counter_rate",
        "mp_suppress",
        "defense_suppress",
        "combo_bonus",
        "damage_reduce",
        "skill_power_bonus",
        "heavy_bonus",
        "combo_damage_bonus",
        "single_hit_bonus",
        "dodge_bonus",
        "burn_rate",
        "bleed_rate",
        "stun_rate",
        "interval_delta",
        "interval_rate",
    }
    offenders: list[str] = []
    rows = conn.execute("SELECT name, effect FROM weapon_enchants").fetchall()
    for row in rows:
        try:
            effect = json.loads(row["effect"] or "{}")
        except json.JSONDecodeError:
            offenders.append(f"{row['name']}: effect 不是 JSON")
            continue
        for key in effect:
            if key not in allowed:
                offenders.append(f"{row['name']}: 未知效果字段 {key}")
    assert not offenders, "技能书存在未接入战斗公式的字段：\n" + "\n".join(offenders)


def _check_ws_command_duplicates() -> None:
    """检查修仙 WS 精确命令没有重复挂到多个函数。"""

    command_map: dict[str, str] = {}
    duplicates: list[str] = []
    for command, current in _iter_ws_commands():
        old = command_map.get(command)
        if old and old != current:
            duplicates.append(f"{command}: {old} / {current}")
        else:
            command_map[command] = current
    assert command_map, "没有扫描到任何修仙 WS 精确命令"
    assert not duplicates, "WS 命令重复：\n" + "\n".join(duplicates)


def _check_deprecated_commands_removed() -> None:
    """检查已经废弃的历史命令没有重新注册。

    当前修仙模块是“新开始”，只保留正式命令。
    例外包括自然入口和手动保留的顺口别名：
    `帮助/修仙帮助`、`结束休息/休息结束`、
    `修仙信息/状态`、`升级源库/源库升级`、`存入源石/源石存入`、`取出源石/源石取出`。
    """

    deprecated = {
        "用户创建",
        "礼包",
        "获取源库",
        "源库获取",
        "结息源库",
        "地点",
        "探索",
        "状态探险",
        "探索状态",
        "结束探索",
        "探索结束",
        "记录掉落",
        "跑商",
        "列表商场",
        "跑商列表",
        "详情商场",
        "跑商详情",
        "市价商场",
        "跑商市价",
        "购买商场",
        "跑商购买",
        "出售商场",
        "跑商出售",
        "自动出售商场",
        "跑商自动出售",
        "推荐商场",
        "跑商推荐",
        "记录商场",
        "限制商场",
        "奖励商场",
        "奖励跑商",
        "收购特殊",
        "收购",
        "出售特殊",
        "战利品出售",
        "自动出售特殊",
        "前往",
        "二手",
        "上架二手市场",
        "二手上架",
        "下架二手市场",
        "二手下架",
        "购买二手市场",
        "二手购买",
        "武器切换",
        "换武器",
        "武器升级",
        "武器回收",
        "回收武器",
        "技能书回收",
        "回收技能书",
        "武器附魔",
        "升级装备",
        "升级宝石",
        "宝石回收",
        "回收宝石",
        "装备铭刻",
        "武器铭刻",
        "附魔铭刻",
        "技能铭刻",
        "铭刻武器技能",
        "武器技能铭刻",
        "铭刻自带技能",
        "自带技能铭刻",
        "切磋接受",
        "切磋拒绝",
        "决斗接受",
        "决斗拒绝",
        "记录决斗",
        "异界虫洞",
        "状态虫洞",
        "虫洞挑战",
        "排行虫洞",
        "奖励虫洞",
        "状态首领",
        "首领挑战",
        "排行首领",
        "奖励首领",
        "查看背包",
        "背包查看",
        "查看纳戒",
        "纳戒查看",
        "查看装备库",
        "装备库查看",
        "装备库",
        "使用装备库",
        "装备库使用",
        "物品库",
        "物品",
        "武器查看",
        "查看装备",
        "装备查看",
        "查看孔位",
        "孔位查看",
        "我的宝石",
        "查看宝石",
        "宝石查看",
        "新手指引",
        "修行札记",
        "札记",
        "今日风云榜",
        "江湖大事记",
        "今日小报",
        "修仙小报",
        "今日气运",
        "气运",
    }
    command_map = dict(_iter_ws_commands())
    offenders = sorted(command for command in deprecated if command in command_map)
    assert not offenders, "历史命令不应再注册：" + "、".join(offenders)


def _check_shared_combat_helpers() -> None:
    """检查战斗效果公式没有在玩法组件里复制一份。

    技能书、武器类型、宝石和体质都会影响战斗。
    这些公式只能在 common.py 的 CoreService 中维护，避免探险、虫洞、首领、武器详情各算各的。
    """

    helper_names = {
        "_weapon_effects",
        "_merge_effects",
        "_attack_raw",
        "_skill_power",
        "_skill_cost",
        "_skill_interval",
        "_pierce_rate",
        "_combo_damage",
        "_reduce_damage",
        "_suppress_mp",
    }
    allowed = XIUXIAN_ROOT / "common.py"
    offenders: list[str] = []
    for file in XIUXIAN_ROOT.rglob("*.py"):
        if file == allowed:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in helper_names:
                offenders.append(f"{file}:{node.lineno}: {node.name}")
    assert not offenders, "战斗公共函数被重复定义：\n" + "\n".join(offenders)


def _iter_ws_commands() -> list[tuple[str, str]]:
    """收集修仙二级包中注册的全部精确命令。"""

    commands: list[tuple[str, str]] = []
    scanned_files = 0
    for file in XIUXIAN_ROOT.glob("*/*__init__.py"):
        scanned_files += 1
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                current = f"{file}:{node.lineno}:{node.name}"
                for command in _handler_commands(decorator):
                    commands.append((command, current))
    assert scanned_files > 0, "没有扫描到修仙二级包命令文件"
    return commands


def _handler_commands(decorator: ast.AST) -> list[str]:
    """从 @WsMessageHandler.handler(cmd=...) 里取出精确命令。"""

    if not isinstance(decorator, ast.Call):
        return []
    func = decorator.func
    if not isinstance(func, ast.Attribute) or func.attr != "handler":
        return []
    for keyword in decorator.keywords:
        if keyword.arg != "cmd":
            continue
        try:
            value = ast.literal_eval(keyword.value)
        except (SyntaxError, ValueError):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple | list):
            return [item for item in value if isinstance(item, str)]
    return []


def _child_dirs() -> set[str]:
    """读取中文二级包目录名。"""

    return {
        path.name
        for path in XIUXIAN_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__") and any("\u4e00" <= char <= "\u9fff" for char in path.name)
    }


def _count(conn, table: str) -> int:
    """读取表行数。"""

    row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
    return int(row["total"] if row else 0)


def _assert_no_rows(conn, sql: str, title: str, params: tuple = ()) -> None:
    """断言查询没有异常记录。"""

    rows = conn.execute(sql, params).fetchall()
    assert not rows, title + "：\n" + "\n".join(str(dict(row)) for row in rows)


if __name__ == "__main__":
    main()
