"""战斗日志展示格式。

这里只负责把已经结算好的战斗结果排版成玩家可读文本，不参与任何数值结算。
"""

from __future__ import annotations

from typing import Any, Callable

from .battle_log_links import battle_log_markdown


def wants_detail(player: dict[str, Any] | None) -> bool:
    """玩家是否让战斗日志链接默认展开逐次出手。"""

    if not player:
        return False
    return bool(int(player.get("battle_log_detail") or 0))


def mode_text(player: dict[str, Any] | None) -> str:
    """战斗日志开关展示文本。"""

    return "详细" if wants_detail(player) else "简要"


def markdown_reply(content: str) -> dict[str, Any]:
    """生成 markdown 回复。"""

    return {
        "code": 202,
        "type": "markdown",
        "message": {
            "content": content.strip(),
        },
    }


def exploration_brief(
    *,
    record: dict[str, Any],
    player: dict[str, Any],
    events: list[dict[str, Any]],
    exp_total: int,
    weapon_exp_total: int,
    old_level: int,
    new_level: int,
    drops_text: str,
    ring_drops_text: str,
    weapon_drops_text: str,
    medicine_text: str,
    stop_reason: str,
    detail: bool = False,
) -> dict[str, Any]:
    """探险结算的简要战斗日志。"""

    wins = sum(1 for event in events if event.get("win"))
    losses = max(0, len(events) - wins)
    hp_left = int(player.get("hp", 1))
    mp_left = int(player.get("mp", 0))
    level_text = f"{old_level} → {new_level}" if new_level > old_level else f"{new_level}，未升级"
    record_id = int(record["record_id"])
    log_link = battle_log_markdown(f"探险战斗日志〔{record_id}〕", "explore", record_id, detail=detail)

    lines = [
        "> **探险结束**",
        f"> 记录 **〔{record['record_id']}〕**｜地点：{record['location_name']}",
        f"> 战斗 **{len(events)}** 场｜胜 **{wins}**｜败 **{losses}**",
        f"> 经验 **+{exp_total}**｜武器经验 **+{weapon_exp_total}**｜等级：{level_text}",
        f"> 最终状态：血气 **{hp_left}/{player['max_hp']}**｜精神 **{mp_left}/{player['max_mp']}**",
        f"> 停止原因：{stop_reason}",
        ">",
        "> **收获**",
        f"> 背包：{drops_text}",
        f"> 纳戒：{ring_drops_text}",
        f"> 武器：{weapon_drops_text}",
        f"> 自动用药：{medicine_text or '无'}",
        f"> 战斗日志：{log_link}",
        "> 当前状态：空闲",
    ]
    return markdown_reply("\n".join(lines))


def boss_brief(
    *,
    title: str,
    subtitle: str = "",
    boss_name: str,
    boss_label: str,
    player: dict[str, Any],
    result: dict[str, Any],
    damage: int,
    left_hp: int,
    max_hp: int,
    killed: bool,
    killed_text: str,
    alive_text: str,
    hurt_text: str,
    log_kind: str = "",
    record_id: int = 0,
    client_id: str = "",
    detail: bool = False,
) -> dict[str, Any]:
    """虫洞和首领挑战的简要战斗日志。"""

    weapon_exp = int(result.get("weapon_exp", 0)) if int(result.get("weapon_id", 0)) > 0 else 0
    state_text = killed_text if killed else alive_text
    if int(result.get("hp_left", 0)) <= 0:
        state_text = f"{hurt_text}；{state_text}"
    reward_text = f"｜武器经验 **+{weapon_exp}**" if weapon_exp > 0 else ""
    log_line = ""
    if log_kind and record_id:
        label = f"{boss_label}战斗日志〔{record_id}〕"
        log_line = f"> 战斗日志：{battle_log_markdown(label, log_kind, record_id, client_id=client_id, detail=detail)}"

    lines = [
        f"> **{title}**",
    ]
    lines.extend(
        [
            f"> {boss_label}：{boss_name}｜剩余 **{left_hp}/{max_hp}**",
            f"> 本次伤害 **{damage}**{reward_text}",
            f"> 我方状态：血气 **{result['hp_left']}/{player['max_hp']}**｜精神 **{result['mp_left']}/{player['max_mp']}**",
            f"> {state_text}",
        ]
    )
    if log_line:
        lines.append(log_line)
    return markdown_reply("\n".join(lines))


def duel_brief(
    *,
    title: str,
    result: dict[str, Any],
    settlement: str,
    format_player_name: Callable[[str], str],
    log_kind: str = "",
    record_id: int = 0,
    detail: bool = False,
) -> dict[str, Any]:
    """切磋和决斗的简要战斗日志。"""

    left_weapon_exp = int(result.get("left_weapon_exp", 0)) if int(result.get("left_weapon_id", 0)) > 0 else 0
    right_weapon_exp = int(result.get("right_weapon_exp", 0)) if int(result.get("right_weapon_id", 0)) > 0 else 0

    left_id = str(result.get("left_id", ""))
    right_id = str(result.get("right_id", ""))
    lines = [
        f"> **{title}**",
        f"> 胜者：{format_player_name(str(result.get('winner_id', '')))}｜败者：{format_player_name(str(result.get('loser_id', '')))}",
        (
            f"> {format_player_name(left_id)}：血气 **{result.get('left_hp_left', 0)}/{result.get('left_max_hp', 0)}**｜"
            f"精神 **{result.get('left_mp_left', 0)}/{result.get('left_max_mp', 0)}**"
        ),
        (
            f"> {format_player_name(right_id)}：血气 **{result.get('right_hp_left', 0)}/{result.get('right_max_hp', 0)}**｜"
            f"精神 **{result.get('right_mp_left', 0)}/{result.get('right_max_mp', 0)}**"
        ),
    ]
    weapon_exp_parts = []
    if left_weapon_exp > 0:
        weapon_exp_parts.append(f"{format_player_name(left_id)} +{left_weapon_exp}")
    if right_weapon_exp > 0:
        weapon_exp_parts.append(f"{format_player_name(right_id)} +{right_weapon_exp}")
    if weapon_exp_parts:
        lines.append(f"> 武器经验：{'｜'.join(weapon_exp_parts)}")
    if settlement:
        lines.append(f"> {settlement}")
    if log_kind and record_id:
        label = f"{title.replace('结束', '')}战斗日志〔{record_id}〕"
        lines.append(f"> 战斗日志：{battle_log_markdown(label, log_kind, record_id, detail=detail)}")
    return markdown_reply("\n".join(lines))
