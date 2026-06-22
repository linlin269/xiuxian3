"""修仙模块通用能力。

根目录只放基础函数和公共服务，不反向导入各个玩法包。
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable

from .constants import (
    BATTLE_RECORD_RETENTION_DAYS,
    CITY_MAX_LEVEL,
    DAY_RESET_HOUR,
    DEFAULT_BACKPACK_LIMIT,
    DEFAULT_LOCATION,
    DEFAULT_WEIGHT_LIMIT,
    DIRECT_FLOW_RETENTION_DAYS,
    EQUIPMENT_SLOTS,
    FIXED_EQUIPMENT_SLOT_FACTORS,
    MAX_LEVEL,
    NEWSPAPER_RETENTION_DAYS,
    RENAME_COOLDOWN_HOURS,
    SECT_LEVEL_MAX,
    WEAPON_TYPE_INTERVAL_FACTORS,
    WORLD_LONG_RECORD_RETENTION_DAYS,
    WORLD_SHORT_RECORD_RETENTION_DAYS,
)
from .format_text import T
from .rules import (
    base_attack,
    damage_after_defense,
    defense,
    exp_need,
    level_from_exp,
    max_hp,
    max_mp,
    money,
    player_exp_for_level,
    weapon_enchant_slots,
    weapon_exp_for_level,
    weapon_level_from_exp,
)

FORTUNE_POOL = (
    ("平运", "山河无事，适合稳稳修行。", {}),
    ("小吉", "袖中有风，适合出门寻机缘。", {"explore_bonus": 0.04}),
    ("中吉", "市声入怀，买卖时少些磕绊。", {"trade_bonus": 0.03}),
    ("上吉", "灵台清明，调息恢复更顺。", {"recover_bonus": 0.08}),
    ("轻身", "云履无痕，斗法时身形更活。", {"dodge_bonus": 0.04}),
    ("定心", "心火不摇，承伤更稳。", {"crit_resist_bonus": 0.05}),
    ("天眷", "今日天光偏爱，万事都略顺半分。", {"explore_bonus": 0.08, "recover_bonus": 0.05}),
    ("破云", "心气如剑，出手时更敢一线。", {"damage_reduce": 0.03, "dodge_bonus": 0.03}),
)


WORLD_WEATHER_POOL = (
    ("晴岚", "云开风轻，山路和商道都显得明快。", {"explore_bonus": 0.03}),
    ("灵雨", "细雨带着灵气落下，调息恢复更顺。", {"recover_bonus": 0.04}),
    ("雾锁", "雾色压低远路视野，跑商略有磕绊。", {"trade_bonus": -0.006}),
    ("星夜", "星光清冷，斗法时心神更定。", {"crit_resist_bonus": 0.025}),
    ("风起", "长风推人前行，身法更轻。", {"dodge_bonus": 0.025}),
    ("沉云", "云层压境，万事平稳无额外加成。", {}),
)

WORLD_TIDE_POOL = (
    ("灵潮平稳", "天地灵息如常，适合照旧修行。", {}),
    ("灵潮微涨", "灵息浮动，探险寻物更容易撞见机会。", {"explore_bonus": 0.04}),
    ("商气东来", "坊市人声热闹，跑商手续费略降。", {"trade_bonus": 0.012}),
    ("回春潮", "草木气息回环，休息与恢复效果更好。", {"recover_bonus": 0.05}),
    ("剑气鸣野", "天地间有锋芒回响，出手更稳。", {"damage_reduce": 0.02}),
    ("静海潮", "灵潮安静，承伤更稳。", {"crit_resist_bonus": 0.03}),
)


PERCENT_BONUS_CAPS = {
    "dodge_bonus": 0.35,
    "recover_bonus": 0.50,
    "explore_bonus": 0.20,
    "trade_bonus": 0.20,
    "crit_resist_bonus": 0.45,
}


LIFETIME_STATS_STARTED_AT_KEY = "lifetime_stats_started_at"
GAME_LOG_LIFETIME_STATS = {
    "签到": "sign_count",
    "改名": "rename_count",
    "新手礼包": "newbie_gift_count",
    "使用物品": "item_use_count",
    "铭刻装备": "inscription_count",
    "铭刻武器": "inscription_count",
    "铭刻附魔": "inscription_count",
    "铭刻自带技能": "inscription_count",
}


WEAPON_TYPE_STYLE_TEXT = {
    "匕": "极速近身，靠高频出手、吸血和闪避找机会",
    "飞刃": "高频游斗，适合连击、打断和清小怪",
    "剑": "均衡灵活，速度和伤害都比较稳",
    "铃": "轻灵扰神，偏精神压制和节奏干扰",
    "刀": "均衡爆发，单次伤害和出手节奏都适中",
    "弩": "远程点杀，伤害稳定但蓄势略慢",
    "拂尘": "多段牵引，适合连击和持续压制",
    "杖": "术法续航，偏恢复、精神和控制",
    "枪": "穿透突进，伤害高但节奏偏沉",
    "盾刃": "攻守兼备，输出较慢但更抗打",
    "戟": "重兵破阵，高伤慢速，适合破防",
    "盘": "法器镇压，单次很重，出手和蓄势都慢",
    "斧": "极重爆发，伤害最高档，但速度代价明显",
}


def now() -> datetime:
    """返回当前时间。"""

    return datetime.now()


def ts(value: datetime | None = None) -> str:
    """把时间转成数据库保存的字符串。"""

    return (value or now()).isoformat(timespec="seconds")


def dt(value: str | None) -> datetime | None:
    """把数据库时间字符串转成时间对象。"""

    if not value:
        return None
    return datetime.fromisoformat(value)


def business_day(value: datetime | None = None) -> str:
    """按每日 04:00 计算业务日。"""

    return ((value or now()) - timedelta(hours=DAY_RESET_HOUR)).date().isoformat()


def world_state_for_day(day: str | None = None) -> dict[str, Any]:
    """按业务日稳定生成全服天气和灵潮。"""

    current_day = day or business_day()
    weather = _stable_choice(WORLD_WEATHER_POOL, f"{current_day}:weather")
    tide = _stable_choice(WORLD_TIDE_POOL, f"{current_day}:tide")
    return {
        "business_day": current_day,
        "weather": _world_entry("weather", weather),
        "tide": _world_entry("tide", tide),
        "effect": merge_numeric_effects(weather[2], tide[2]),
    }


def merge_numeric_effects(*effects: dict[str, Any]) -> dict[str, float]:
    """合并多个加成字典，只保留数值型效果。"""

    merged: dict[str, float] = {}
    for effect in effects:
        for key, value in effect.items():
            if isinstance(value, int | float):
                merged[key] = merged.get(key, 0) + float(value)
    return merged


def soft_cap_percent_bonus(raw: float, cap: float) -> float:
    """给百分比加成做收益递减封顶；负面效果保持原值。"""

    if raw <= 0:
        return raw
    return cap * raw / (raw + cap)


def _stable_choice(pool: tuple[tuple[str, str, dict[str, Any]], ...], seed_text: str) -> tuple[str, str, dict[str, Any]]:
    """用业务日文字稳定选择一项，避免服务重启后变化。"""

    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(pool)
    return pool[index]


def _world_entry(kind: str, row: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """把天气/灵潮定义整理成统一结构。"""

    name, flavor, effect = row
    return {
        "kind": kind,
        "name": name,
        "flavor": flavor,
        "effect": dict(effect),
    }


def to_int(value: object, default: int = 0) -> int:
    """把输入转成整数。"""

    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_json(text: object, default: Any = None) -> Any:
    """安全读取 JSON 字段。"""

    if default is None:
        default = {}
    if not text:
        return default
    try:
        return json.loads(str(text))
    except json.JSONDecodeError:
        return default


def dump_json(data: Any) -> str:
    """把对象保存成 JSON 文本。"""

    return json.dumps(data, ensure_ascii=False)


def validate_name(name: str) -> tuple[bool, str]:
    """校验展示名称。"""

    clean = name.strip()
    if len(clean) < 2 or len(clean) > 12:
        return False, "名称需要 2 到 12 个字符。"
    if any(ch.isspace() for ch in clean):
        return False, "名称中不能包含空白字符。"
    return True, clean


def row_value(row: Any, key: str, default: Any = "") -> Any:
    """从 dict 或 sqlite.Row 里安全取值。"""

    if row is None:
        return default
    try:
        if hasattr(row, "get"):
            value = row.get(key, default)
        else:
            value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def custom_label(base_name: object, custom_name: object = "") -> str:
    """优先展示自定义名，同时保留原名方便识别。"""

    base = str(base_name or "").strip()
    custom = str(custom_name or "").strip()
    return f"{custom}（{base}）" if custom else base


def fixed_equipment_label(equipment: Any) -> str:
    """装备展示名：自定义名（原部位）。"""

    return custom_label(row_value(equipment, "slot"), row_value(equipment, "custom_name"))


def weapon_label_name(weapon: Any) -> str:
    """武器展示名：自定义名（原模板名）。"""

    return custom_label(row_value(weapon, "name"), row_value(weapon, "custom_name"))


def weapon_id_label(weapon_id: object) -> str:
    """武器实例编号展示，避开 Markdown 标题/列表等特殊符号。"""

    return f"〔{str(weapon_id).strip()}〕"


def enchant_label_name(enchant_name: object, custom_name: object = "") -> str:
    """附魔展示名：自定义名（原技能书名）。"""

    return custom_label(enchant_name, custom_name)


def ring_item_use_hint(item: dict[str, Any]) -> str:
    """按纳戒物品类型给出正确消耗入口。"""


    if item["name"] == "洗髓液":
        return "<洗髓><宝石><武器>"
    if item["name"] == "淬锋丹":
        return "淬锋丹由纳戒承接消耗，请发送：武器淬锋；默认淬锋已装备武器，也可发送：武器淬锋 武器ID。<纳戒><武器>"
    if item["category"] == "宝石":
        return "宝石请发送：镶嵌 装备位 孔位号 宝石名称；同名多等级时加等级，例如：护心玉 2级。<洗髓><宝石><武器>"
    if item["category"] == "技能书":
        return "技能书请发送：附魔武器 武器ID 技能书名；武器 ID 支持 武器#12、#12、12。<洗髓><宝石><武器>"
    if item["name"] == "开孔器":
        return "开孔器由纳戒承接消耗，请发送：开孔 装备位。<纳戒><宝石><装备>"
    return "只有恢复类物品可以直接发送：使用 物品名，或使用 物品名 数量。<洗髓><宝石><武器>"


def split_words(message: str) -> list[str]:
    """按空白拆参数。"""

    return [part for part in message.strip().split() if part]


def parse_name_level(text: str) -> tuple[str, int | None]:
    """解析“名称 2级 / 名称 Lv2”，没有等级时返回 None。"""

    parts = split_words(text)
    if not parts:
        return "", None

    token = parts[-1].strip().lower()
    level_text = ""
    if token.endswith("级"):
        level_text = token[:-1]
    elif token.startswith("lv"):
        level_text = token[2:]

    if level_text.isdigit() and len(parts) > 1:
        return " ".join(parts[:-1]), max(1, to_int(level_text, 1))
    return text.strip(), None


def parse_name_quantity_optional(text: str, default: int = 1) -> tuple[str, int]:
    """解析“名称 [数量]”，没有数量时使用默认值。"""

    parts = split_words(text)
    if not parts:
        return "", 0
    if len(parts) > 1 and parts[-1].lstrip("+-").isdigit():
        return " ".join(parts[:-1]), to_int(parts[-1], default)
    return text.strip(), default


def parse_weapon_ref(text: str) -> int:
    """把 武器#12 / 武器ID12 / #12 / 12 转成武器实例 ID。"""

    value = text.strip()
    for prefix in ("武器#", "武器ID", "武器", "#"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return to_int(value)


def quality_factor(quality: str) -> float:
    """返回品质系数。"""

    return {"凡品": 1.0, "良品": 1.4, "珍品": 2.0, "稀品": 3.0}.get(quality, 1.0)


def random_quality() -> str:
    """随机品质。"""

    return random.choices(("凡品", "良品", "珍品", "稀品"), weights=(60, 28, 10, 2), k=1)[0]


WEAPON_TYPE_ATTACK_BASE_FACTORS = {
    "匕": 0.90,
    "飞刃": 0.94,
    "铃": 0.90,
    "剑": 1.00,
    "刀": 1.05,
    "弩": 1.08,
    "拂尘": 1.00,
    "杖": 0.98,
    "枪": 1.16,
    "盾刃": 1.08,
    "戟": 1.22,
    "盘": 1.25,
    "斧": 1.30,
}

WEAPON_TYPE_ATTACK_GROWTH_FACTORS = {
    "匕": 1.08,
    "飞刃": 1.05,
    "铃": 1.00,
    "剑": 1.00,
    "刀": 0.98,
    "弩": 0.96,
    "拂尘": 0.94,
    "杖": 0.92,
    "枪": 0.92,
    "盾刃": 0.90,
    "戟": 0.96,
    "盘": 0.95,
    "斧": 0.96,
}


def weapon_attack_value(base_attack_value: object, quality: object, level: object, weapon_type: object) -> int:
    """按武器模板、品质、等级和类型实时计算武器攻击。

    `player_weapons.attack` 已不再作为真实业务值；攻击力是派生数值。
    轻武器成长更顺，重武器保留更高单击但不再随高基础攻击指数膨胀。
    """

    base_attack_int = max(1, to_int(base_attack_value, 1))
    level_int = max(0, min(MAX_LEVEL, to_int(level, 0)))
    quality_value = max(1.0, quality_factor(str(quality or "")))
    weapon_type_text = str(weapon_type or "")
    base_factor = WEAPON_TYPE_ATTACK_BASE_FACTORS.get(weapon_type_text, 1.0)
    growth_factor = WEAPON_TYPE_ATTACK_GROWTH_FACTORS.get(weapon_type_text, 1.0)

    base_part = base_attack_int * quality_value * base_factor
    quality_growth = 0.75 + quality_value * 0.25
    early_level = min(level_int, 60)
    late_level = max(0, level_int - 60)
    early_growth = early_level * (1.4 + base_attack_int * 0.06) * quality_growth * growth_factor
    late_growth = late_level * (0.6 + base_attack_int * 0.025) * quality_growth * growth_factor
    return max(1, int(base_part + early_growth + late_growth))


def computed_weapon_attack(weapon: Any | None) -> int:
    """读取武器实时攻击。"""

    if not weapon:
        return 0
    base_attack_value = to_int(row_value(weapon, "base_attack", 0))
    if base_attack_value <= 0:
        return 0
    return weapon_attack_value(
        base_attack_value,
        row_value(weapon, "quality", "凡品"),
        row_value(weapon, "level", 0),
        row_value(weapon, "weapon_type", ""),
    )


def computed_weapon_enchant_slots(weapon: Any | None) -> int:
    """按武器等级实时计算可用附魔栏，已附魔技能永不被吞掉。"""

    if not weapon:
        return 0
    raw_enchants = row_value(weapon, "enchant_effects", "[]")
    enchant_ids = raw_enchants if isinstance(raw_enchants, list) else load_json(raw_enchants, [])
    used_slots = len(enchant_ids) if isinstance(enchant_ids, list) else 0
    dynamic_slots = weapon_enchant_slots(
        to_int(row_value(weapon, "max_level", 0)),
        to_int(row_value(weapon, "level", 0)),
    )
    return max(0, dynamic_slots, used_slots)


def computed_weapon_potential_slots(weapon: Any | None) -> int:
    """读取武器满级后的潜力附魔栏。"""

    if not weapon:
        return 0
    raw_enchants = row_value(weapon, "enchant_effects", "[]")
    enchant_ids = raw_enchants if isinstance(raw_enchants, list) else load_json(raw_enchants, [])
    used_slots = len(enchant_ids) if isinstance(enchant_ids, list) else 0
    potential_slots = weapon_enchant_slots(
        to_int(row_value(weapon, "max_level", 0)),
        to_int(row_value(weapon, "max_level", 0)),
    )
    return max(0, potential_slots, used_slots)


class CoreService:
    """所有玩法服务共享的基础能力。"""

    def __init__(self, database: Any) -> None:
        self.db = database

    def player(self, client_id: str) -> dict[str, Any] | None:
        """读取玩家。"""

        return self.db.fetch_one("SELECT * FROM players WHERE client_id = ?", (client_id,))

    @staticmethod
    def weapon_attack(weapon: Any | None) -> int:
        """读取武器实时攻击。"""

        return computed_weapon_attack(weapon)

    @staticmethod
    def weapon_enchant_slots(weapon: Any | None) -> int:
        """读取武器实时可用附魔栏。"""

        return computed_weapon_enchant_slots(weapon)

    @staticmethod
    def weapon_potential_slots(weapon: Any | None) -> int:
        """读取武器满级潜力附魔栏。"""

        return computed_weapon_potential_slots(weapon)

    def player_by_ref(self, ref: str) -> dict[str, Any] | None:
        """按 client_id 或展示名读取玩家。

        WS 层已经把 CQ/at 转成 client_id；普通文本则传展示名。
        所有“指定其他玩家”的业务都走这里，避免各组件各写一套。
        """

        value = str(ref).strip()
        if not value:
            return None

        player = self.player(value)
        if player:
            return player
        return self.db.fetch_one(
            "SELECT * FROM players WHERE display_name = ?",
            (value,),
        )

    def player_id_by_ref(self, ref: str) -> str:
        """按 client_id 或展示名读取玩家 id；找不到时返回空字符串。"""

        player = self.player_by_ref(ref)
        return str(player["client_id"]) if player else ""

    def player_id_from_last_arg(self, message: str) -> str:
        """取最后一个参数作为玩家引用，支持 client_id 和展示名。"""

        parts = split_words(message)
        return self.player_id_by_ref(parts[-1]) if parts else ""

    def equipped_weapon_row(self, client_id: str) -> dict[str, Any] | None:
        """读取玩家当前装备的武器；不自动补初始武器。"""

        return self.db.fetch_one(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ? AND w.equipped = 1
            LIMIT 1
            """,
            (client_id,),
        )

    def equipped_flame_multiplier(self, client_id: str) -> float:
        """读取玩家当前装备异火的攻击倍率，默认 1.0。"""

        row = self.db.fetch_one(
            """
            SELECT fd.attack_multiplier
            FROM player_flames pf
            JOIN flame_defs fd ON fd.flame_id = pf.flame_id
            WHERE pf.client_id = ? AND pf.equipped = 1
            """,
            (client_id,),
        )
        return float(row["attack_multiplier"]) if row else 1.0

    def equipped_flame_name(self, client_id: str) -> str:
        """读取玩家当前装备异火名称，未装备返回空字符串。"""

        row = self.db.fetch_one(
            """
            SELECT fd.name
            FROM player_flames pf
            JOIN flame_defs fd ON fd.flame_id = pf.flame_id
            WHERE pf.client_id = ? AND pf.equipped = 1
            """,
            (client_id,),
        )
        return str(row["name"]) if row else ""

    def final_attack(self, client_id: str, player: dict[str, Any] | None = None, weapon: dict[str, Any] | None = None) -> int:
        """计算最终攻击：floor((base_attack + weapon_attack) * flame_multiplier)。"""

        if player is None:
            player = self.player(client_id) or {}
        if weapon is None:
            weapon = self.equipped_weapon_row(client_id)
        base = int(player.get("base_attack", 5)) + self.weapon_attack(weapon)
        multiplier = self.equipped_flame_multiplier(client_id)
        import math
        return max(1, math.floor(base * multiplier))

    def ensure_player_weapon(self, client_id: str) -> None:
        """保证玩家至少有一把已装备武器。"""

        with self.db.transaction() as conn:
            self.ensure_player_weapon_conn(conn, client_id)

    def ensure_player_weapon_conn(self, conn: sqlite3.Connection, client_id: str) -> None:
        """事务内保证玩家至少有一把已装备武器。"""

        equipped = conn.execute(
            "SELECT weapon_id FROM player_weapons WHERE holder_id = ? AND equipped = 1 LIMIT 1",
            (client_id,),
        ).fetchone()
        if equipped:
            return
        first = conn.execute(
            "SELECT weapon_id FROM player_weapons WHERE holder_id = ? ORDER BY weapon_id LIMIT 1",
            (client_id,),
        ).fetchone()
        if first:
            conn.execute(
                "UPDATE player_weapons SET equipped = CASE WHEN weapon_id = ? THEN 1 ELSE 0 END WHERE holder_id = ?",
                (int(first["weapon_id"]), client_id),
            )
            return
        weapon_def = conn.execute(
            "SELECT 1 FROM weapon_defs WHERE weapon_def_id = ?",
            ("qinglan_duanjian",),
        ).fetchone()
        if not weapon_def:
            return
        cursor = conn.execute(
            """
            INSERT INTO player_weapons
            (holder_id, weapon_def_id, level, max_level, quality, equipped, enchant_effects, custom_name, created_at)
            VALUES (?, 'qinglan_duanjian', 0, 40, '凡品', 1, ?, '', ?)
            """,
            (client_id, dump_json([]), ts()),
        )
        self.record_weapon_created_conn(conn, client_id, int(cursor.lastrowid))

    def player_name_taken(self, display_name: str, exclude_client_id: str | None = None) -> bool:
        """判断展示名称是否已被其他玩家使用。"""

        if exclude_client_id is None:
            row = self.db.fetch_one(
                "SELECT 1 FROM players WHERE display_name = ? LIMIT 1",
                (display_name,),
            )
        else:
            row = self.db.fetch_one(
                "SELECT 1 FROM players WHERE display_name = ? AND client_id != ? LIMIT 1",
                (display_name, exclude_client_id),
            )
        return bool(row)

    def recycle_location(self, location_name: str, recycle_type: str | None = None) -> dict[str, Any] | None:
        """读取系统回收地点；可按类型过滤。"""

        name = location_name.strip()
        if recycle_type is None:
            return self.db.fetch_one("SELECT * FROM recycle_locations WHERE name = ?", (name,))
        return self.db.fetch_one(
            "SELECT * FROM recycle_locations WHERE name = ? AND recycle_type = ?",
            (name, recycle_type),
        )

    def require_player(self, client_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """要求玩家已创建。"""

        player = self.player(client_id)
        if not player:
            return None, T.hint("你还没有创建用户。", "发送：创建用户 名称，例如：创建用户 青衫客")
        return player, None

    def cleanup_battle_records(self, force: bool = False) -> None:
        """每天最多清理一次可直接过期的流水，避免明细记录长期堆积。"""

        today = business_day()
        battle_cutoff = ts(now() - timedelta(days=BATTLE_RECORD_RETENTION_DAYS))
        direct_cutoff = ts(now() - timedelta(days=DIRECT_FLOW_RETENTION_DAYS))
        world_short_cutoff = ts(now() - timedelta(days=WORLD_SHORT_RECORD_RETENTION_DAYS))
        world_long_cutoff = ts(now() - timedelta(days=WORLD_LONG_RECORD_RETENTION_DAYS))
        direct_business_day = business_day(now() - timedelta(days=DIRECT_FLOW_RETENTION_DAYS))
        newspaper_business_day = business_day(now() - timedelta(days=NEWSPAPER_RETENTION_DAYS))
        with self.db.transaction() as conn:
            if not force:
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'direct_flow_cleanup_day'",
                ).fetchone()
                if row and row["value"] == today:
                    return
            self._clamp_level_progress_conn(conn)
            self._cleanup_orphan_current_state_conn(conn)
            conn.execute(
                """
                DELETE FROM combat_logs
                WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (battle_cutoff,),
            )
            conn.execute(
                """
                DELETE FROM duel_requests
                WHERE status != '等待'
                  AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (battle_cutoff,),
            )
            conn.execute("DELETE FROM trade_prices WHERE business_day < ?", (direct_business_day,))
            conn.execute("DELETE FROM trade_heat WHERE business_day < ?", (direct_business_day,))
            conn.execute("DELETE FROM trade_daily_rewards WHERE business_day < ?", (direct_business_day,))
            conn.execute(
                """
                DELETE FROM treasure_map_bids
                WHERE active = 0
                  AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (world_short_cutoff,),
            )
            conn.execute(
                """
                DELETE FROM treasure_maps
                WHERE status = '已领取'
                  AND settled_at IS NOT NULL
                  AND datetime(replace(settled_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (world_long_cutoff,),
            )
            conn.execute(
                """
                DELETE FROM trade_buy_locks
                WHERE datetime(replace(last_buy_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (direct_cutoff,),
            )
            conn.execute("DELETE FROM daily_fortunes WHERE business_day < ?", (direct_business_day,))
            conn.execute("DELETE FROM daily_newspapers WHERE business_day < ?", (newspaper_business_day,))

            stats_start_at = self._lifetime_stats_started_at_conn(conn)
            self._rollup_lifetime_records_conn(conn, stats_start_at, direct_cutoff, world_long_cutoff)
            self._cleanup_lifetime_source_records_conn(conn, direct_cutoff, world_long_cutoff)
            conn.execute(
                """
                INSERT INTO schema_meta (key, value)
                VALUES ('direct_flow_cleanup_day', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (today,),
            )

    def _clamp_level_progress_conn(self, conn: sqlite3.Connection) -> None:
        """收敛已封顶等级的经验，避免未来开放上限时兑现历史溢出。"""

        player_cap_exp = player_exp_for_level(MAX_LEVEL)
        conn.execute("UPDATE players SET exp = 0 WHERE exp < 0")
        conn.execute("UPDATE players SET exp = ? WHERE exp > ?", (player_cap_exp, player_cap_exp))
        conn.execute(
            "UPDATE players SET level = ? WHERE level > ?",
            (MAX_LEVEL, MAX_LEVEL),
        )

        for row in conn.execute("SELECT holder_id, weapon_id, exp, max_level FROM player_weapons").fetchall():
            cap_exp = weapon_exp_for_level(int(row["max_level"]))
            exp = min(max(0, int(row["exp"] or 0)), cap_exp)
            level = weapon_level_from_exp(exp, int(row["max_level"]))
            conn.execute(
                "UPDATE player_weapons SET exp = ?, level = ? WHERE holder_id = ? AND weapon_id = ?",
                (exp, level, row["holder_id"], int(row["weapon_id"])),
            )

        conn.execute("UPDATE sect_stats SET exp = 0 WHERE exp < 0 OR level >= ?", (SECT_LEVEL_MAX,))

        conn.execute("UPDATE city_world_states SET build_exp = 0 WHERE build_exp < 0 OR city_level >= ?", (CITY_MAX_LEVEL,))

    def _cleanup_orphan_current_state_conn(self, conn: sqlite3.Connection) -> None:
        """清理已不存在玩家的当前态数据，避免事故删号后残留可交互状态。"""

        player_tables = (
            "source_vaults",
            "backpack_items",
            "ring_items",
            "gem_items",
            "vault_items",
            "vault_weapons",
            "fixed_equipment",
            "fixed_equipment_inlays",
            "inscription_feathers",
            "player_journals",
            "player_titles",
            "player_lifetime_stats",
            "daily_fortunes",
            "trade_daily_rewards",
            "trade_buy_locks",
            "sect_members",
            "sect_war_rewards",
        )
        for table in player_tables:
            conn.execute(
                f"""
                DELETE FROM {table}
                WHERE client_id IS NOT NULL
                  AND client_id != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM players p WHERE p.client_id = {table}.client_id
                  )
                """
            )

        conn.execute(
            """
            DELETE FROM fixed_equipment_inlays
            WHERE NOT EXISTS (
                SELECT 1
                FROM fixed_equipment e
                WHERE e.client_id = fixed_equipment_inlays.client_id
                  AND e.slot = fixed_equipment_inlays.slot
            )
            """
        )
        conn.execute(
            """
            DELETE FROM player_weapons
            WHERE holder_id IS NOT NULL
              AND holder_id != ''
              AND holder_id NOT LIKE '__vault__:%'
              AND holder_id NOT LIKE '__second_hand__:%'
              AND NOT EXISTS (
                  SELECT 1 FROM players p WHERE p.client_id = player_weapons.holder_id
              )
            """
        )
        conn.execute(
            """
            DELETE FROM weapon_enchant_names
            WHERE NOT EXISTS (
                SELECT 1
                FROM player_weapons w
                WHERE w.weapon_id = weapon_enchant_names.weapon_id
            )
            """
        )
        conn.execute(
            """
            UPDATE sects
            SET master_client_id = (
                SELECT m.client_id
                FROM sect_members m
                WHERE m.sect_id = sects.sect_id
                ORDER BY m.role = '宗主' DESC, m.joined_at ASC, m.client_id ASC
                LIMIT 1
            )
            WHERE NOT EXISTS (
                SELECT 1 FROM players p WHERE p.client_id = sects.master_client_id
            )
              AND EXISTS (
                SELECT 1 FROM sect_members m WHERE m.sect_id = sects.sect_id
              )
            """
        )
        conn.execute(
            """
            DELETE FROM sects
            WHERE NOT EXISTS (
                SELECT 1 FROM players p WHERE p.client_id = sects.master_client_id
            )
              AND NOT EXISTS (
                SELECT 1 FROM sect_members m WHERE m.sect_id = sects.sect_id
              )
            """
        )
        conn.execute(
            """
            UPDATE sect_members
            SET role = CASE
                WHEN client_id = (
                    SELECT s.master_client_id
                    FROM sects s
                    WHERE s.sect_id = sect_members.sect_id
                )
                THEN '宗主'
                ELSE '成员'
            END
            WHERE sect_id IN (SELECT sect_id FROM sects)
            """
        )

    def _lifetime_stats_started_at_conn(self, conn: sqlite3.Connection) -> str:
        """首次启用长期统计时只记录起点，不回填旧流水。"""

        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (LIFETIME_STATS_STARTED_AT_KEY,),
        ).fetchone()
        if row:
            return str(row["value"])

        started_at = ts()
        conn.execute(
            """
            INSERT INTO schema_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (LIFETIME_STATS_STARTED_AT_KEY, started_at),
        )
        return started_at

    def _rollup_lifetime_records_conn(
        self,
        conn: sqlite3.Connection,
        start_at: str,
        cutoff_at: str,
        world_cutoff_at: str | None = None,
    ) -> None:
        """把即将清理的长期来源流水汇总到玩家长期统计。"""

        self._rollup_game_logs_conn(conn, start_at, cutoff_at)
        self._rollup_trade_records_conn(conn, start_at, cutoff_at)
        self._rollup_second_hand_records_conn(conn, start_at, cutoff_at)
        self._rollup_recycle_records_conn(conn, start_at, cutoff_at)
        self._rollup_world_material_records_conn(conn, start_at, world_cutoff_at or cutoff_at)
        self._rollup_exploration_records_conn(conn, start_at, cutoff_at)
        self._rollup_boss_records_conn(conn, start_at, cutoff_at)
        self._rollup_duel_records_conn(conn, start_at, cutoff_at)
        self._rollup_robbery_records_conn(conn, start_at, cutoff_at)

    def _rollup_game_logs_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总签到、铭刻、使用物品等通用行为流水。"""

        rows = conn.execute(
            """
            SELECT client_id, action, COUNT(*) AS total
            FROM game_logs
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY client_id, action
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            stat_key = GAME_LOG_LIFETIME_STATS.get(str(row["action"]))
            if stat_key:
                self.add_lifetime_stat_conn(conn, row["client_id"], stat_key, int(row["total"] or 0))

    def _rollup_trade_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总跑商次数和普通跑商净利润。"""

        rows = conn.execute(
            """
            SELECT client_id,
                   COUNT(*) AS trade_count,
                   SUM(CASE WHEN action = 'sell' THEN 1 ELSE 0 END) AS trade_sell_count,
                   SUM(CASE WHEN action = 'buy' THEN 1 ELSE 0 END) AS trade_buy_count,
                   SUM(
                       CASE
                           WHEN action = 'sell' THEN total_price - fee
                           WHEN action = 'buy' THEN -(total_price + fee)
                           ELSE 0
                       END
                   ) AS trade_net
            FROM trade_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY client_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            client_id = row["client_id"]
            for stat_key in (
                "trade_count",
                "trade_sell_count",
                "trade_buy_count",
                "trade_net",
            ):
                self.add_lifetime_stat_conn(conn, client_id, stat_key, int(row[stat_key] or 0))

    def _rollup_second_hand_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总二手市场买卖次数。"""

        rows = conn.execute(
            """
            SELECT seller_id AS client_id, COUNT(*) AS total
            FROM second_hand_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY seller_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "second_hand_sell_count", int(row["total"] or 0))

        rows = conn.execute(
            """
            SELECT buyer_id AS client_id, COUNT(*) AS total
            FROM second_hand_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY buyer_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "second_hand_buy_count", int(row["total"] or 0))

    def _rollup_recycle_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总武器、宝石和技能书出售次数与收入。"""

        configs = (
            ("weapon_recycle_records", "weapon_recycle_count", "weapon_recycle_income"),
            ("gem_recycle_records", "gem_recycle_count", "gem_recycle_income"),
            ("book_recycle_records", "book_recycle_count", "book_recycle_income"),
        )
        for table, count_key, income_key in configs:
            rows = conn.execute(
                f"""
                SELECT client_id, COUNT(*) AS total, COALESCE(SUM(total_price), 0) AS income
                FROM {table}
                WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
                  AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                GROUP BY client_id
                """,
                (start_at, cutoff_at),
            ).fetchall()
            for row in rows:
                self.add_lifetime_stat_conn(conn, row["client_id"], count_key, int(row["total"] or 0))
                self.add_lifetime_stat_conn(conn, row["client_id"], income_key, int(row["income"] or 0))

    def _rollup_world_material_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总世界物资回收流水，清理后仍保留玩家长期倾向和收入账。"""

        rows = conn.execute(
            """
            SELECT client_id,
                   category,
                   COUNT(*) AS total,
                   COALESCE(SUM(quantity), 0) AS quantity,
                   COALESCE(SUM(stones), 0) AS income
            FROM world_material_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY client_id, category
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            client_id = row["client_id"]
            category = str(row["category"] or "未分")
            total = int(row["total"] or 0)
            quantity = int(row["quantity"] or 0)
            income = int(row["income"] or 0)
            self.add_lifetime_stat_conn(conn, client_id, "world_material_count", total)
            self.add_lifetime_stat_conn(conn, client_id, "world_material_quantity", quantity)
            self.add_lifetime_stat_conn(conn, client_id, "world_material_income", income)
            self.add_lifetime_stat_conn(conn, client_id, f"world_material_{category}_count", total)
            self.add_lifetime_stat_conn(conn, client_id, f"world_material_{category}_quantity", quantity)
            self.add_lifetime_stat_conn(conn, client_id, f"world_material_{category}_income", income)

    def _rollup_exploration_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总已领取探险次数；未领取探险保留，不进入清理。"""

        rows = conn.execute(
            """
            SELECT client_id, COUNT(*) AS total
            FROM exploration_records
            WHERE claimed = 1
              AND datetime(replace(COALESCE(finished_at, ready_at, started_at), 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(COALESCE(finished_at, ready_at, started_at), 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY client_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "explore_count", int(row["total"] or 0))

    def _rollup_boss_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总虫洞和岁时首领参与次数与伤害。"""

        configs = (
            ("wormhole_participants", "wormhole_count", "wormhole_damage"),
            ("seasonal_boss_participants", "boss_count", "boss_damage"),
        )
        for table, count_key, damage_key in configs:
            rows = conn.execute(
                f"""
                SELECT client_id, COUNT(*) AS total, COALESCE(SUM(damage), 0) AS damage
                FROM {table}
                WHERE reward_claimed = 1
                  AND datetime(replace(updated_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
                  AND datetime(replace(updated_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                GROUP BY client_id
                """,
                (start_at, cutoff_at),
            ).fetchall()
            for row in rows:
                self.add_lifetime_stat_conn(conn, row["client_id"], count_key, int(row["total"] or 0))
                self.add_lifetime_stat_conn(conn, row["client_id"], damage_key, int(row["damage"] or 0))

    def _rollup_duel_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总切磋和决斗参与次数、胜场。"""

        rows = conn.execute(
            """
            SELECT client_id, COUNT(*) AS total
            FROM (
                SELECT from_client_id AS client_id, created_at FROM duel_records
                UNION ALL
                SELECT to_client_id AS client_id, created_at FROM duel_records
            )
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY client_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "duel_count", int(row["total"] or 0))

        rows = conn.execute(
            """
            SELECT winner_id AS client_id, COUNT(*) AS total
            FROM duel_records
            WHERE winner_id IS NOT NULL
              AND winner_id != ''
              AND datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY winner_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "duel_win_count", int(row["total"] or 0))

    def _rollup_robbery_records_conn(self, conn: sqlite3.Connection, start_at: str, cutoff_at: str) -> None:
        """汇总抢劫和被抢记录，供后续人物志或称号使用。"""

        rows = conn.execute(
            """
            SELECT robber_id AS client_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_total
            FROM robbery_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY robber_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "robbery_count", int(row["total"] or 0))
            self.add_lifetime_stat_conn(conn, row["client_id"], "robbery_success_count", int(row["success_total"] or 0))

        rows = conn.execute(
            """
            SELECT target_id AS client_id, COUNT(*) AS total
            FROM robbery_records
            WHERE datetime(replace(created_at, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
              AND datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            GROUP BY target_id
            """,
            (start_at, cutoff_at),
        ).fetchall()
        for row in rows:
            self.add_lifetime_stat_conn(conn, row["client_id"], "robbed_count", int(row["total"] or 0))

    def _cleanup_lifetime_source_records_conn(
        self,
        conn: sqlite3.Connection,
        cutoff_at: str,
        world_cutoff_at: str | None = None,
    ) -> None:
        """清理已经能由长期统计承接的明细流水。"""

        world_cutoff = world_cutoff_at or cutoff_at
        conn.execute(
            """
            DELETE FROM game_logs
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        for table in ("trade_records", "second_hand_records", "weapon_recycle_records", "gem_recycle_records", "book_recycle_records"):
            conn.execute(
                f"""
                DELETE FROM {table}
                WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                """,
                (cutoff_at,),
            )
        conn.execute(
            """
            DELETE FROM world_material_records
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (world_cutoff,),
        )
        conn.execute(
            """
            DELETE FROM exploration_records
            WHERE claimed = 1
              AND datetime(replace(COALESCE(finished_at, ready_at, started_at), 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM duel_records
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM robbery_records
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM wormhole_challenge_records
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM wormhole_participants
            WHERE reward_claimed = 1
              AND datetime(replace(updated_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM wormhole_notices
            WHERE wormhole_id IN (
                SELECT w.wormhole_id FROM wormholes w
                WHERE datetime(replace(w.closes_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
                  AND NOT EXISTS (
                      SELECT 1 FROM wormhole_participants p
                      WHERE p.wormhole_id = w.wormhole_id
                  )
            )
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM wormholes
            WHERE datetime(replace(closes_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
              AND NOT EXISTS (
                  SELECT 1 FROM wormhole_participants p
                  WHERE p.wormhole_id = wormholes.wormhole_id
              )
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM boss_challenge_records
            WHERE datetime(replace(created_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM seasonal_boss_participants
            WHERE reward_claimed = 1
              AND datetime(replace(updated_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
            """,
            (cutoff_at,),
        )
        conn.execute(
            """
            DELETE FROM seasonal_boss_events
            WHERE datetime(replace(closes_at, 'T', ' ')) < datetime(replace(?, 'T', ' '))
              AND NOT EXISTS (
                  SELECT 1 FROM seasonal_boss_participants p
                  WHERE p.event_id = seasonal_boss_events.event_id
              )
            """,
            (cutoff_at,),
        )

    @staticmethod
    def add_lifetime_stat_conn(
        conn: sqlite3.Connection,
        client_id: str,
        stat_key: str,
        delta: int,
        *,
        updated_at: str | None = None,
    ) -> None:
        """累加玩家长期统计；只给清理汇总和后续新功能复用。"""

        amount = int(delta)
        if not client_id or not stat_key or amount == 0:
            return
        current = updated_at or ts()
        conn.execute(
            """
            INSERT INTO player_lifetime_stats (client_id, stat_key, stat_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, stat_key)
            DO UPDATE SET
                stat_value = stat_value + excluded.stat_value,
                updated_at = excluded.updated_at
            """,
            (client_id, stat_key, amount, current),
        )

    @staticmethod
    def lifetime_stat_conn(conn: sqlite3.Connection, client_id: str, stat_key: str) -> int:
        """读取事务内玩家长期统计值。"""

        row = conn.execute(
            "SELECT stat_value FROM player_lifetime_stats WHERE client_id = ? AND stat_key = ?",
            (client_id, stat_key),
        ).fetchone()
        return int(row["stat_value"] or 0) if row else 0

    def lifetime_stat(self, client_id: str, stat_key: str) -> int:
        """读取玩家长期统计值。"""

        row = self.db.fetch_one(
            "SELECT stat_value FROM player_lifetime_stats WHERE client_id = ? AND stat_key = ?",
            (client_id, stat_key),
        )
        return int(row["stat_value"] or 0) if row else 0

    def stat_count(self, client_id: str, stat_key: str, sql: str, params: tuple[Any, ...]) -> int:
        """长期统计加当前明细计数。"""

        row = self.db.fetch_one(sql, params)
        live = int(row["count"] or 0) if row else 0
        return self.lifetime_stat(client_id, stat_key) + live

    def stat_total(self, client_id: str, stat_key: str, sql: str, params: tuple[Any, ...]) -> int:
        """长期统计加当前明细求和。"""

        row = self.db.fetch_one(sql, params)
        live = int(row["total"] or 0) if row else 0
        return self.lifetime_stat(client_id, stat_key) + live

    def stat_count_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        stat_key: str,
        table: str,
        where: str,
        params: tuple[Any, ...],
    ) -> int:
        """事务内长期统计加当前明细计数。"""

        return self.lifetime_stat_conn(conn, client_id, stat_key) + self._count_conn(conn, table, where, params)

    def stat_total_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        stat_key: str,
        sql: str,
        params: tuple[Any, ...],
    ) -> int:
        """事务内长期统计加当前明细求和。"""

        row = conn.execute(sql, params).fetchone()
        live = int(row["total"] or 0) if row else 0
        return self.lifetime_stat_conn(conn, client_id, stat_key) + live

    def create_player(self, client_id: str, display_name: str) -> str:
        """创建玩家。"""


        if self.player(client_id):
            return T.hint("你已经创建过用户了。", "发送：修仙信息 查看角色，或发送：改名 新名称<指南><探险><修仙帮助>")
        ok, result = validate_name(display_name)
        if not ok:
            return T.hint(result, "请换一个 2 到 12 个字符、且不含空白的名称。")
        if self.player_name_taken(result):
            return T.hint("这个名称已经被使用了。", "请换一个不重复的名称后再创建用户。")

        hp = max_hp(1, 0)
        mp = max_mp(1)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO players (
                    client_id, display_name, level, exp, hp, max_hp, mp, max_mp,
                    physique_id, physique_value, base_attack, defense, source_stones, status,
                    location_name, x, y, backpack_limit, weight_limit, created_at
                )
                VALUES (?, ?, 1, 0, ?, ?, ?, ?, 'fanti', 0, ?, ?, 0, '空闲',
                        ?, 0, 0, ?, ?, ?)
                """,
                (
                    client_id,
                    result,
                    hp,
                    hp,
                    mp,
                    mp,
                    base_attack(1),
                    defense(1, 0),
                    DEFAULT_LOCATION,
                    DEFAULT_BACKPACK_LIMIT,
                    DEFAULT_WEIGHT_LIMIT,
                    ts(),
                ),
            )
            if cursor.rowcount <= 0:
                if conn.execute("SELECT 1 FROM players WHERE client_id = ?", (client_id,)).fetchone():
                    return T.hint("你已经创建过用户了。", "发送：修仙信息 查看角色，或发送：改名 新名称")
                if conn.execute("SELECT 1 FROM players WHERE display_name = ?", (result,)).fetchone():
                    return T.hint("这个名称刚刚被别人使用了。", "请换一个不重复的名称后再创建用户。")
                return T.hint("创建用户失败。", "请稍后重试，或换一个不重复的名称。")
            conn.execute(
                """
                INSERT INTO source_vaults (client_id, star_level, balance, last_settle_at)
                VALUES (?, 1, 0, ?)
                """,
                (client_id, ts()),
            )
            conn.executemany(
                "INSERT INTO fixed_equipment (client_id, slot, level) VALUES (?, ?, 0)",
                [(client_id, slot) for slot in EQUIPMENT_SLOTS],
            )
            self.ensure_player_weapon_conn(conn, client_id)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '创建用户', ?, ?)",
                (client_id, result, ts()),
            )
        return f"创建成功，道友 {result}。初始武器：青岚短剑。"

    def rename_player(self, client_id: str, display_name: str) -> str:
        """修改展示名称。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        ok, result = validate_name(display_name)
        if not ok:
            return T.hint(result, "请换一个 2 到 12 个字符、且不含空白的名称。")
        if result == player["display_name"]:
            return T.hint("名称没有变化。", "发送：改名 新名称，或发送：修仙信息 查看当前角色。<指南><探险><修仙帮助>")
        if self.player_name_taken(result, client_id):
            return T.hint("这个名称已经被使用了。", "请换一个不重复的新名称。<指南><探险><修仙帮助>")

        last = dt(player.get("last_rename_at"))
        if last and now() - last < timedelta(hours=RENAME_COOLDOWN_HOURS):
            left = timedelta(hours=RENAME_COOLDOWN_HOURS) - (now() - last)
            hours = max(1, int(left.total_seconds() // 3600) + 1)
            return T.hint(f"改名太频繁，请约 {hours} 小时后再试。", "冷却结束后发送：改名 新名称<指南><探险><修仙帮助>")

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE players
                SET display_name = ?, last_rename_at = ?
                WHERE client_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM players WHERE display_name = ? AND client_id != ?
                  )
                """,
                (result, ts(), client_id, result, client_id),
            )
            if cursor.rowcount <= 0:
                return T.hint("这个名称刚刚被别人使用了。", "请换一个不重复的新名称。")
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '改名', ?, ?)",
                (client_id, result, ts()),
            )
        return f"改名成功，现在叫 {result}。<指南><探险><修仙帮助>"

    def log(self, client_id: str, action: str, detail: str = "") -> None:
        """写行为日志。"""

        self.db.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (client_id, action, detail, ts()),
        )

    def record_journal(
        self,
        client_id: str,
        milestone_key: str,
        text: str,
        *,
        created_at: str | None = None,
        keep_first_time: bool = False,
    ) -> None:
        """写入或刷新一条玩家日记里程碑。"""

        with self.db.transaction() as conn:
            self.record_journal_conn(
                conn,
                client_id,
                milestone_key,
                text,
                created_at=created_at,
                keep_first_time=keep_first_time,
            )

    def record_journal_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        milestone_key: str,
        text: str,
        *,
        created_at: str | None = None,
        keep_first_time: bool = False,
    ) -> None:
        """在事务里写入或刷新一条玩家日记里程碑。

        player_journals 以 client_id + milestone_key 去重。
        - keep_first_time=True：适合创建角色这类“第一次发生”的记录，只更新文字，不改时间。
        - keep_first_time=False：适合等级、次数、资产等会变化的统计记录，每次刷新都会更新展示。
        """

        current = created_at or ts()
        if keep_first_time:
            conn.execute(
                """
                INSERT INTO player_journals
                (client_id, milestone_key, text, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_id, milestone_key)
                DO UPDATE SET text = excluded.text
                """,
                (client_id, milestone_key, text, current),
            )
            return

        conn.execute(
            """
            INSERT INTO player_journals
            (client_id, milestone_key, text, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, milestone_key)
            DO UPDATE SET text = excluded.text, created_at = excluded.created_at
            """,
            (client_id, milestone_key, text, current),
        )

    def equipment_bonuses(self, client_id: str) -> dict[str, float]:
        """汇总装备和宝石加成。

        装备只给生存属性，宝石和体质按自身效果叠加。
        这里放在公共服务里，所有组件都能读取，不需要二级包互相引用。
        """

        self.db.ensure_fixed_equipment(client_id)
        with self.db.transaction() as conn:
            return self.equipment_bonuses_conn(conn, client_id)

    def equipment_bonuses_conn(self, conn: sqlite3.Connection, client_id: str) -> dict[str, float]:
        """在事务里汇总装备、宝石和体质加成。

        事务内不能调用 ensure_fixed_equipment()，否则会提前 commit 外层事务。
        创建玩家时已经写入装备位，普通查询入口也会兜底补齐。
        """

        bonuses: dict[str, float] = {
            "max_hp_bonus": 0,
            "max_mp_bonus": 0,
            "defense_bonus": 0,
            "dodge_bonus": 0,
            "recover_bonus": 0,
            "explore_bonus": 0,
            "trade_bonus": 0,
            "crit_resist_bonus": 0,
        }

        rows = conn.execute(
            "SELECT slot, level FROM fixed_equipment WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        for row in rows:
            level = int(row["level"])
            factor = FIXED_EQUIPMENT_SLOT_FACTORS.get(row["slot"], 1.0)
            bonuses["max_hp_bonus"] += int(level * 8 * factor)
            bonuses["max_mp_bonus"] += int(level * 3 * factor)
            bonuses["defense_bonus"] += int(level * 2 * factor)

        inlays = conn.execute(
            """
            SELECT i.level, e.effect
            FROM fixed_equipment_inlays i
            JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
            WHERE i.client_id = ?
            """,
            (client_id,),
        ).fetchall()
        for row in inlays:
            level = max(1, int(row["level"]))
            effect = load_json(row["effect"], {})
            for key, value in effect.items():
                if not isinstance(value, int | float):
                    continue
                bonus_key = "max_mp_bonus" if key == "mp_bonus" else key
                bonuses[bonus_key] = bonuses.get(bonus_key, 0) + float(value) * level
        physique = conn.execute(
            """
            SELECT d.effect
            FROM players p
            JOIN physique_defs d ON d.physique_id = p.physique_id
            WHERE p.client_id = ?
            """,
            (client_id,),
        ).fetchone()
        if physique:
            for key, value in load_json(physique["effect"], {}).items():
                if isinstance(value, int | float):
                    bonus_key = "max_mp_bonus" if key == "mp_bonus" else key
                    bonuses[bonus_key] = bonuses.get(bonus_key, 0) + float(value)
        fortune = conn.execute(
            """
            SELECT effect
            FROM daily_fortunes
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, business_day()),
        ).fetchone()
        if fortune:
            for key, value in load_json(fortune["effect"], {}).items():
                if isinstance(value, int | float):
                    bonuses[key] = bonuses.get(key, 0) + float(value)
        for key, value in world_state_for_day()["effect"].items():
            bonuses[key] = bonuses.get(key, 0) + value
        for key, cap in PERCENT_BONUS_CAPS.items():
            bonuses[key] = soft_cap_percent_bonus(float(bonuses.get(key, 0)), cap)
        return bonuses

    def ensure_daily_fortune(self, client_id: str) -> dict[str, Any]:
        """生成或读取今日气运。"""

        with self.db.transaction() as conn:
            return self.ensure_daily_fortune_conn(conn, client_id)

    def ensure_daily_fortune_conn(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        """在事务里生成或读取今日气运。"""

        day = business_day()
        row = conn.execute(
            """
            SELECT * FROM daily_fortunes
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, day),
        ).fetchone()
        if row:
            return dict(row)

        fortune, flavor, effect = random.choice(FORTUNE_POOL)
        conn.execute(
            """
            INSERT INTO daily_fortunes
            (client_id, business_day, fortune, effect, flavor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (client_id, day, fortune, dump_json(effect), flavor, ts()),
        )
        row = conn.execute(
            """
            SELECT * FROM daily_fortunes
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, day),
        ).fetchone()
        assert row is not None
        return dict(row)

    def active_title(self, client_id: str) -> str:
        """读取当前自动称号。"""

        from .称号.service import service as title_service

        return title_service.active_title(client_id)

    def manual_title(self, client_id: str) -> str:
        """读取手动佩戴称号。"""

        from .称号.service import service as title_service

        return title_service.manual_title(client_id)

    def current_display_title(self, client_id: str) -> str:
        """读取当前显示称号：手动优先，否则自动。"""

        from .称号.service import service as title_service

        return title_service.current_display_title(client_id)

    def set_manual_title(self, client_id: str, title_name: str) -> None:
        """保存手动佩戴称号。"""

        from .称号.service import service as title_service

        title_service.set_manual_title(client_id, title_name)

    def clear_manual_title(self, client_id: str) -> None:
        """清空手动佩戴称号。"""

        from .称号.service import service as title_service

        title_service.clear_manual_title(client_id)

    def delete_title_prefs(self, client_id: str) -> None:
        """删除称号偏好记录。"""

        from .称号.service import service as title_service

        title_service.delete_title_prefs(client_id)

    def refresh_titles(self, client_id: str, player: dict[str, Any] | None = None) -> str:
        """按当前数据刷新称号，并自动佩戴当前最合适的一个。"""

        from .称号.service import service as title_service

        return title_service.refresh_titles(client_id, player)

    def refresh_titles_conn(self, conn: sqlite3.Connection, client_id: str, player: dict[str, Any]) -> str:
        """在事务里刷新称号，并返回自动佩戴的称号。"""

        from .称号.service import service as title_service

        return title_service.refresh_titles_conn(conn, client_id, player)

    def _title_stats_conn(self, conn: sqlite3.Connection, client_id: str, player: dict[str, Any]) -> dict[str, Any]:
        """收集称号判断需要的玩家统计。"""

        from .称号.service import service as title_service

        return title_service._title_stats_conn(conn, client_id, player)

    @staticmethod
    def _all_equipment_slots_opened(conn: sqlite3.Connection, client_id: str) -> bool:
        """判断装备是否全部开孔。"""
        rows = conn.execute("SELECT holes_opened FROM equipment WHERE client_id = ?", (client_id,)).fetchall()
        if not rows:
            return False
        return all(int(r["holes_opened"]) >= 1 for r in rows)

    @staticmethod
    def _sect_contribution_conn(conn: sqlite3.Connection, client_id: str) -> int:
        """读取宗门个人贡献。"""
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM sect_contribution_records WHERE client_id = ?", (client_id,)).fetchone()
        return int(row["total"] or 0) if row else 0

    @staticmethod
    def _title_rules(stats: dict[str, Any]) -> tuple[tuple[int, str, str, bool], ...]:
        """把玩家统计转成称号规则。"""

        from .称号.service import service as title_service

        return title_service._title_rules(stats)

    @staticmethod
    def _save_valid_titles_conn(
        conn: sqlite3.Connection,
        client_id: str,
        rules: tuple[tuple[int, str, str, bool], ...],
        current: str,
    ) -> list[tuple[int, str]]:
        """写入当前有效称号，并返回可佩戴称号列表。"""

        from .称号.service import service as title_service

        return title_service._save_valid_titles_conn(conn, client_id, rules, current)

    @staticmethod
    def _count_conn(conn: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...]) -> int:
        """在事务里执行简单计数。"""

        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params).fetchone()
        return int(row["count"]) if row else 0

    @staticmethod
    def _recent_count_conn(
        conn: sqlite3.Connection,
        table: str,
        where: str,
        params: tuple[Any, ...],
        *,
        time_column: str = "created_at",
        days: int = DIRECT_FLOW_RETENTION_DAYS,
    ) -> int:
        """统计当前明细保留窗口内的近期行为次数。"""

        cutoff_at = ts(now() - timedelta(days=days))
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {table}
            WHERE {where}
              AND datetime(replace({time_column}, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
            """,
            (*params, cutoff_at),
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    @staticmethod
    def _recent_total_conn(
        conn: sqlite3.Connection,
        table: str,
        field: str,
        where: str,
        params: tuple[Any, ...],
        *,
        time_column: str = "created_at",
        days: int = DIRECT_FLOW_RETENTION_DAYS,
    ) -> int:
        """统计当前明细保留窗口内的近期行为数量。"""

        cutoff_at = ts(now() - timedelta(days=days))
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM({field}), 0) AS total
            FROM {table}
            WHERE {where}
              AND datetime(replace({time_column}, 'T', ' ')) >= datetime(replace(?, 'T', ' '))
            """,
            (*params, cutoff_at),
        ).fetchone()
        return int(row["total"] or 0) if row else 0

    @staticmethod
    def _exists_conn(conn: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...]) -> bool:
        """在事务里判断数据是否存在。"""

        return bool(conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone())

    def record_weapon_created_conn(self, conn: sqlite3.Connection, client_id: str, weapon_id: int) -> None:
        """为新武器初始化传奇记录。"""

        if int(weapon_id) <= 0:
            return
        current = ts()
        conn.execute(
            """
            INSERT OR IGNORE INTO weapon_legends
            (weapon_id, original_owner_id, current_owner_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(weapon_id), client_id, client_id, current, current),
        )

    def record_weapon_combat_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        weapon_id: int,
        *,
        monster_kill: bool = False,
        boss_challenge: bool = False,
        duel_win: bool = False,
        damage: int = 0,
        weapon_exp: int = 0,
    ) -> None:
        """累积武器传奇数据。"""

        if int(weapon_id) <= 0:
            return
        self.add_weapon_exp_conn(conn, client_id, weapon_id, weapon_exp)
        current = ts()
        conn.execute(
            """
            INSERT INTO weapon_legends
            (weapon_id, original_owner_id, current_owner_id, monster_kills, boss_challenges,
             duel_wins, highest_damage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(weapon_id)
            DO UPDATE SET
                current_owner_id = excluded.current_owner_id,
                monster_kills = monster_kills + excluded.monster_kills,
                boss_challenges = boss_challenges + excluded.boss_challenges,
                duel_wins = duel_wins + excluded.duel_wins,
                highest_damage = max(highest_damage, excluded.highest_damage),
                updated_at = excluded.updated_at
            """,
            (
                int(weapon_id),
                client_id,
                client_id,
                1 if monster_kill else 0,
                1 if boss_challenge else 0,
                1 if duel_win else 0,
                max(0, int(damage)),
                current,
                current,
            ),
        )

    def add_weapon_exp_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        weapon_id: int,
        amount: int,
    ) -> int:
        """给玩家持有的武器累计经验，返回实际增加量。"""

        weapon_id_int = to_int(weapon_id, 0)
        amount_int = max(0, to_int(amount, 0))
        if weapon_id_int <= 0 or amount_int <= 0:
            return 0
        row = conn.execute(
            "SELECT exp, max_level FROM player_weapons WHERE holder_id = ? AND weapon_id = ?",
            (client_id, weapon_id_int),
        ).fetchone()
        if not row:
            return 0
        cap_exp = weapon_exp_for_level(int(row["max_level"]))
        current_exp = min(max(0, int(row["exp"] or 0)), cap_exp)
        next_exp = min(cap_exp, current_exp + amount_int)
        actual_gain = max(0, next_exp - current_exp)
        cursor = conn.execute(
            """
            UPDATE player_weapons
            SET exp = ?
            WHERE holder_id = ? AND weapon_id = ?
            """,
            (next_exp, client_id, weapon_id_int),
        )
        if cursor.rowcount <= 0:
            return 0
        self.sync_weapon_level_conn(conn, client_id, weapon_id_int)
        return actual_gain

    def reset_rest_window_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        hp: int,
        mp: int,
    ) -> None:
        """刷新休息恢复窗口，供固定冷却战斗结算后调用。"""

        conn.execute(
            """
            UPDATE players
            SET rest_window_started_at = ?,
                rest_window_hp = ?,
                rest_window_mp = ?,
                rest_window_elapsed_seconds = 0
            WHERE client_id = ?
            """,
            (ts(), max(0, int(hp)), max(0, int(mp)), client_id),
        )

    def sync_weapon_level_conn(self, conn: sqlite3.Connection, client_id: str, weapon_id: int) -> int:
        """按武器经验刷新等级，等级不能超过自身上限。"""

        weapon_id_int = to_int(weapon_id, 0)
        if weapon_id_int <= 0:
            return 0
        row = conn.execute(
            "SELECT exp, max_level FROM player_weapons WHERE holder_id = ? AND weapon_id = ?",
            (client_id, weapon_id_int),
        ).fetchone()
        if not row:
            return 0
        cap_exp = weapon_exp_for_level(int(row["max_level"]))
        exp = min(max(0, int(row["exp"] or 0)), cap_exp)
        level = weapon_level_from_exp(exp, int(row["max_level"]))
        conn.execute(
            "UPDATE player_weapons SET level = ?, exp = ? WHERE holder_id = ? AND weapon_id = ?",
            (level, exp, client_id, weapon_id_int),
        )
        return level

    @staticmethod
    def weapon_exp_for_level(level: int) -> int:
        """暴露武器等级累计经验公式给组件调用。"""

        return weapon_exp_for_level(level)

    def weapon_effects_from_ids(self, enchant_ids: object) -> dict[str, float]:
        """按附魔 id 列表汇总武器附魔效果。

        技能书真正参与战斗的是 weapon_enchants 表里的 effect 和 mp_delta。
        这个函数是唯一入口，战斗结算和武器详情都走这里，避免不同玩法漏算技能书。
        """

        effects: dict[str, float] = {}
        if not isinstance(enchant_ids, list):
            return effects
        seen: set[str] = set()
        for enchant_id in enchant_ids:
            enchant_id = str(enchant_id)
            if enchant_id in seen:
                continue
            seen.add(enchant_id)
            row = self.db.fetch_one("SELECT effect, mp_delta FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
            if not row:
                continue
            for key, value in load_json(row["effect"], {}).items():
                if isinstance(value, int | float):
                    effects[key] = effects.get(key, 0) + float(value)
            effects["mp_delta"] = effects.get("mp_delta", 0) + int(row["mp_delta"])
        return effects

    def _weapon_effects(self, weapon: dict[str, Any] | None) -> dict[str, float]:
        """读取一把武器已经附魔的全部战斗效果。"""

        if not weapon:
            return {}
        return self.weapon_effects_from_ids(load_json(weapon.get("enchant_effects"), []))

    @staticmethod
    def _merge_effects(*groups: dict[str, float]) -> dict[str, float]:
        """合并装备、宝石、体质和武器附魔效果。"""

        merged: dict[str, float] = {}
        for group in groups:
            for key, value in group.items():
                if isinstance(value, int | float):
                    merged[key] = merged.get(key, 0) + float(value)
        return merged

    @staticmethod
    def _attack_raw(base_attack_value: int, level: int, effects: dict[str, float]) -> int:
        """计算一次普通出手的原始伤害。"""

        stable_bonus = float(effects.get("hit_bonus", 0)) * 0.5
        raw = int(base_attack_value * (1 + stable_bonus))
        return raw + random.randint(0, max(2, int(level) * 2))

    @staticmethod
    def _skill_power(skill: dict[str, Any], effects: dict[str, float]) -> float:
        """计算武器技能实际威力。"""

        power = float(skill["power"])
        power += float(effects.get("skill_power_bonus", 0))
        power += float(effects.get("heavy_bonus", 0))
        power += float(effects.get("single_hit_bonus", 0))
        return max(1.0, power)

    @staticmethod
    def _skill_cost(skill: dict[str, Any] | None, effects: dict[str, float]) -> int:
        """计算武器技能实际精神消耗。"""

        if not skill:
            return 0
        return max(0, int(skill["cost_mp"]) + int(effects.get("mp_delta", 0)))

    @staticmethod
    def _skill_interval(skill: dict[str, Any] | None, weapon: dict[str, Any] | None, effects: dict[str, float]) -> int:
        """计算武器技能蓄势基准。

        数值越小，技能蓄力越快。它不再表示“固定每 N 回合触发一次”。
        """

        if not skill:
            return 0
        weapon_type = str(weapon.get("weapon_type") if weapon else "")
        type_factor = WEAPON_TYPE_INTERVAL_FACTORS.get(weapon_type, 1.0)
        rate = max(0.6, 1.0 + float(effects.get("interval_rate", 0)))
        interval = round(int(skill["interval"]) * type_factor * rate)
        interval += int(effects.get("interval_delta", 0))
        return max(2, min(12, interval))

    @staticmethod
    def _weapon_attack_load(weapon: dict[str, Any] | None) -> float:
        """计算武器攻击带来的速度负重。

        高攻击武器可以打得疼，但技能条不能也无脑更快。
        这里按武器等级做归一，避免正常升级被过度惩罚。
        """

        if not weapon:
            return 0.0
        attack = computed_weapon_attack(weapon)
        level = max(0, int(weapon.get("level") or 0))
        base_line = 28 + level * 2.4
        return max(0.0, (attack - base_line) / max(1.0, base_line))

    @staticmethod
    def _actor_speed(level: int, weapon: dict[str, Any] | None, effects: dict[str, float]) -> float:
        """计算人物行动速度。

        等级提供少量成长，轻武器更快，闪避/命中类效果也会让出手更顺。
        高攻击武器会带来一点负重，避免高攻武器同时成为最快技能流。
        """

        weapon_type = str(weapon.get("weapon_type") if weapon else "")
        type_factor = WEAPON_TYPE_INTERVAL_FACTORS.get(weapon_type, 1.0)
        type_speed = (1.0 / max(0.72, type_factor) - 1.0) * 42
        effect_speed = float(effects.get("dodge_bonus", 0)) * 90 + float(effects.get("hit_bonus", 0)) * 45
        attack_load = CoreService._weapon_attack_load(weapon)
        load_penalty = min(34.0, attack_load * 44.0)
        speed = 96 + min(42, max(0, int(level)) * 0.42) + type_speed + effect_speed - load_penalty
        return max(68.0, min(168.0, speed))

    @staticmethod
    def _skill_charge_gain(
        skill: dict[str, Any] | None,
        weapon: dict[str, Any] | None,
        effects: dict[str, float],
        actor_speed: float,
    ) -> float:
        """计算每次出手获得多少技能蓄力。

        技能速度来自旧的 interval 字段：数值越小，蓄力越快。
        人物速度越高，同样一次出手积累的蓄力也越多。
        """

        if not skill:
            return 0.0
        interval = CoreService._skill_interval(skill, weapon, effects)
        speed_rate = max(0.7, min(1.7, actor_speed / 100))
        attack_load = CoreService._weapon_attack_load(weapon)
        load_rate = max(0.66, 1.0 - min(0.34, attack_load * 0.42))
        gain = (0.92 / interval + 0.16) * speed_rate * load_rate
        return max(0.22, min(0.78, gain))

    @staticmethod
    def _speed_grade(speed: float) -> str:
        """把行动速度翻译成玩家一眼能懂的档位。"""

        if speed >= 126:
            return "极快"
        if speed >= 112:
            return "快"
        if speed >= 96:
            return "均衡"
        if speed >= 84:
            return "慢"
        return "极慢"

    @staticmethod
    def _skill_tempo_text(gain: float) -> str:
        """把技能蓄势速度翻译成触发节奏。"""

        if gain <= 0:
            return "无"
        attacks = max(2, int(round(1 / max(0.01, gain))))
        if attacks <= 2:
            grade = "高频"
        elif attacks <= 3:
            grade = "偏快"
        elif attacks <= 4:
            grade = "适中"
        elif attacks <= 5:
            grade = "偏慢"
        else:
            grade = "很慢"
        return f"{grade}，约每 {attacks} 次出手触发"

    @staticmethod
    def _weapon_style_text(weapon: dict[str, Any] | None) -> str:
        """按武器类型说明打法定位。"""

        if not weapon:
            return "未装备武器，只有基础出手"
        weapon_type = str(weapon.get("weapon_type") or "")
        return WEAPON_TYPE_STYLE_TEXT.get(weapon_type, "通用兵器，打法较均衡")

    @staticmethod
    def combat_profile(
        level: int,
        weapon: dict[str, Any] | None,
        skill: dict[str, Any] | None,
        effects: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """生成面板和武器详情共用的速度/技能节奏描述。"""

        active_effects = effects or {}
        speed = CoreService._actor_speed(level, weapon, active_effects)
        gain = CoreService._skill_charge_gain(skill, weapon, active_effects, speed)
        interval = CoreService._skill_interval(skill, weapon, active_effects)
        return {
            "speed": round(speed, 1),
            "speed_grade": CoreService._speed_grade(speed),
            "skill_charge_gain": round(gain, 3),
            "skill_interval": interval,
            "skill_tempo": CoreService._skill_tempo_text(gain),
            "weapon_style": CoreService._weapon_style_text(weapon),
        }

    @staticmethod
    def _skill_initial_charge(
        skill: dict[str, Any] | None,
        weapon: dict[str, Any] | None,
        effects: dict[str, float],
        actor_speed: float,
    ) -> float:
        """开战时给一点初始蓄力，避免短战完全看不到武器技能。"""

        gain = CoreService._skill_charge_gain(skill, weapon, effects, actor_speed)
        return min(0.9, 0.45 + gain * 0.7)

    @staticmethod
    def _enemy_speed(level: int, kind: str, boss: bool = False) -> float:
        """计算怪物或 Boss 的行动速度。

        怪物没有武器，但也按类型区分打法：妖鬼更快，傀儡和重甲兵更慢。
        Boss 略慢一点，但技能更重，给玩家留下反应空间。
        """

        kind = str(kind or "")
        kind_bonus = {
            "妖": 10,
            "妖君": 10,
            "鬼": 8,
            "游魂": 9,
            "兽": 2,
            "龙": 0,
            "龙影": 0,
            "魔": 4,
            "魔将": 2,
            "兵": -3,
            "古卫": -8,
            "傀": -10,
        }.get(kind, 0)
        boss_penalty = -6 if boss else 0
        speed = 88 + min(36, max(1, int(level)) * 0.36) + kind_bonus + boss_penalty
        return max(62.0, min(150.0, speed))

    @staticmethod
    def _enemy_skill(kind: str, level: int, boss: bool = False) -> dict[str, Any]:
        """生成怪物或 Boss 的技能配置。

        不单独建表，避免为了技能速度重构数据库。
        所有技能仍使用同一套蓄力条：interval 越小越快，power 越大越重。
        """

        kind = str(kind or "")
        boss_bonus = 0.16 if boss else 0.0
        level_bonus = min(0.18, max(1, int(level)) / 600)
        configs = {
            "妖": ("妖影撕咬", 4, 1.12, {"bleed_rate": 0.10}),
            "妖君": ("妖君裂影", 4, 1.18, {"bleed_rate": 0.12}),
            "兽": ("蛮兽冲撞", 5, 1.22, {"stun_rate": 0.08}),
            "龙": ("龙息压顶", 6, 1.30, {"mp_suppress": 0.08}),
            "龙影": ("龙影吐息", 6, 1.32, {"mp_suppress": 0.10}),
            "鬼": ("阴魂噬念", 4, 1.08, {"mp_suppress": 0.10}),
            "游魂": ("游魂缠身", 4, 1.10, {"mp_suppress": 0.08}),
            "魔": ("魔焰灼心", 5, 1.18, {"burn_rate": 0.12}),
            "魔将": ("魔将破阵", 5, 1.24, {"pierce_bonus": 0.08}),
            "兵": ("残兵破甲", 5, 1.16, {"pierce_bonus": 0.06}),
            "古卫": ("古卫镇压", 6, 1.20, {"damage_reduce": 0.08}),
            "傀": ("傀儡重压", 6, 1.18, {"damage_reduce": 0.06}),
        }
        name, interval, power, effects = configs.get(kind, ("凶煞一击", 5, 1.16, {}))
        return {
            "name": name,
            "cost_mp": 0,
            "interval": max(3, int(interval) + (1 if boss and power >= 1.28 else 0)),
            "power": power + boss_bonus + level_bonus,
            "effects": effects,
        }

    @staticmethod
    def _enemy_skill_charge_gain(skill: dict[str, Any], actor_speed: float) -> float:
        """计算怪物或 Boss 每次出手获得多少技能蓄力。"""

        interval = max(3, int(skill.get("interval", 5)))
        speed_rate = max(0.7, min(1.55, actor_speed / 100))
        return max(0.20, min(0.62, (0.66 / interval + 0.16) * speed_rate))

    @staticmethod
    def _enemy_skill_initial_charge(skill: dict[str, Any], actor_speed: float) -> float:
        """怪物/Boss 开场技能条。"""

        gain = CoreService._enemy_skill_charge_gain(skill, actor_speed)
        return min(0.82, 0.30 + gain * 0.65)

    @staticmethod
    def _pierce_rate(effects: dict[str, float]) -> float:
        """把穿透和压防统一成防御穿透率。"""

        return min(0.8, float(effects.get("pierce_bonus", 0)) + float(effects.get("defense_suppress", 0)))

    @staticmethod
    def _combo_damage(raw: int, defense_value: int, effects: dict[str, float]) -> int:
        """按连击类附魔追加一段轻伤害。"""

        if random.random() >= min(0.5, float(effects.get("combo_bonus", 0))):
            return 0
        rate = min(0.8, 0.35 + float(effects.get("combo_damage_bonus", 0)))
        return damage_after_defense(int(raw * rate), defense_value, CoreService._pierce_rate(effects))

    @staticmethod
    def _reduce_damage(damage: int, effects: dict[str, float], skill_used: bool) -> int:
        """计算最终承伤；玄盾书在本回合武器技能触发时生效。"""

        rate = float(effects.get("damage_reduce", 0)) + float(effects.get("crit_resist_bonus", 0))
        if skill_used:
            rate += float(effects.get("shield_bonus", 0))
        return max(1, int(damage * (1 - min(0.7, rate))))

    @staticmethod
    def _suppress_mp(mp: int, max_mp_value: int, effects: dict[str, float]) -> int:
        """按断念类附魔削掉对手精神。"""

        rate = min(0.25, float(effects.get("mp_suppress", 0)))
        if rate <= 0:
            return mp
        return max(0, int(mp) - int(max_mp_value * rate))

    def recalc_player(self, client_id: str) -> dict[str, Any]:
        """按经验重算等级和基础数值。"""

        with self.db.transaction() as conn:
            return self.recalc_player_conn(conn, client_id)

    def recalc_player_conn(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        """在事务里按经验重算等级和基础数值。"""

        player = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
        if not player:
            raise ValueError("玩家不存在")
        exp = min(max(0, int(player["exp"] or 0)), player_exp_for_level(MAX_LEVEL))
        level = level_from_exp(exp)
        physique_value = int(player["physique_value"])
        physique_def = conn.execute(
            "SELECT physique_value FROM physique_defs WHERE physique_id = ?",
            (player["physique_id"],),
        ).fetchone()
        if physique_def:
            physique_value = int(physique_def["physique_value"])
        bonuses = self.equipment_bonuses_conn(conn, client_id)
        hp_max = max_hp(level, physique_value, int(bonuses["max_hp_bonus"]))
        mp_max = max_mp(level, int(bonuses["max_mp_bonus"]))
        attack_value = base_attack(level)
        defense_value = defense(level, physique_value, int(bonuses["defense_bonus"]))
        conn.execute(
            """
            UPDATE players
            SET level = ?, exp = ?, max_hp = ?, max_mp = ?, hp = min(hp, ?), mp = min(mp, ?),
                physique_value = ?, base_attack = ?, defense = ?
            WHERE client_id = ?
            """,
            (level, exp, hp_max, mp_max, hp_max, mp_max, physique_value, attack_value, defense_value, client_id),
        )
        row = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
        return dict(row) if row else dict(player)

    def add_exp(self, client_id: str, amount: int) -> tuple[int, int]:
        """增加经验，返回旧等级和新等级。"""

        with self.db.transaction() as conn:
            return self.add_exp_conn(conn, client_id, amount)

    def add_exp_conn(self, conn: sqlite3.Connection, client_id: str, amount: int) -> tuple[int, int]:
        """在事务里增加经验，返回旧等级和新等级。"""

        player = conn.execute("SELECT * FROM players WHERE client_id = ?", (client_id,)).fetchone()
        if not player:
            return 1, 1
        old_level = player["level"]
        cap_exp = player_exp_for_level(MAX_LEVEL)
        current_exp = min(max(0, int(player["exp"] or 0)), cap_exp)
        next_exp = min(cap_exp, current_exp + max(0, int(amount)))
        conn.execute(
            "UPDATE players SET exp = ? WHERE client_id = ?",
            (next_exp, client_id),
        )
        player = self.recalc_player_conn(conn, client_id)
        return old_level, player["level"]

    def add_stones(self, client_id: str, amount: int) -> None:
        """增加随身源石。"""

        if amount <= 0:
            return
        self.db.execute(
            "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
            (amount, client_id),
        )

    def spend_stones_conn(self, conn: sqlite3.Connection, client_id: str, amount: int) -> bool:
        """在事务里扣源石。"""

        if amount < 0:
            return False
        row = conn.execute("SELECT source_stones FROM players WHERE client_id = ?", (client_id,)).fetchone()
        if not row or row["source_stones"] < amount:
            return False
        conn.execute(
            "UPDATE players SET source_stones = source_stones - ? WHERE client_id = ?",
            (amount, client_id),
        )
        return True

    def item_def_by_name(self, name: str) -> dict[str, Any] | None:
        """按名称读取背包物品定义。"""

        return self.db.fetch_one("SELECT * FROM item_defs WHERE name = ?", (name.strip(),))

    def item_def(self, item_id: str) -> dict[str, Any] | None:
        """按 id 读取背包物品定义。"""

        return self.db.fetch_one("SELECT * FROM item_defs WHERE item_id = ?", (item_id,))

    def ring_item_def_by_name(self, name: str) -> dict[str, Any] | None:
        """按名称读取纳戒物品定义。"""

        return self.db.fetch_one("SELECT * FROM ring_item_defs WHERE name = ?", (name.strip(),))

    def ring_item_def(self, ring_item_id: str) -> dict[str, Any] | None:
        """按 id 读取纳戒物品定义。"""

        return self.db.fetch_one(
            "SELECT * FROM ring_item_defs WHERE ring_item_id = ?",
            (ring_item_id,),
        )

    def maybe_upgrade_extreme_book(self, ring_item_id: str, location_name: str, play_bonus: float = 0.0) -> str:
        """按城池民生恩赐把普通技能书升级为极版；没有命中时返回原 id。"""

        item_id = str(ring_item_id or "")
        if not item_id or item_id.startswith("extreme_"):
            return item_id
        item = self.ring_item_def(item_id)
        if not item or item.get("category") != "技能书":
            return item_id
        extreme_id = f"extreme_{item_id}"
        if not self.ring_item_def(extreme_id):
            return item_id
        chance = self._extreme_book_upgrade_chance(location_name, play_bonus)
        if chance <= 0 or random.random() >= chance:
            return item_id
        return extreme_id

    def maybe_upgrade_extreme_book_item(
        self,
        item: dict[str, Any] | None,
        location_name: str,
        play_bonus: float = 0.0,
    ) -> dict[str, Any] | None:
        """把抽到的技能书行替换为极版行，方便掉落文本直接使用新名字。"""

        if not item:
            return None
        upgraded_id = self.maybe_upgrade_extreme_book(str(item.get("ring_item_id") or ""), location_name, play_bonus)
        if upgraded_id == str(item.get("ring_item_id") or ""):
            return item
        return self.ring_item_def(upgraded_id) or item

    def _extreme_book_upgrade_chance(self, location_name: str, play_bonus: float = 0.0) -> float:
        """极版技能书概率：民生阶数提供底盘，玩法入口再给少量修正。"""

        row = self.db.fetch_one(
            "SELECT * FROM city_world_states WHERE location_name = ?",
            (str(location_name or ""),),
        )
        tier = 0
        if row:
            values = [
                int(row.get("life_food") or 0),
                int(row.get("life_salt") or 0),
                int(row.get("life_water") or 0),
                int(row.get("life_cloth") or 0),
                int(row.get("life_fuel") or 0),
            ]
            thresholds = (100, 240, 430, 680, 1000, 1400, 1900, 2500, 3200, 4000)
            tiers = [sum(1 for threshold in thresholds if value >= threshold) for value in values]
            tier = int(sum(tiers) / len(tiers)) if tiers else 0
            if tiers and min(tiers) <= tier - 2:
                tier -= 1
        return min(0.06, max(0.0, 0.002 + max(0, min(10, tier)) * 0.0045 + float(play_bonus)))

    def add_backpack_conn(self, conn: sqlite3.Connection, client_id: str, item_id: str, quantity: int) -> None:
        """在事务里增加背包物品。"""

        if quantity <= 0:
            return
        conn.execute(
            """
            INSERT INTO backpack_items (client_id, item_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id, item_id)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (client_id, item_id, quantity),
        )

    def can_add_backpack_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        item_id: str,
        quantity: int,
    ) -> tuple[bool, str]:
        """检查背包是否还能放入指定物品。"""

        if quantity <= 0:
            return True, ""

        player = conn.execute(
            "SELECT backpack_limit, weight_limit FROM players WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT name, weight, stack_limit FROM item_defs WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if not player or not item:
            return False, T.hint("玩家或物品不存在。", "确认已创建用户，并检查物品名称是否正确。")

        weight_row = conn.execute(
            """
            SELECT COALESCE(SUM(b.quantity * i.weight), 0) AS total
            FROM backpack_items b
            JOIN item_defs i ON i.item_id = b.item_id
            WHERE b.client_id = ?
            """,
            (client_id,),
        ).fetchone()
        weight_after = int(weight_row["total"]) + int(item["weight"]) * quantity
        if weight_after > int(player["weight_limit"]):
            return False, T.hint(
                f"背包负重不足，放入后会变成 {weight_after}/{player['weight_limit']}。",
                "先发送：自动出售 清理背包，或发送：出售 物品名 数量 处理指定物品。<自动出售><背包>",
            )

        current = conn.execute(
            "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
            (client_id, item_id),
        ).fetchone()
        current_quantity = int(current["quantity"]) if current else 0
        if current_quantity + quantity > int(item["stack_limit"]):
            return False, T.hint(
                f"{item['name']} 堆叠上限不足，最多 {item['stack_limit']}。",
                "先出售或使用一部分同名物品，再重新领取或购买。",
            )

        if not current:
            kind_row = conn.execute(
                "SELECT COUNT(*) AS total FROM backpack_items WHERE client_id = ? AND quantity > 0",
                (client_id,),
            ).fetchone()
            if int(kind_row["total"]) + 1 > int(player["backpack_limit"]):
                return False, T.hint(
                    f"背包格子不足，最多 {player['backpack_limit']} 种物品。",
                    "先出售不需要的背包物品，再重新领取或购买。",
                )

        return True, ""

    def remove_backpack_conn(self, conn: sqlite3.Connection, client_id: str, item_id: str, quantity: int) -> bool:
        """在事务里扣除背包物品。"""

        row = conn.execute(
            "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
            (client_id, item_id),
        ).fetchone()
        if not row or row["quantity"] < quantity:
            return False
        left = row["quantity"] - quantity
        if left:
            conn.execute(
                "UPDATE backpack_items SET quantity = ? WHERE client_id = ? AND item_id = ?",
                (left, client_id, item_id),
            )
        else:
            conn.execute(
                "DELETE FROM backpack_items WHERE client_id = ? AND item_id = ?",
                (client_id, item_id),
            )
        return True

    def add_ring_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        ring_item_id: str,
        quantity: int,
    ) -> None:
        """在事务里增加纳戒物品。"""

        if quantity <= 0:
            return
        if self._is_gem_conn(conn, ring_item_id):
            self.add_gem_conn(conn, client_id, ring_item_id, 1, quantity)
            return
        conn.execute(
            """
            INSERT INTO ring_items (client_id, ring_item_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id, ring_item_id)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (client_id, ring_item_id, quantity),
        )

    def remove_ring_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        ring_item_id: str,
        quantity: int,
    ) -> bool:
        """在事务里扣除纳戒物品。"""

        if self._is_gem_conn(conn, ring_item_id):
            return self.remove_gem_conn(conn, client_id, ring_item_id, 1, quantity)

        row = conn.execute(
            "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
            (client_id, ring_item_id),
        ).fetchone()
        if not row or row["quantity"] < quantity:
            return False
        left = row["quantity"] - quantity
        if left:
            conn.execute(
                "UPDATE ring_items SET quantity = ? WHERE client_id = ? AND ring_item_id = ?",
                (left, client_id, ring_item_id),
            )
        else:
            conn.execute(
                "DELETE FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
                (client_id, ring_item_id),
            )
        return True

    def add_gem_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        gem_id: str,
        level: int,
        quantity: int,
    ) -> None:
        """在事务里增加指定等级的宝石库存。"""

        if quantity <= 0:
            return
        conn.execute(
            """
            INSERT INTO gem_items (client_id, gem_id, level, quantity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, gem_id, level)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (client_id, gem_id, max(1, int(level)), quantity),
        )

    def remove_gem_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        gem_id: str,
        level: int,
        quantity: int,
    ) -> bool:
        """在事务里扣除指定等级的宝石库存。"""

        row = conn.execute(
            """
            SELECT quantity FROM gem_items
            WHERE client_id = ? AND gem_id = ? AND level = ?
            """,
            (client_id, gem_id, max(1, int(level))),
        ).fetchone()
        if not row or row["quantity"] < quantity:
            return False
        left = row["quantity"] - quantity
        if left:
            conn.execute(
                """
                UPDATE gem_items
                SET quantity = ?
                WHERE client_id = ? AND gem_id = ? AND level = ?
                """,
                (left, client_id, gem_id, max(1, int(level))),
            )
        else:
            conn.execute(
                """
                DELETE FROM gem_items
                WHERE client_id = ? AND gem_id = ? AND level = ?
                """,
                (client_id, gem_id, max(1, int(level))),
            )
        return True

    @staticmethod
    def _is_gem_conn(conn: sqlite3.Connection, ring_item_id: str) -> bool:
        """判断纳戒物品是否是宝石。"""

        row = conn.execute(
            "SELECT category FROM ring_item_defs WHERE ring_item_id = ?",
            (ring_item_id,),
        ).fetchone()
        return bool(row and row["category"] == "宝石")

    def backpack_weight(self, client_id: str) -> int:
        """计算背包负重。"""

        rows = self.db.fetch_all(
            """
            SELECT b.quantity, i.weight
            FROM backpack_items b
            JOIN item_defs i ON i.item_id = b.item_id
            WHERE b.client_id = ?
            """,
            (client_id,),
        )
        return sum(row["quantity"] * row["weight"] for row in rows)

    def backpack_rows(self, client_id: str) -> list[dict[str, Any]]:
        """读取背包明细。"""

        return self.db.fetch_all(
            """
            SELECT b.item_id, b.quantity, i.name, i.weight, i.category, i.usable, i.base_price, i.effect
            FROM backpack_items b
            JOIN item_defs i ON i.item_id = b.item_id
            WHERE b.client_id = ? AND b.quantity > 0
            ORDER BY i.category, i.name
            """,
            (client_id,),
        )

    def ring_rows(self, client_id: str) -> list[dict[str, Any]]:
        """读取纳戒明细。"""

        rows = self.db.fetch_all(
            """
            SELECT r.ring_item_id, r.quantity, e.name, e.category, e.usable, e.effect, NULL AS level
            FROM ring_items r
            JOIN ring_item_defs e ON e.ring_item_id = r.ring_item_id
            WHERE r.client_id = ? AND r.quantity > 0
              AND e.category != '宝石'
            ORDER BY e.category, e.name
            """,
            (client_id,),
        )
        rows.extend(self.gem_rows(client_id))
        return sorted(rows, key=lambda row: (row["category"], row["name"], row.get("level") or 0))

    def gem_rows(self, client_id: str) -> list[dict[str, Any]]:
        """读取纳戒里按等级分组的宝石库存。"""

        return self.db.fetch_all(
            """
            SELECT g.gem_id AS ring_item_id, g.quantity, g.level,
                   e.name, e.category, e.quality, e.usable, e.effect
            FROM gem_items g
            JOIN ring_item_defs e ON e.ring_item_id = g.gem_id
            WHERE g.client_id = ? AND g.quantity > 0
            ORDER BY e.name, g.level
            """,
            (client_id,),
        )

    def resolve_gem_level_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        gem_id: str,
        gem_name: str,
        wanted_level: int | None,
        example_template: str,
    ) -> tuple[int | None, str | None]:
        """确定要操作的宝石等级；同名多等级时要求用户写清等级。"""

        if wanted_level is not None:
            return wanted_level, None

        rows = conn.execute(
            """
            SELECT level, quantity FROM gem_items
            WHERE client_id = ? AND gem_id = ? AND quantity > 0
            ORDER BY level
            """,
            (client_id, gem_id),
        ).fetchall()
        if not rows:
            return 1, None
        if len(rows) == 1:
            return int(rows[0]["level"]), None

        level = int(rows[-1]["level"])
        example = example_template.format(name=gem_name, level=level)
        options = "、".join(f"{row['level']}级x{row['quantity']}" for row in rows)
        return None, T.hint(
            f"纳戒里有多种等级的 {gem_name}。",
            f"请写清等级，例如：{example}。现有：{options}",
        )

    def format_player_name(self, client_id: str) -> str:
        """返回玩家展示名；对外回复不展示 client_id。"""

        player = self.player(client_id)
        if not player:
            return "未知道友"
        return str(player["display_name"])

    def next_level_text(self, player: dict[str, Any]) -> str:
        """返回升级进度文本。"""

        if player["level"] >= MAX_LEVEL:
            return "已满级"
        exp = min(max(0, int(player["exp"])), player_exp_for_level(MAX_LEVEL))
        current = exp - player_exp_for_level(player["level"])
        need = exp_need(player["level"])
        return f"{current}/{need}"


def format_effect(effect_text: Any) -> str:
    """把效果配置转成展示文本，支持 JSON 字符串和 dict。"""

    effect = dict(effect_text) if isinstance(effect_text, dict) else load_json(effect_text, {})
    parts: list[str] = []
    if effect.get("exp_delta"):
        parts.append(f"经验+{effect['exp_delta']}")
    if effect.get("random_exp_min") is not None:
        parts.append(f"经验+{effect['random_exp_min']}-{effect['random_exp_max']}")
    if effect.get("random_stones_min") is not None:
        parts.append(f"源石+{effect['random_stones_min']}-{effect['random_stones_max']}")
    if effect.get("random_stones_segments"):
        texts = []
        for segment in effect["random_stones_segments"]:
            if not isinstance(segment, dict):
                continue
            texts.append(f"{segment.get('min_level')}-{segment.get('max_level')}级:" f"{segment.get('min')}-{segment.get('max')}")
        if texts:
            parts.append("源石按等级段随机(" + "；".join(texts) + ")")
    if effect.get("hp_delta"):
        parts.append(f"血气+{effect['hp_delta']}")
    if effect.get("mp_delta"):
        parts.append(f"精神+{effect['mp_delta']}")
    if effect.get("hp_ratio"):
        parts.append(f"血气+{int(effect['hp_ratio'] * 100)}%")
    if effect.get("mp_ratio"):
        parts.append(f"精神+{int(effect['mp_ratio'] * 100)}%")
    if effect.get("wash_physique"):
        parts.append("洗髓体质，大概率升阶，小概率回落")
    if effect.get("enchant_id"):
        parts.append("武器附魔")
    weapon_max_level_delta = effect.get("weapon_max_level_delta")
    if isinstance(weapon_max_level_delta, int | float) and weapon_max_level_delta:
        text = f"武器等级上限{int(weapon_max_level_delta):+d}"
        weapon_max_level_cap = effect.get("weapon_max_level_cap")
        if isinstance(weapon_max_level_cap, int | float) and weapon_max_level_cap:
            text += f"，最高{int(weapon_max_level_cap)}级"
        parts.append(text)
    bonus_labels = {
        "max_hp_bonus": "血气上限",
        "max_mp_bonus": "精神上限",
        "mp_bonus": "精神上限",
        "defense_bonus": "防御",
    }
    for key, label in bonus_labels.items():
        value = effect.get(key)
        if isinstance(value, int | float) and value:
            parts.append(f"{label}{value:+g}")
    rate_labels = {
        "dodge_bonus": "闪避",
        "recover_bonus": "恢复",
        "explore_bonus": "探险",
        "crit_resist_bonus": "承伤减免",
    }
    for key, label in rate_labels.items():
        value = effect.get(key)
        if isinstance(value, int | float) and value:
            parts.append(f"{label}{value * 100:+.1f}%")
    combat_labels = {
        "hit_bonus": "命中稳定",
        "pierce_bonus": "防御穿透",
        "life_steal": "吸血",
        "shield_bonus": "技能护盾",
        "counter_rate": "反击",
        "mp_suppress": "精神压制",
        "defense_suppress": "压低防御",
        "combo_bonus": "连击概率",
        "damage_reduce": "最终减伤",
        "skill_power_bonus": "技能威力",
        "heavy_bonus": "重击威力",
        "combo_damage_bonus": "连击伤害",
        "single_hit_bonus": "单次爆发",
        "burn_rate": "灼烧",
        "bleed_rate": "流血",
        "stun_rate": "行动条压制",
    }
    for key, label in combat_labels.items():
        value = effect.get(key)
        if isinstance(value, int | float) and value:
            parts.append(f"{label}{value * 100:+.1f}%")
    interval_delta = effect.get("interval_delta")
    if isinstance(interval_delta, int | float) and interval_delta:
        direction = "变慢" if interval_delta > 0 else "变快"
        parts.append(f"技能蓄势基准{int(interval_delta):+d}({direction})")
    trade_bonus = effect.get("trade_bonus")
    if isinstance(trade_bonus, int | float) and trade_bonus:
        if trade_bonus > 0:
            parts.append(f"跑商手续费-{trade_bonus * 100:.1f}%")
        else:
            parts.append(f"跑商手续费+{abs(trade_bonus) * 100:.1f}%")
    return "，".join(parts) if parts else "无主动效果"


def choose_one(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """随机选择一行。"""

    values = list(rows)
    if not values:
        return None
    return random.choice(values)


__all__ = [
    "Any",
    "CoreService",
    "business_day",
    "choose_one",
    "computed_weapon_attack",
    "custom_label",
    "equipped_flame_multiplier",
    "equipped_flame_name",
    "dt",
    "dump_json",
    "enchant_label_name",
    "fixed_equipment_label",
    "final_attack",
    "format_effect",
    "load_json",
    "merge_numeric_effects",
    "money",
    "now",
    "parse_name_level",
    "parse_name_quantity_optional",
    "quality_factor",
    "random",
    "random_quality",
    "row_value",
    "split_words",
    "sqlite3",
    "timedelta",
    "to_int",
    "ts",
    "validate_name",
    "weapon_id_label",
    "weapon_attack_value",
    "weapon_label_name",
    "world_state_for_day",
]
