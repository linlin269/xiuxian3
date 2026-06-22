"""修仙模块通用公式。"""

from __future__ import annotations

from math import floor, sqrt

from .constants import (
    BOOK_RECYCLE_MIN_RATE,
    BOOK_RECYCLE_PRESSURE_FACTOR,
    BOOK_RECYCLE_SINGLE_CAP_BASE,
    BOOK_RECYCLE_SINGLE_CAP_LEVEL_BONUS,
    BOOK_RECYCLE_SOFT_BASE,
    BOOK_RECYCLE_SOFT_LEVEL_BONUS,
    EXP_CURVE_POWER,
    EXP_LATE_END_FACTOR,
    EXP_LATE_END_POWER,
    EXP_LATE_HIGH_FACTOR,
    EXP_LATE_HIGH_POWER,
    EXP_LATE_MID_FACTOR,
    EXP_LATE_MID_POWER,
    EXP_LATE_PROGRESS_SPAN,
    EXP_LATE_START_LEVEL,
    GEM_RECYCLE_MIN_RATE,
    GEM_RECYCLE_PRESSURE_FACTOR,
    GEM_RECYCLE_SINGLE_CAP_BASE,
    GEM_RECYCLE_SINGLE_CAP_LEVEL_BONUS,
    GEM_RECYCLE_SOFT_BASE,
    GEM_RECYCLE_SOFT_LEVEL_BONUS,
    MAX_LEVEL,
    PLAYER_BASE_ATTACK,
    PLAYER_EXP_LEVEL_BASE,
    PLAYER_EXP_POWER_BASE,
    REST_FAST_SECONDS,
    REST_FULL_MINUTES,
    SPECIAL_SELL_PRESSURE_FACTOR,
    WEAPON_EXP_PER_ACTION,
    WEAPON_EXP_ACTION_BONUS_RATE,
    WEAPON_EXP_DEALT_RATIO_CAP,
    WEAPON_EXP_DEALT_WEIGHT,
    WEAPON_EXP_LEVEL_DOWN_FLOOR,
    WEAPON_EXP_LEVEL_DOWN_STEP,
    WEAPON_EXP_LEVEL_UP_CAP,
    WEAPON_EXP_LEVEL_UP_STEP,
    WEAPON_EXP_LEVEL_BASE,
    WEAPON_EXP_NO_ACTION_FLOOR_RATE,
    WEAPON_EXP_POWER_BASE,
    WEAPON_EXP_TAKEN_RATIO_CAP,
    WEAPON_EXP_TAKEN_WEIGHT,
    SPECIAL_SELL_MIN_RATE,
    SPECIAL_SELL_SOFT_BASE,
    SPECIAL_SELL_SOFT_LEVEL_BONUS,
    TRADE_DAILY_PROFIT_MIN_RATE,
    TRADE_DAILY_PLAYER_SOFT_MAX_SHARE,
    TRADE_DAILY_PLAYER_SOFT_MIN_QUANTITY,
    TRADE_DAILY_PLAYER_SOFT_SHARE_MULTIPLIER,
    TRADE_DAILY_REWARD_MIN_NET,
    TRADE_DAILY_REWARD_MIN_QUANTITY,
    TRADE_DAILY_REWARD_NET_SOFT_RATE,
    TRADE_DAILY_REWARD_QUANTITY_SOFT_RATE,
    TRADE_DAILY_SOFT_BASE_QUANTITY,
    TRADE_DAILY_SOFT_PER_ACTIVE_QUANTITY,
    TRADE_PROFIT_GLOBAL_PRESSURE_WEIGHT,
    TRADE_PROFIT_PLAYER_PRESSURE_WEIGHT,
    TRADE_PROFIT_PRESSURE_FACTOR,
    WEAPON_RECYCLE_MIN_RATE,
    WEAPON_RECYCLE_PRESSURE_FACTOR,
    WEAPON_RECYCLE_SINGLE_CAP_BASE,
    WEAPON_RECYCLE_SINGLE_CAP_LEVEL_BONUS,
    WEAPON_RECYCLE_SOFT_BASE,
    WEAPON_RECYCLE_SOFT_LEVEL_BONUS,
)


def money(value: int) -> str:
    """把源石数量转成稳定文本。"""

    return f"{max(0, int(value)):,}".replace(",", "")


