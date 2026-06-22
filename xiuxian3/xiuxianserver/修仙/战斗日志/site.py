"""战斗日志网页。"""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..common import load_json, row_value
from ..sql import db
from ..battle_log_links import LOG_BASE_PATH
from ..combat_core import CombatCore

router = APIRouter()


@router.get(f"{LOG_BASE_PATH}/explore/{{record_id}}", response_class=HTMLResponse)
async def exploration_log(record_id: int, detail: int = 0) -> HTMLResponse:
    """探险战斗日志。"""

    record = db.fetch_one("SELECT * FROM exploration_records WHERE record_id = ?", (record_id,))
    if not record:
        raise HTTPException(status_code=404, detail="exploration record not found")
    result = _dict_json(record["result"])
    player = db.fetch_one("SELECT * FROM players WHERE client_id = ?", (record["client_id"],))
    return HTMLResponse(_render_exploration(dict(record), result, dict(player) if player else {}, bool(detail)))


@router.get(f"{LOG_BASE_PATH}/duel/{{record_id}}", response_class=HTMLResponse)
async def duel_log(record_id: int, detail: int = 0) -> HTMLResponse:
    """切磋/决斗战斗日志。"""

    record = db.fetch_one("SELECT * FROM duel_records WHERE record_id = ?", (record_id,))
    if not record:
        raise HTTPException(status_code=404, detail="duel record not found")
    result = _dict_json(row_value(record, "result", "{}"))
    return HTMLResponse(_render_duel(dict(record), result, bool(detail)))


@router.get(f"{LOG_BASE_PATH}/robbery/{{record_id}}", response_class=HTMLResponse)
async def robbery_log(record_id: int, detail: int = 0) -> HTMLResponse:
    """抢劫战斗日志。"""

    record = db.fetch_one("SELECT * FROM robbery_records WHERE record_id = ?", (record_id,))
    if not record:
        raise HTTPException(status_code=404, detail="robbery record not found")
    result = _dict_json(row_value(record, "result", "{}"))
    return HTMLResponse(_render_robbery(dict(record), result, bool(detail)))


@router.get(f"{LOG_BASE_PATH}/boss/{{record_id}}", response_class=HTMLResponse)
async def boss_log(record_id: int, player: str = Query("", alias="player"), detail: int = 0) -> HTMLResponse:
    """首领战斗日志。"""

    record = db.fetch_one("SELECT * FROM boss_challenge_records WHERE record_id = ?", (record_id,))
    if not record:
        raise HTTPException(status_code=404, detail="boss challenge record not found")
    event = db.fetch_one("SELECT * FROM seasonal_boss_events WHERE event_id = ?", (record["event_id"],))
    if not event:
        raise HTTPException(status_code=404, detail="boss event not found")
    client_id = player or str(record["client_id"])
    participant = _participant("seasonal_boss_participants", "event_id", int(event["event_id"]), client_id)
    result = _dict_json(record["result"])
    return HTMLResponse(_render_boss_challenge("首领", dict(event), dict(record), participant, result, bool(detail)))


@router.get(f"{LOG_BASE_PATH}/wormhole/{{record_id}}", response_class=HTMLResponse)
async def wormhole_log(record_id: int, player: str = Query("", alias="player"), detail: int = 0) -> HTMLResponse:
    """虫洞战斗日志。"""

    record = db.fetch_one("SELECT * FROM wormhole_challenge_records WHERE record_id = ?", (record_id,))
    if not record:
        raise HTTPException(status_code=404, detail="wormhole challenge record not found")
    event = db.fetch_one("SELECT * FROM wormholes WHERE wormhole_id = ?", (record["wormhole_id"],))
    if not event:
        raise HTTPException(status_code=404, detail="wormhole not found")
    client_id = player or str(record["client_id"])
    participant = _participant("wormhole_participants", "wormhole_id", int(event["wormhole_id"]), client_id)
    result = _dict_json(record["result"])
    return HTMLResponse(_render_boss_challenge("虫洞", dict(event), dict(record), participant, result, bool(detail)))


def _participant(table: str, key: str, value: int, client_id: str) -> dict[str, Any]:
    """读取指定玩家参与记录；未指定玩家时取伤害最高者。"""

    if client_id:
        row = db.fetch_one(f"SELECT * FROM {table} WHERE {key} = ? AND client_id = ?", (value, client_id))
    else:
        row = db.fetch_one(f"SELECT * FROM {table} WHERE {key} = ? ORDER BY damage DESC LIMIT 1", (value,))
    return dict(row) if row else {}


def _render_exploration(record: dict[str, Any], result: dict[str, Any], player: dict[str, Any], detail: bool) -> str:
    """渲染探险日志。"""

    events = result.get("events") if isinstance(result.get("events"), list) else []
    wins = sum(1 for event in events if isinstance(event, dict) and event.get("win"))
    exp_total = sum(int(event.get("exp", 0)) for event in events if isinstance(event, dict))
    weapon_exp_total = sum(_weapon_exp(event) for event in events if isinstance(event, dict))
    medicine = _medicine_text(_dict_json(result.get("medicine_used", {})))
    drops = Counter()
    ring_drops: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for key in ("drop_item_id", "location_drop_item_id"):
            item_id = str(event.get(key) or "")
            if item_id:
                drops[_item_name(item_id)] += 1
        if event.get("ring_drop_id"):
            ring_drops.append(_ring_drop_name(event))

    cards = [
        _metric("记录", f"〔{record['record_id']}〕"),
        _metric("地点", str(record["location_name"])),
        _metric("战斗", f"{len(events)} 场｜胜 {wins}｜败 {max(0, len(events) - wins)}"),
        _metric("经验", f"人物 +{exp_total}｜武器 +{weapon_exp_total}"),
        _metric("时间", f"{record['started_at']} -> {record.get('finished_at') or record['ready_at']}"),
        _metric("自动用药", medicine),
    ]
    if player:
        cards.append(_metric("最终状态", f"血气 {player.get('hp', 0)}/{player.get('max_hp', 0)}｜精神 {player.get('mp', 0)}/{player.get('max_mp', 0)}"))
    settlement = [
        ("背包掉落", _counter_text(drops)),
        ("纳戒掉落", "、".join(ring_drops) if ring_drops else "无"),
        ("武器掉落", _weapon_drop_text(result)),
        ("停止原因", _stop_reason(result)),
    ]
    battles = "\n".join(_battle_card(index, event, detail, "怪物") for index, event in enumerate(events, start=1) if isinstance(event, dict))
    if not battles:
        battles = '<section class="empty">本次没有战斗事件。</section>'
    return _layout(
        f"探险战斗日志〔{record['record_id']}〕",
        "探险战斗日志",
        "".join(cards),
        _settlement(settlement),
        battles,
        detail,
    )


def _render_duel(record: dict[str, Any], result: dict[str, Any], detail: bool) -> str:
    """渲染切磋/决斗日志。"""

    title = f"{record.get('mode') or '对战'}战斗日志〔{record['record_id']}〕"
    cards = [
        _metric("记录", f"〔{record['record_id']}〕"),
        _metric("类型", str(record.get("mode") or "对战")),
        _metric("双方", f"{_player_name(record.get('from_client_id'))} vs {_player_name(record.get('to_client_id'))}"),
        _metric("胜负", f"胜者 {_player_name(record.get('winner_id'))}｜败者 {_player_name(record.get('loser_id'))}"),
        _metric("押注", f"{record.get('stake', 0)}｜手续费 {record.get('fee', 0)}"),
        _metric("时间", str(record.get("created_at") or "")),
    ]
    settlement = [
        ("摘要", str(record.get("summary") or result.get("summary") or "无")),
        ("武器经验", _duel_weapon_exp_text(result)),
        ("最终状态", _duel_state_text(result)),
    ]
    battles = _duel_battle_card(result, detail)
    return _layout(title, "切磋/决斗战斗日志", "".join(cards), _settlement(settlement), battles, detail)