def exp_need(level: int) -> int:
    """计算当前等级升到下一级需要的经验。"""

    level = max(1, min(MAX_LEVEL, int(level)))
    if level >= MAX_LEVEL:
        return 0
    base_need = floor(PLAYER_EXP_POWER_BASE * (level**EXP_CURVE_POWER) + PLAYER_EXP_LEVEL_BASE * level)
    if level <= EXP_LATE_START_LEVEL:
        return base_need

    return floor(base_need * _late_exp_difficulty(level))


def level_from_exp(exp: int) -> int:
    """按累计经验计算等级。"""

    level = 1
    left = max(0, int(exp))
    while level < MAX_LEVEL:
        need = exp_need(level)
        if left < need:
            break
        left -= need
        level += 1
    return level


def player_exp_for_level(level: int) -> int:
    """计算玩家达到指定等级所需的累计经验。"""

    target = max(1, min(MAX_LEVEL, int(level)))
    return sum(exp_need(current_level) for current_level in range(1, target))


def weapon_exp_need(level: int) -> int:
    """计算武器当前等级升到下一级需要的经验。"""

    level = max(0, min(MAX_LEVEL - 1, int(level)))
    curve_level = max(1, level)
    base_need = floor(WEAPON_EXP_POWER_BASE * (curve_level**EXP_CURVE_POWER) + WEAPON_EXP_LEVEL_BASE * curve_level)
    if curve_level <= EXP_LATE_START_LEVEL:
        return max(1, base_need)
    return max(1, floor(base_need * _late_exp_difficulty(curve_level)))


def _late_exp_difficulty(level: int) -> float:
    """40 级后统一成长压力曲线。"""

    progress = max(0.0, (int(level) - EXP_LATE_START_LEVEL) / max(1, EXP_LATE_PROGRESS_SPAN))
    return (
        1
        + EXP_LATE_MID_FACTOR * (progress**EXP_LATE_MID_POWER)
        + EXP_LATE_HIGH_FACTOR * (progress**EXP_LATE_HIGH_POWER)
        + EXP_LATE_END_FACTOR * (progress**EXP_LATE_END_POWER)
    )


def weapon_exp_for_level(level: int) -> int:
    """计算武器达到指定等级所需的累计经验。"""

    level = max(0, min(MAX_LEVEL, int(level)))
    return sum(weapon_exp_need(current_level) for current_level in range(level))


def weapon_level_from_exp(exp: int, max_level: int) -> int:
    """按累计经验和武器等级上限计算武器等级。"""

    level = 0
    left = max(0, int(exp))
    cap = max(0, min(MAX_LEVEL, int(max_level)))
    while level < cap:
        need = weapon_exp_need(level)
        if left < need:
            break
        left -= need
        level += 1
    return level


def weapon_exp_progress(exp: int, level: int, max_level: int) -> tuple[int, int]:
    """返回当前武器等级内的经验进度和本级需求。"""

    level_int = max(0, min(int(level), int(max_level), MAX_LEVEL))
    if level_int >= max(0, min(MAX_LEVEL, int(max_level))):
        return 0, 0
    current_floor = weapon_exp_for_level(level_int)
    return max(0, int(exp) - current_floor), weapon_exp_need(level_int)


def weapon_exp_from_combat(
    action_count: int,
    *,
    player_action_count: int = 0,
    player_level: int = 1,
    opponent_level: int = 1,
    damage_dealt: int = 0,
    damage_taken: int = 0,
    opponent_max_hp: int = 1,
    player_max_hp: int = 1,
    battle_factor: float = 1.0,
) -> int:
    """按战斗时长、难度和实际承压计算武器经验。

    武器经验以整场战斗行动时长为底，实际出手只给小额加成；
    这样轻武器仍有频率收益，重武器即使没来得及出手也能按实战承压获得保底。
    """

    actions = max(0, int(action_count))
    player_actions = max(0, int(player_action_count))
    dealt = max(0, int(damage_dealt))
    taken = max(0, int(damage_taken))
    if actions <= 0 and player_actions <= 0 and dealt <= 0 and taken <= 0:
        return 0

    timeline = max(actions, player_actions, 1)
    base = timeline * WEAPON_EXP_PER_ACTION
    action_bonus = floor(min(player_actions, timeline) * WEAPON_EXP_PER_ACTION * WEAPON_EXP_ACTION_BONUS_RATE)
    level_factor = _weapon_exp_level_factor(player_level, opponent_level)
    situation_factor = _weapon_exp_situation_factor(dealt, taken, opponent_max_hp, player_max_hp)
    factor = _clamp_float(
        0.5,
        2.8,
        level_factor * situation_factor * _clamp_float(0.5, 2.0, battle_factor),
    )
    value = floor((base + action_bonus) * factor)

    if player_actions <= 0:
        no_action_floor = floor(
            timeline
            * WEAPON_EXP_PER_ACTION
            * WEAPON_EXP_NO_ACTION_FLOOR_RATE
            * _clamp_float(0.7, 1.7, level_factor * battle_factor)
        )
        value = max(value, no_action_floor, WEAPON_EXP_PER_ACTION)

    return max(1, value)