def _render_robbery(record: dict[str, Any], result: dict[str, Any], detail: bool) -> str:
    """渲染抢劫日志。"""

    cards = [
        _metric("记录", f"〔{record['record_id']}〕"),
        _metric("目标探险", f"〔{record['exploration_record_id']}〕"),
        _metric("双方", f"{_player_name(record.get('robber_id'))} 抢劫 {_player_name(record.get('target_id'))}"),
        _metric("结果", "成功" if int(record.get("success", 0)) else "失败"),
        _metric("胜者", _player_name(record.get("winner_id"))),
        _metric("时间", str(record.get("created_at") or "")),
    ]
    settlement = [
        ("掠得", str(record.get("loot_text") or "无")),
        ("仇恨", f"战前 {record.get('hate_before', 0)}｜消耗 {record.get('hate_used', 0)}"),
        ("武器经验", _duel_weapon_exp_text(result)),
        ("摘要", str(result.get("summary") or "无")),
    ]
    battles = _duel_battle_card(result, detail)
    return _layout(f"抢劫战斗日志〔{record['record_id']}〕", "抢劫战斗日志", "".join(cards), _settlement(settlement), battles, detail)


def _render_boss_challenge(
    kind: str,
    event: dict[str, Any],
    record: dict[str, Any],
    participant: dict[str, Any],
    result: dict[str, Any],
    detail: bool,
) -> str:
    """渲染首领/虫洞单次挑战日志。"""

    record_id = int(record.get("record_id") or 0)
    event_id = int(event.get("event_id") or event.get("wormhole_id") or 0)
    boss_name = str(event.get("boss_name") or "未知")
    boss_label = "首领" if kind == "首领" else "Boss"
    cards = [
        _metric("记录", f"〔{record_id}〕"),
        _metric("事件", f"〔{event_id}〕"),
        _metric("目标", boss_name),
        _metric("状态", str(event.get("status") or "")),
        _metric("本次伤害", str(record.get("damage", 0))),
        _metric("本次血量", f"{record.get('hp_before', 0)} -> {record.get('hp_after', 0)}"),
        _metric("玩家", _player_name(record.get("client_id"))),
        _metric("累计伤害", str(participant.get("damage", 0) if participant else 0)),
    ]
    weapon_exp = int(result.get("weapon_exp", 0)) if int(result.get("weapon_id", 0) or 0) > 0 else 0
    settlement = [
        ("结果", f"{'击杀' if int(record.get('killed', 0)) else '未击杀'}｜当前剩余 {event.get('hp', 0)}/{event.get('max_hp', 0)}"),
        ("战后状态", f"血气 {result.get('hp_left', 0)}/{result.get('player_max_hp', 0)}｜精神 {result.get('mp_left', 0)}/{result.get('player_max_mp', 0)}"),
        ("武器经验", f"+{weapon_exp}"),
        ("挑战次数", str(participant.get("challenge_count", 0) if participant else 0)),
        ("最近挑战", str(participant.get("last_challenge_at") or "无") if participant else "无"),
        ("奖励状态", "已领取" if participant and int(participant.get("reward_claimed", 0)) else "未领取"),
        ("事件时间", f"{event.get('opened_at', '')} -> {event.get('killed_at') or event.get('closes_at') or ''}"),
    ]
    battle = _boss_battle_card(kind, boss_label, boss_name, record, result, detail)
    return _layout(f"{kind}战斗日志〔{record_id}〕", f"{kind}战斗日志", "".join(cards), _settlement(settlement), battle, detail)


def _battle_card(index: int, event: dict[str, Any], detail: bool, enemy_label: str) -> str:
    """渲染单场探险战斗。"""

    actions = event.get("actions") if isinstance(event.get("actions"), list) else []
    state = "胜利" if event.get("win") else "失败"
    summary = str(event.get("summary") or "")
    detail_html = _actions_html(actions, str(event.get("monster") or enemy_label), detail, enemy_label)
    return f"""
<section class="battle" id="battle-{index}">
  <div class="battle-head">
    <h2>第 {index} 战｜{escape(str(event.get('monster') or enemy_label))}</h2>
    <span class="pill {('win' if event.get('win') else 'lose')}">{state}</span>
  </div>
  <p>{escape(summary or '无战斗摘要')}</p>
  <div class="battle-grid">
    {_metric("行动", f"{len(actions)} 次")}
    {_metric("经验", f"+{int(event.get('exp', 0))}")}
    {_metric("武器经验", f"+{_weapon_exp(event)}")}
    {_metric("战后", f"血气 {event.get('hp_left', 0)}｜精神 {event.get('mp_left', 0)}")}
  </div>
  <p class="drop">掉落：{escape(_event_drop_text(event))}</p>
  {detail_html}
</section>"""


def _duel_battle_card(result: dict[str, Any], detail: bool) -> str:
    """渲染对战单场。"""

    actions = result.get("actions") if isinstance(result.get("actions"), list) else []
    detail_html = _actions_html(actions, "对手", detail, "对手")
    return f"""
<section class="battle" id="battle-1">
  <div class="battle-head">
    <h2>对战过程</h2>
    <span class="pill">{escape(str(result.get('summary') or '已结算'))}</span>
  </div>
  <div class="battle-grid">
    {_metric("行动", f"{len(actions)} 次")}
    {_metric("胜者", _player_name(result.get("winner_id")))}
    {_metric("败者", _player_name(result.get("loser_id")))}
    {_metric("武器经验", _duel_weapon_exp_text(result))}
  </div>
  {detail_html}
</section>"""


def _boss_battle_card(
    kind: str,
    boss_label: str,
    boss_name: str,
    record: dict[str, Any],
    result: dict[str, Any],
    detail: bool,
) -> str:
    """渲染首领/虫洞单次挑战。"""

    actions = result.get("actions") if isinstance(result.get("actions"), list) else []
    state = "击杀" if int(record.get("killed", 0)) else "退开"
    detail_html = _actions_html(actions, boss_name, detail, boss_label)
    weapon_exp = int(result.get("weapon_exp", 0)) if int(result.get("weapon_id", 0) or 0) > 0 else 0
    return f"""
<section class="battle" id="battle-1">
  <div class="battle-head">
    <h2>{escape(kind)}挑战｜{escape(boss_name)}</h2>
    <span class="pill {('win' if int(record.get('killed', 0)) else '')}">{state}</span>
  </div>
  <div class="battle-grid">
    {_metric("行动", f"{len(actions)} 次")}
    {_metric("伤害", str(record.get("damage", 0)))}
    {_metric("目标血量", f"{record.get('hp_before', 0)} -> {record.get('hp_after', 0)}")}
    {_metric("武器经验", f"+{weapon_exp}")}
  </div>
  {detail_html}
</section>"""


def _actions_html(actions: list[dict[str, Any]], enemy_name: str, detail: bool, enemy_label: str) -> str:
    """渲染逐次出手。"""

    if not actions:
        return '<details class="rounds" open><summary>逐次出手</summary><p>无逐次出手记录。</p></details>'
    rows = "\n".join(f"<li>{escape(_action_text(action, enemy_name, enemy_label))}</li>" for action in actions if isinstance(action, dict))
    open_attr = " open" if detail else ""
    return f'<details class="rounds"{open_attr}><summary>逐次出手</summary><ol>{rows}</ol></details>'