def _weapon_exp_level_factor(player_level: int, opponent_level: int) -> float:
    """武器经验的等级差倍率，比人物经验更平缓。"""

    diff = int(opponent_level) - int(player_level)
    if diff >= 0:
        return min(WEAPON_EXP_LEVEL_UP_CAP, 1.0 + diff * WEAPON_EXP_LEVEL_UP_STEP)
    return max(WEAPON_EXP_LEVEL_DOWN_FLOOR, 1.0 + diff * WEAPON_EXP_LEVEL_DOWN_STEP)


def _weapon_exp_situation_factor(damage_dealt: int, damage_taken: int, opponent_max_hp: int, player_max_hp: int) -> float:
    """战况倍率：打穿对手和承受压力都会增加武器实战经验。"""

    dealt_ratio = min(WEAPON_EXP_DEALT_RATIO_CAP, max(0, int(damage_dealt)) / max(1, int(opponent_max_hp)))
    taken_ratio = min(WEAPON_EXP_TAKEN_RATIO_CAP, max(0, int(damage_taken)) / max(1, int(player_max_hp)))
    return 1.0 + dealt_ratio * WEAPON_EXP_DEALT_WEIGHT + taken_ratio * WEAPON_EXP_TAKEN_WEIGHT


def _clamp_float(minimum: float, maximum: float, value: float) -> float:
    """限制浮点倍率范围。"""

    return max(minimum, min(maximum, float(value)))


def rest_recovery_rate(elapsed_seconds: int | float) -> float:
    """计算休息恢复比例：前 1 分钟恢复一半，后 29 分钟补满。"""

    elapsed = max(0.0, float(elapsed_seconds))
    full_seconds = max(1, REST_FULL_MINUTES * 60)
    fast_seconds = max(1, min(REST_FAST_SECONDS, full_seconds))
    if elapsed <= 0:
        return 0.0
    if elapsed <= fast_seconds:
        return min(0.5, 0.5 * elapsed / fast_seconds)

    slow_seconds = max(1, full_seconds - fast_seconds)
    return min(1.0, 0.5 + 0.5 * (elapsed - fast_seconds) / slow_seconds)


def max_hp(level: int, physique: int, equipment_bonus: int = 0) -> int:
    """计算血气上限。"""

    return 100 + int(level) * 12 + int(physique) * 8 + int(equipment_bonus)


def max_mp(level: int, equipment_bonus: int = 0) -> int:
    """计算精神上限。"""

    return 60 + int(level) * 6 + int(equipment_bonus)


def base_attack(level: int) -> int:
    """计算人物低基础攻击。"""

    return PLAYER_BASE_ATTACK + floor(int(level) / 10)


def defense(level: int, physique: int, equipment_bonus: int = 0) -> int:
    """计算防御。"""

    return int(level) + int(physique) * 2 + int(equipment_bonus)


def sign_reward(level: int) -> int:
    """计算每日签到源石。"""

    return min(500 + int(level) * 30, 5_000)


def special_sell_soft_line(level: int) -> int:
    """计算特殊收购开始明显降价的参考线。"""

    return SPECIAL_SELL_SOFT_BASE + max(1, int(level)) * SPECIAL_SELL_SOFT_LEVEL_BONUS


def special_sell_price_rate(level: int, today_income: int) -> float:
    """按今日特殊出售收入计算当前收购价倍率。

    不做硬限制，玩家一直可以卖；只是卖得越多，后续价格越低。
    """

    soft_line = max(1, special_sell_soft_line(level))
    pressure = max(0, int(today_income)) / soft_line
    rate = 1.0 / (1.0 + pressure * SPECIAL_SELL_PRESSURE_FACTOR)
    return max(SPECIAL_SELL_MIN_RATE, min(1.0, rate))