def _action_text(action: dict[str, Any], enemy_name: str, enemy_label: str) -> str:
    """把一次行动转成人话。"""

    round_no = int(action.get("round", 0))
    actor = str(action.get("actor") or "")
    if actor == "player":
        skill = str(action.get("skill_name") or "")
        attack = f"技能「{skill}」" if action.get("skill_used") else "普通攻击"
        damage = _first_int(action, ("player_total_damage", "damage"))
        extra = _action_effect_suffix(action, include_mp_cost=True)
        target_state = _enemy_state_suffix(action, enemy_name)
        return f"第 {round_no} 次行动：我方使用{attack}，造成 {damage} 伤害{extra}{target_state}。"
    if _is_enemy_action(action, actor):
        skill = _first_text(action, ("enemy_skill_name", "monster_skill_name", "boss_skill_name", "skill_name"))
        used = any(bool(action.get(key)) for key in ("enemy_skill_used", "monster_skill_used", "boss_skill_used", "skill_used"))
        attack = f"技能「{skill}」" if used and skill else "普通攻击"
        damage = _first_int(action, ("monster_damage", "boss_damage", "enemy_damage"))
        extra = _action_effect_suffix(action, include_mp_cost=True)
        player_state = _player_state_suffix(action)
        if action.get("dodged"):
            return f"第 {round_no} 次行动：{enemy_name} 使用{attack}，被我方避开{extra}{player_state}。"
        return f"第 {round_no} 次行动：{enemy_name} 使用{attack}，造成 {damage} 伤害{extra}{player_state}。"
    left = action.get("left") if isinstance(action.get("left"), dict) else None
    right = action.get("right") if isinstance(action.get("right"), dict) else None
    if left or right:
        parts = []
        for side_name, attack in (("左方", left), ("右方", right)):
            if not attack:
                continue
            skill = str(attack.get("skill_name") or "")
            action_name = f"技能「{skill}」" if attack.get("skill_used") else "普通攻击"
            damage = int(attack.get("damage", attack.get("total_damage", 0)))
            parts.append(f"{side_name}使用{action_name}造成 {damage} 伤害")
        return f"第 {round_no} 次行动：" + "；".join(parts) + "。"
    return f"第 {round_no} 次行动：{enemy_label}战斗记录。"


def _is_enemy_action(action: dict[str, Any], actor: str) -> bool:
    """判断本条是否是敌方出手。"""

    if actor in {"enemy", "monster", "boss"}:
        return True
    return bool(action.get("monster_attack") or action.get("boss_attack"))


def _action_effect_suffix(action: dict[str, Any], *, include_mp_cost: bool = False) -> str:
    """渲染技能、连击、吸血、护身等附加效果。"""

    parts = []
    effect = CombatCore.action_effect_text(action)
    if effect:
        parts.append(effect)
    if include_mp_cost:
        mp_cost = _int_value(action.get("mp_cost"))
        if mp_cost > 0:
            parts.append(f"消耗精神 {mp_cost}")
    return "，" + "，".join(parts) if parts else ""


def _enemy_state_suffix(action: dict[str, Any], enemy_name: str) -> str:
    """渲染敌方剩余血气。"""

    hp_left = _first_present_int(action, ("monster_hp_left", "boss_hp_left"))
    if hp_left is None:
        return ""
    hp_max = _first_present_int(action, ("monster_hp_max", "boss_hp_max"))
    if hp_max is None or hp_max <= 0:
        return f"；{enemy_name} 血气 {max(0, hp_left)}"
    return f"；{enemy_name} 血气 {max(0, hp_left)}/{hp_max}"


def _player_state_suffix(action: dict[str, Any]) -> str:
    """渲染我方战后血气和精神。"""

    hp_left = _first_present_int(action, ("player_hp_left",))
    mp_left = _first_present_int(action, ("player_mp_left",))
    if hp_left is None and mp_left is None:
        return ""
    texts = []
    if hp_left is not None:
        texts.append(f"血气 {max(0, hp_left)}")
    if mp_left is not None:
        texts.append(f"精神 {max(0, mp_left)}")
    return "；我方" + "，".join(texts)


def _first_text(action: dict[str, Any], keys: tuple[str, ...]) -> str:
    """按优先级读取第一个非空文本字段。"""

    for key in keys:
        value = str(action.get(key) or "").strip()
        if value:
            return value
    return ""


def _first_int(action: dict[str, Any], keys: tuple[str, ...]) -> int:
    """按优先级读取第一个数值字段。"""

    for key in keys:
        if key in action:
            return _int_value(action.get(key))
    return 0


def _first_present_int(action: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    """读取可能为 0 的数值字段。"""

    for key in keys:
        if key in action:
            return _int_value(action.get(key))
    return None


def _int_value(value: Any) -> int:
    """把日志里的数值安全转成 int。"""

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _layout(title: str, subtitle: str, metrics: str, settlement: str, battles: str, detail: bool) -> str:
    """页面框架。"""

    detail_text = "详细模式：逐次出手默认展开" if detail else "简要模式：逐次出手默认折叠"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>{STYLE}</style>
</head>
<body>
  <header class="hero">
    <a href="/xiuxian/help">修仙帮助</a>
    <h1>{escape(title)}</h1>
    <p>{escape(subtitle)}｜{detail_text}</p>
  </header>
  <main>
    <section class="panel">
      <h2>一、日志总览</h2>
      <div class="metrics">{metrics}</div>
    </section>
    <section class="panel">
      <h2>二、掉落与结算</h2>
      {settlement}
    </section>
    <section class="panel">
      <h2>三、战斗明细</h2>
      {battles}
    </section>
  </main>
</body>
</html>"""


def _metric(label: str, value: str) -> str:
    """指标块。"""

    return f'<div class="metric"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></div>'


def _settlement(items: list[tuple[str, str]]) -> str:
    """结算列表。"""

    lines = "\n".join(f"<li><span>{escape(label)}</span><strong>{escape(value)}</strong></li>" for label, value in items)
    return f'<ul class="settlement">{lines}</ul>'


def _dict_json(value: Any) -> dict[str, Any]:
    """读取 JSON 字典。"""

    if isinstance(value, dict):
        return value
    loaded = load_json(value, {})
    return loaded if isinstance(loaded, dict) else {}


def _weapon_exp(event: dict[str, Any]) -> int:
    """单场武器经验。"""

    return int(event.get("weapon_exp", 0)) if int(event.get("weapon_id", 0) or 0) > 0 else 0


def _item_name(item_id: str) -> str:
    """物品名。"""

    row = db.fetch_one("SELECT name FROM item_defs WHERE item_id = ?", (item_id,))
    return str(row["name"]) if row else item_id


def _ring_item_name(item_id: str) -> str:
    """纳戒物品名。"""

    row = db.fetch_one("SELECT name FROM ring_item_defs WHERE ring_item_id = ?", (item_id,))
    if row:
        return str(row["name"])
    row = db.fetch_one("SELECT name FROM item_defs WHERE item_id = ?", (item_id,))
    return str(row["name"]) if row else item_id


def _player_name(client_id: Any) -> str:
    """玩家展示名。"""

    value = str(client_id or "")
    if not value:
        return "无"
    row = db.fetch_one("SELECT display_name FROM players WHERE client_id = ?", (value,))
    return str(row["display_name"]) if row else value


def _event_drop_text(event: dict[str, Any]) -> str:
    """单场掉落文本。"""

    texts = []
    if event.get("drop_item_id"):
        texts.append(f"怪物掉落 {_item_name(str(event['drop_item_id']))}")
    if event.get("location_drop_item_id"):
        texts.append(f"古界物资 {_item_name(str(event['location_drop_item_id']))}")
    if event.get("ring_drop_id"):
        texts.append(_ring_drop_name(event))
    return "、".join(texts) if texts else "无"


def _ring_drop_name(event: dict[str, Any]) -> str:
    """纳戒掉落名。"""

    name = _ring_item_name(str(event.get("ring_drop_id") or ""))
    level = int(event.get("ring_drop_level") or 0)
    return f"{name} {level}级" if level > 0 else name


def _counter_text(counter: Counter[str]) -> str:
    """计数器文本。"""

    return "、".join(f"{name} x{count}" for name, count in counter.items()) if counter else "无"


def _medicine_text(items: dict[str, Any]) -> str:
    """自动用药文本。"""

    if not items:
        return "无"
    return "、".join(f"{_ring_item_name(str(item_id))} x{int(quantity)}" for item_id, quantity in items.items())


def _weapon_drop_text(result: dict[str, Any]) -> str:
    """武器掉落文本。"""

    drops = result.get("weapon_drops")
    if not isinstance(drops, list) or not drops:
        return "无"
    names = []
    for item in drops:
        if isinstance(item, dict):
            names.append(f"{item.get('name', '武器')}[{item.get('quality', '未知')}]")
    return "、".join(names) if names else "无"


def _stop_reason(result: dict[str, Any]) -> str:
    """探险停止原因。"""

    if result.get("dead"):
        return "本体战斗失败，后续不再继续遇怪"
    if result.get("bag_full"):
        return "背包已满，后续不再继续遇怪"
    if result.get("secret_realm"):
        return "秘境轮数结束或实际冷却到点"
    return "普通探险到点"


def _duel_weapon_exp_text(result: dict[str, Any]) -> str:
    """对战武器经验。"""

    left = int(result.get("left_weapon_exp", 0)) if int(result.get("left_weapon_id", 0) or 0) > 0 else 0
    right = int(result.get("right_weapon_exp", 0)) if int(result.get("right_weapon_id", 0) or 0) > 0 else 0
    return f"{_player_name(result.get('left_id'))} +{left}｜{_player_name(result.get('right_id'))} +{right}"


def _duel_state_text(result: dict[str, Any]) -> str:
    """对战最终状态。"""

    return (
        f"{_player_name(result.get('left_id'))} 血气 {result.get('left_hp_left', 0)}/{result.get('left_max_hp', 0)}｜"
        f"{_player_name(result.get('right_id'))} 血气 {result.get('right_hp_left', 0)}/{result.get('right_max_hp', 0)}"
    )


STYLE = """
:root {
  --bg: #f6f7f2;
  --paper: #fff;
  --ink: #263238;
  --muted: #6d7a7a;
  --line: #dfe6df;
  --green: #477d5b;
  --gold: #b47b2b;
  --red: #b8554d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  line-height: 1.6;
}
a { color: inherit; }
.hero {
  padding: 28px 18px 22px;
  background: linear-gradient(135deg, #426d53, #2f4650);
  color: #fff;
}
.hero a { font-size: 13px; color: rgba(255,255,255,.82); text-decoration: none; }
.hero h1 {
  max-width: 1040px;
  margin: 18px auto 8px;
  font-size: clamp(28px, 5vw, 46px);
  font-weight: 800;
}
.hero p {
  max-width: 1040px;
  margin: 0 auto;
  color: rgba(255,255,255,.82);
}
main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 18px;
}
.panel {
  padding: 20px 0;
  border-bottom: 1px solid var(--line);
}
.panel h2 {
  margin: 0 0 14px;
  font-size: 20px;
}
.metrics,
.battle-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.metric {
  min-width: 0;
  padding: 12px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.metric span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}
.metric strong {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
}
.settlement {
  margin: 0;
  padding: 0;
  list-style: none;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.settlement li {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}
.settlement li:last-child { border-bottom: 0; }
.settlement span { color: var(--muted); }
.settlement strong { overflow-wrap: anywhere; }
.battle {
  margin: 14px 0;
  padding: 14px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.battle-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.battle h2 {
  margin: 0;
  font-size: 18px;
}
.pill {
  flex: 0 0 auto;
  padding: 3px 8px;
  border-radius: 999px;
  color: #fff;
  background: var(--gold);
  font-size: 13px;
}
.pill.win { background: var(--green); }
.pill.lose { background: var(--red); }
.drop {
  margin: 10px 0 0;
  color: var(--muted);
}
.rounds {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
}
.rounds summary {
  cursor: pointer;
  font-weight: 700;
}
.rounds ol {
  margin: 10px 0 0;
  padding-left: 22px;
}
.rounds li { padding: 4px 0; }
.empty {
  padding: 18px;
  background: var(--paper);
  border: 1px dashed var(--line);
  border-radius: 6px;
  color: var(--muted);
}
@media (max-width: 720px) {
  .metrics,
  .battle-grid {
    grid-template-columns: 1fr;
  }
  .settlement li {
    grid-template-columns: 1fr;
    gap: 2px;
  }
  .battle-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
"""