def trade_profit_rate(player_used: int, global_used: int, player_soft_line: int, global_soft_line: int) -> float:
    """按今日普通跑商出售量计算利润倍率。

    不阻断买卖，只让个人集中出货和全服过热时的后续利润自然变薄。
    """

    player_pressure = max(0, int(player_used)) / max(1, int(player_soft_line))
    global_pressure = max(0, int(global_used)) / max(1, int(global_soft_line))
    pressure = (
        player_pressure * TRADE_PROFIT_PLAYER_PRESSURE_WEIGHT
        + global_pressure * TRADE_PROFIT_GLOBAL_PRESSURE_WEIGHT
    )
    rate = 1.0 / (1.0 + pressure * TRADE_PROFIT_PRESSURE_FACTOR)
    return max(TRADE_DAILY_PROFIT_MIN_RATE, min(1.0, rate))


def trade_global_soft_line(active_count: int) -> int:
    """按活跃人数计算全服普通跑商收益线。"""

    active = max(1, int(active_count))
    return max(1, TRADE_DAILY_SOFT_BASE_QUANTITY + active * TRADE_DAILY_SOFT_PER_ACTIVE_QUANTITY)


def trade_player_soft_line(active_count: int, global_soft_line: int) -> int:
    """按公平份额和最大占比计算个人普通跑商收益线。"""

    active = max(1, int(active_count))
    total = max(1, int(global_soft_line))
    if active <= 1:
        return total
    fair_share = total / active
    by_fair_share = int(fair_share * TRADE_DAILY_PLAYER_SOFT_SHARE_MULTIPLIER)
    by_max_share = int(total * TRADE_DAILY_PLAYER_SOFT_MAX_SHARE)
    return max(TRADE_DAILY_PLAYER_SOFT_MIN_QUANTITY, min(by_fair_share, by_max_share))


def trade_daily_reward_thresholds(player_soft_line: int) -> tuple[int, int]:
    """按个人收益线计算每日跑商奖励领取门槛。"""

    soft_line = max(1, int(player_soft_line))
    min_quantity = max(
        TRADE_DAILY_REWARD_MIN_QUANTITY,
        int(soft_line * TRADE_DAILY_REWARD_QUANTITY_SOFT_RATE),
    )
    min_net = max(
        TRADE_DAILY_REWARD_MIN_NET,
        int(soft_line * TRADE_DAILY_REWARD_NET_SOFT_RATE * 1_000),
    )
    return min_quantity, min_net


def weapon_recycle_single_cap(level: int) -> int:
    """计算单把武器回收价上限。"""

    return WEAPON_RECYCLE_SINGLE_CAP_BASE + max(1, int(level)) * WEAPON_RECYCLE_SINGLE_CAP_LEVEL_BONUS


def weapon_recycle_soft_line(level: int) -> int:
    """计算武器回收开始明显降价的日收入参考线。"""

    return WEAPON_RECYCLE_SOFT_BASE + max(1, int(level)) * WEAPON_RECYCLE_SOFT_LEVEL_BONUS


def weapon_recycle_price_rate(level: int, today_income: int) -> float:
    """按今日武器回收收入计算当前回收倍率。"""

    soft_line = max(1, weapon_recycle_soft_line(level))
    pressure = max(0, int(today_income)) / soft_line
    rate = 1.0 / (1.0 + pressure * WEAPON_RECYCLE_PRESSURE_FACTOR)
    return max(WEAPON_RECYCLE_MIN_RATE, min(1.0, rate))


def gem_recycle_single_cap(level: int) -> int:
    """计算单颗宝石回收价上限。"""

    return GEM_RECYCLE_SINGLE_CAP_BASE + max(1, int(level)) * GEM_RECYCLE_SINGLE_CAP_LEVEL_BONUS


def gem_recycle_soft_line(level: int) -> int:
    """计算宝石回收开始明显降价的日收入参考线。"""

    return GEM_RECYCLE_SOFT_BASE + max(1, int(level)) * GEM_RECYCLE_SOFT_LEVEL_BONUS


def gem_recycle_price_rate(level: int, today_income: int) -> float:
    """按今日宝石回收收入计算当前回收倍率。"""

    soft_line = max(1, gem_recycle_soft_line(level))
    pressure = max(0, int(today_income)) / soft_line
    rate = 1.0 / (1.0 + pressure * GEM_RECYCLE_PRESSURE_FACTOR)
    return max(GEM_RECYCLE_MIN_RATE, min(1.0, rate))


def book_recycle_single_cap(level: int) -> int:
    """计算单本技能书回收价上限。"""

    return BOOK_RECYCLE_SINGLE_CAP_BASE + max(1, int(level)) * BOOK_RECYCLE_SINGLE_CAP_LEVEL_BONUS


def book_recycle_soft_line(level: int) -> int:
    """计算技能书回收开始明显降价的日收入参考线。"""

    return BOOK_RECYCLE_SOFT_BASE + max(1, int(level)) * BOOK_RECYCLE_SOFT_LEVEL_BONUS


def book_recycle_price_rate(level: int, today_income: int) -> float:
    """按今日技能书回收收入计算当前回收倍率。"""

    soft_line = max(1, book_recycle_soft_line(level))
    pressure = max(0, int(today_income)) / soft_line
    rate = 1.0 / (1.0 + pressure * BOOK_RECYCLE_PRESSURE_FACTOR)
    return max(BOOK_RECYCLE_MIN_RATE, min(1.0, rate))


def monster_exp(monster_level: int, kind_factor: float = 1.0, player_level: int | None = None) -> int:
    """计算怪物经验。"""

    base = 80 + int(monster_level) * 22
    return int(base * monster_exp_rate(monster_level, player_level) * kind_factor)


def monster_exp_rate(monster_level: int, player_level: int | None) -> float:
    """按玩家和怪物等级差计算经验倍率。

    平级是 1.0；越级打怪奖励更高；高等级刷低级怪会明显衰减。
    """

    if player_level is None:
        return 1.0

    diff = int(monster_level) - int(player_level)
    if diff == 0:
        return 1.0
    if diff > 0:
        return min(2.0, 1.0 + diff * 0.1)
    return max(0.15, 1.0 + diff * 0.08)


def damage_after_defense(raw_damage: int, defense_value: int, pierce_rate: float = 0.0) -> int:
    """按防御公式计算最终伤害。"""

    raw_damage = max(1, int(raw_damage))
    effective_defense = max(0.0, float(defense_value) * (1.0 - max(0.0, min(0.9, pierce_rate))))
    flat_reduce = min(effective_defense * 0.2, raw_damage * 0.5)
    middle_damage = raw_damage - flat_reduce
    reduction_rate = effective_defense / (effective_defense + 300 + 30 * sqrt(effective_defense))
    reduction_rate = min(0.6, reduction_rate)
    return max(1, int(middle_damage * (1 - reduction_rate)))


def weapon_enchant_slots(max_level: int, level: int) -> int:
    """按武器上限和当前等级计算已解锁附魔栏。"""

    slots = 0
    for need in (10, 25, 40, 60, 80, 95, 100):
        if int(max_level) >= need and int(level) >= need:
            slots += 1
    return slots


def weapon_upgrade_cost(next_level: int, quality_factor: float) -> int:
    """计算武器升级源石。"""

    factor = max(1.0, float(quality_factor))
    quality_cost = max(0.65, (factor**1.55) * 0.78)
    return floor(1500 * (int(next_level) ** 1.6) * quality_cost)


def equipment_upgrade_cost(next_level: int, slot_factor: float) -> int:
    """计算装备升级源石。"""

    return floor(800 * (int(next_level) ** 1.55) * float(slot_factor))


def gem_upgrade_cost(next_level: int) -> int:
    """计算已镶嵌宝石升级源石。

    1-5 级保持平滑铺底，6 级后用立方段拉开长期消耗。
    """

    level = max(1, int(next_level))
    late_level = max(0, level - 5)
    return 5000 * (level**2) + 70000 * (late_level**3)


def page_count(total: int, page_size: int) -> int:
    """计算页数。"""

    if total <= 0:
        return 1
    return (total + page_size - 1) // page_size


__all__ = [
    "base_attack",
    "book_recycle_price_rate",
    "book_recycle_single_cap",
    "book_recycle_soft_line",
    "damage_after_defense",
    "defense",
    "equipment_upgrade_cost",
    "exp_need",
    "gem_upgrade_cost",
    "gem_recycle_price_rate",
    "gem_recycle_single_cap",
    "gem_recycle_soft_line",
    "level_from_exp",
    "max_hp",
    "max_mp",
    "money",
    "monster_exp",
    "monster_exp_rate",
    "page_count",
    "player_exp_for_level",
    "rest_recovery_rate",
    "sign_reward",
    "special_sell_price_rate",
    "special_sell_soft_line",
    "trade_daily_reward_thresholds",
    "trade_global_soft_line",
    "trade_player_soft_line",
    "trade_profit_rate",
    "weapon_enchant_slots",
    "weapon_exp_for_level",
    "weapon_exp_from_combat",
    "weapon_exp_need",
    "weapon_exp_progress",
    "weapon_level_from_exp",
    "weapon_recycle_price_rate",
    "weapon_recycle_single_cap",
    "weapon_recycle_soft_line",
    "weapon_upgrade_cost",
]
