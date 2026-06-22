"""修仙百科知识服务。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common import computed_weapon_attack, fixed_equipment_label, load_json, weapon_id_label, weapon_label_name
from ..constants import EQUIPMENT_MAX_HOLES, EQUIPMENT_SLOTS
from ..format_text import T
from ..sql import db


XIUXIAN_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class KnowledgeEntry:
    """一条可检索百科知识。"""

    title: str
    group: str
    kind: str
    body: str
    keywords: tuple[str, ...]
    source: str = ""


@dataclass(frozen=True)
class WeaponProfile:
    """从武器百科条目中提炼出来的玩法画像。"""

    title: str
    weapon_type: str
    attack: int | None
    source: str
    skill: str
    interval: int | None
    power: float | None
    cost_mp: int | None
    desc: str
    style: str
    traits: frozenset[str]


@dataclass(frozen=True)
class PlayerContext:
    """回答推荐问题时使用的玩家上下文。"""

    exists: bool
    level: int = 0
    hp: int = 0
    max_hp: int = 0
    mp: int = 0
    max_mp: int = 0
    source_stones: int = 0
    status: str = ""
    location_name: str = ""
    weapon_name: str = ""
    weapon_type: str = ""
    weapon_quality: str = ""
    weapon_source: str = ""
    weapon_level: int = 0
    weapon_max_level: int = 0
    weapon_attack: int = 0
    skill_name: str = ""
    skill_desc: str = ""
    interval: int | None = None
    power: float | None = None
    cost_mp: int | None = None
    enchant_names: tuple[str, ...] = ()


class EncyclopediaService:
    """修仙百科：启动时缓存文档和数据库知识，查询时只读内存。"""

    def __init__(self, database: Any) -> None:
        self.db = database
        self._entries: tuple[KnowledgeEntry, ...] = ()

    def load(self) -> tuple[KnowledgeEntry, ...]:
        """刷新百科知识缓存。"""

        entries: list[KnowledgeEntry] = []
        entries.extend(self._load_markdown_entries())
        entries.extend(self._load_database_entries())
        self._entries = tuple(entries)
        return self._entries

    def ask(self, client_id: str, question: str) -> str:
        """回答修仙百科问题。"""

        query = question.strip()
        if not query:
            return T.hint(
                "请在 修仙百科 后面写问题。",
                "例如：修仙百科 轻身水晶有什么用，或 修仙百科 断念杖怎么玩。<修仙百科 轻身水晶有什么用><修仙百科 武器流派><修仙百科 跑商>",
            )

        if not self._entries:
            self.load()

        matches = self._focused_matches(query, self._search_with_player_names(client_id, query))
        if not matches:
            return T.hint(
                f"暂时没有找到和「{query}」直接相关的百科资料。",
                "可以换成物品名、武器名、技能书名、地点名或玩法名再问。<修仙百科 武器><修仙百科 宝石><修仙百科 跑商>",
            )

        panel = T.panel()
        panel.section(f"修仙百科：{query}")
        entries = [entry for entry, _score in matches]
        panel.lines(self._smart_answer_lines(client_id, query, entries))
        return panel.render() + _answer_buttons(query, entries)

    def _smart_answer_lines(self, client_id: str, query: str, entries: list[KnowledgeEntry]) -> list[str]:
        """把命中资料合成答案，并在需要时带上玩家上下文。"""

        context = self._player_context(client_id, query)
        return _smart_answer_lines(
            query,
            entries,
            context,
            item_helper=lambda entry, intent: self._item_assistant_lines(client_id, query, entry, intent),
        )

    def _item_assistant_lines(
        self,
        client_id: str,
        query: str,
        entry: KnowledgeEntry,
        intent: str,
    ) -> list[str]:
        """把具体物品问答变成玩家可执行的下一步。"""

        if entry.group != "修仙物品":
            return []
        if intent not in {"usage", "build", "lookup"} and "怎么用" not in query:
            return []

        if entry.title == "开孔器":
            return self._hole_opener_lines(client_id)
        if entry.title == "洗髓液":
            return self._wash_item_lines(client_id)
        if entry.title == "淬锋丹":
            return self._temper_item_lines(client_id)
        if entry.kind == "技能书":
            return self._book_item_lines(client_id, entry)
        if entry.kind == "宝石":
            return self._gem_item_lines(client_id, entry)
        if entry.kind == "恢复类":
            return self._recover_item_lines(client_id, entry)
        return []

    def _hole_opener_lines(self, client_id: str) -> list[str]:
        """开孔器专用助手。"""

        quantity = self._ring_quantity(client_id, "kaikongqi")
        rows = self._equipment_rows(client_id)
        openable = [row for row in rows if int(row.get("hole_count") or 0) < EQUIPMENT_MAX_HOLES]
        openable.sort(key=lambda row: (-int(row.get("level") or 0), int(row.get("hole_count") or 0), _slot_order(row.get("slot"))))
        target = openable[0] if openable else None

        lines = [
            f"结论：开孔器不走“使用”。它只用来给装备位增加 1 个宝石孔，单件装备最多 {EQUIPMENT_MAX_HOLES} 孔。",
            "怎么用：先发“孔位”看哪件装备缺孔，再发“开孔 装备位”；开完后用“镶嵌 装备位 孔位号 宝石名称”放宝石。",
            f"纳戒库存：开孔器 x{quantity}。",
        ]
        if not rows:
            lines.append("当前建议：先发“装备”或“孔位”刷新装备位，再决定给哪一件开孔。")
        elif not openable:
            lines.append("当前建议：你的装备孔位已经全满，开孔器先留着；后续有新装备上限玩法再处理。")
        elif quantity <= 0:
            labels = "、".join(_equipment_hole_label(row) for row in openable[:3])
            lines.append(f"当前建议：你缺的是开孔器，不缺目标；可开孔位有 {labels}。先打岁时情劫并领取首领奖励。")
        else:
            assert target is not None
            slot = str(target["slot"])
            lines.append(f"当前建议：先开 {_equipment_hole_label(target)}，直接发送“开孔 {slot}”。")
        lines.append("来源：岁时情劫首领奖励；拿到后会进入纳戒，不占背包负重。")
        return lines

    def _wash_item_lines(self, client_id: str) -> list[str]:
        """洗髓液专用助手。"""

        quantity = self._ring_quantity(client_id, "xisuiye")
        lines = [
            "结论：洗髓液不走“使用”。它是重置体质的成长道具，入口只有“洗髓”。",
            f"纳戒库存：洗髓液 x{quantity}。",
        ]
        if quantity > 0:
            lines.append("当前建议：想赌体质就发送“洗髓”；不想换掉当前体质就先留着。")
        else:
            lines.append("当前建议：你现在没有洗髓液，优先打岁时情劫或异界虫洞，再领取对应奖励。")
        lines.append("提醒：洗髓会立刻替换体质，属于角色成长操作，不是普通恢复药。")
        return lines

    def _temper_item_lines(self, client_id: str) -> list[str]:
        """淬锋丹专用助手。"""

        quantity = self._ring_quantity(client_id, "cuifengdan")
        weapon = self._current_weapon(client_id)
        lines = [
            "结论：淬锋丹不走“使用”。它只提升武器等级上限，默认作用到已装备武器。",
            f"纳戒库存：淬锋丹 x{quantity}。",
        ]
        if not weapon:
            lines.append("当前建议：先发“武器”确认可培养目标；有目标后发“武器淬锋”或“武器淬锋 武器ID”。")
        else:
            weapon_text = f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)} Lv{weapon['level']}/{weapon['max_level']}"
            if int(weapon["max_level"]) >= 100:
                lines.append(f"当前建议：{weapon_text} 已到 100 上限，别把淬锋丹砸在它身上。")
            elif quantity > 0:
                lines.append(f"当前建议：默认目标是 {weapon_text}；确认要养它就发送“武器淬锋”。")
            else:
                lines.append(f"当前目标：{weapon_text}；你缺淬锋丹，去宗门战周期奖励里拿。")
        lines.append("来源：淬锋丹只来自宗门战奖励，属于来源唯一的专属道具。")
        return lines

    def _book_item_lines(self, client_id: str, entry: KnowledgeEntry) -> list[str]:
        """技能书助手。"""

        item = self._ring_item_def(entry.title)
        quantity = self._ring_quantity(client_id, str(item.get("ring_item_id") if item else ""))
        weapon = self._current_weapon(client_id)
        style = _enchant_style(entry.title) or "特化附魔"
        desc = _last_plain_part(entry.body).rstrip("。")
        lines = [
            f"结论：{entry.title} 是{style}技能书，不走“使用”，要用“附魔武器 武器ID 技能书名”。",
            f"纳戒库存：{entry.title} x{quantity}。",
            f"用途判断：{desc or '它会改变武器战斗倾向'}。",
        ]
        if weapon:
            weapon_text = f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}"
            lines.append(f"当前建议：如果要给当前武器附魔，发送“附魔武器 {weapon['weapon_id']} {entry.title}”；当前武器是 {weapon_text}。")
        else:
            lines.append(f"当前建议：先发“武器”选目标，再发送“附魔武器 武器ID {entry.title}”。")
        lines.append("提醒：附魔后不可撤销，同一把武器不能重复附魔同一本书。")
        return lines

    def _gem_item_lines(self, client_id: str, entry: KnowledgeEntry) -> list[str]:
        """宝石助手。"""

        item = self._ring_item_def(entry.title)
        levels = self._gem_level_rows(client_id, str(item.get("ring_item_id") if item else ""))
        level_text = "、".join(f"{row['level']}级 x{row['quantity']}" for row in levels) if levels else "无"
        target = self._first_empty_hole(client_id)
        effect = _human_effect_text(_extract_field(entry.body, "效果"))
        lines = [
            f"结论：{entry.title} 是宝石，不走“使用”，要镶嵌到已开启的装备孔位。",
            f"纳戒库存：{level_text}。",
            f"实际收益：{effect or _last_plain_part(entry.body).rstrip('。') or '看宝石效果字段'}。",
        ]
        if target:
            slot, hole_no = target
            lines.append(f"当前建议：可以先发“镶嵌 {slot} {hole_no} {entry.title}”；同名多等级时在末尾补“等级”。")
        else:
            lines.append("当前建议：先发“孔位”看有没有空孔；没有空孔就先用开孔器开孔。")
        return lines

    def _recover_item_lines(self, client_id: str, entry: KnowledgeEntry) -> list[str]:
        """恢复类物品助手。"""

        ring_item = self._ring_item_def(entry.title)
        bag_item = self._backpack_item_def(entry.title)
        ring_quantity = self._ring_quantity(client_id, str(ring_item.get("ring_item_id") if ring_item else ""))
        bag_quantity = self._backpack_quantity(client_id, str(bag_item.get("item_id") if bag_item else ""))
        effect = _human_effect_text(_extract_field(entry.body, "效果"))
        lines = [
            f"结论：{entry.title} 是恢复类物品，可以直接发“使用 {entry.title}”消耗。",
            f"库存：背包 x{bag_quantity}｜纳戒 x{ring_quantity}。",
            f"效果：{effect or _last_plain_part(entry.body).rstrip('。') or '按物品效果恢复状态'}。",
        ]
        if bag_quantity + ring_quantity > 0:
            lines.append(f"当前建议：少量补状态发“使用 {entry.title}”；要一次用多个就发“使用 {entry.title} 数量”。")
        else:
            lines.append("当前建议：你现在没有这个物品，先通过探险、首领、虫洞或商路顺药补给获取。")
        return lines

    def _ring_item_def(self, name: str) -> dict[str, Any] | None:
        """按名称读取纳戒物品定义。"""

        return self.db.fetch_one("SELECT * FROM ring_item_defs WHERE name = ?", (str(name).strip(),))

    def _backpack_item_def(self, name: str) -> dict[str, Any] | None:
        """按名称读取背包物品定义。"""

        return self.db.fetch_one("SELECT * FROM item_defs WHERE name = ?", (str(name).strip(),))

    def _ring_quantity(self, client_id: str, ring_item_id: str) -> int:
        """读取纳戒非宝石物品数量。"""

        if not ring_item_id:
            return 0
        row = self.db.fetch_one(
            "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
            (client_id, ring_item_id),
        )
        return int(row["quantity"]) if row else 0

    def _backpack_quantity(self, client_id: str, item_id: str) -> int:
        """读取背包物品数量。"""

        if not item_id:
            return 0
        row = self.db.fetch_one(
            "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
            (client_id, item_id),
        )
        return int(row["quantity"]) if row else 0

    def _gem_level_rows(self, client_id: str, gem_id: str) -> list[dict[str, Any]]:
        """读取玩家某个宝石的分级库存。"""

        if not gem_id:
            return []
        return self.db.fetch_all(
            """
            SELECT level, quantity
            FROM gem_items
            WHERE client_id = ? AND gem_id = ? AND quantity > 0
            ORDER BY level
            """,
            (client_id, gem_id),
        )

    def _equipment_rows(self, client_id: str) -> list[dict[str, Any]]:
        """读取装备位，不在百科里创建缺失装备行。"""

        rows = self.db.fetch_all(
            "SELECT * FROM fixed_equipment WHERE client_id = ?",
            (client_id,),
        )
        by_slot = {str(row.get("slot")): row for row in rows}
        return [by_slot[slot] for slot in EQUIPMENT_SLOTS if slot in by_slot]

    def _first_empty_hole(self, client_id: str) -> tuple[str, int] | None:
        """寻找第一个已开启且未镶嵌的孔位。"""

        rows = self._equipment_rows(client_id)
        if not rows:
            return None
        used_rows = self.db.fetch_all(
            "SELECT slot, hole_no FROM fixed_equipment_inlays WHERE client_id = ?",
            (client_id,),
        )
        used = {(str(row["slot"]), int(row["hole_no"])) for row in used_rows}
        for row in rows:
            slot = str(row["slot"])
            for hole_no in range(1, int(row.get("hole_count") or 0) + 1):
                if (slot, hole_no) not in used:
                    return slot, hole_no
        return None

    def _current_weapon(self, client_id: str) -> dict[str, Any] | None:
        """读取当前装备武器；没有装备时退到第一把武器。"""

        return self.db.fetch_one(
            """
            SELECT pw.*, wd.name, wd.drop_location, wd.base_attack, wd.weapon_type
            FROM player_weapons AS pw
            JOIN weapon_defs AS wd ON wd.weapon_def_id = pw.weapon_def_id
            WHERE pw.holder_id = ?
            ORDER BY pw.equipped DESC, pw.weapon_id
            LIMIT 1
            """,
            (client_id,),
        )

    def _player_context(self, client_id: str, query: str) -> PlayerContext | None:
        """对推荐类问题补充当前玩家和当前武器上下文。"""

        if not _needs_personal_context(query):
            return None

        player = self.db.fetch_one(
            """
            SELECT level, hp, max_hp, mp, max_mp, source_stones, status, location_name
            FROM players
            WHERE client_id = ?
            """,
            (client_id,),
        )
        if not player:
            return PlayerContext(exists=False)

        weapon = self.db.fetch_one(
            """
            SELECT pw.weapon_id, pw.level, pw.max_level, pw.quality, pw.enchant_effects, pw.custom_name,
                   wd.name, wd.weapon_type, wd.base_attack, wd.drop_location, wd.skill_id,
                   ws.name AS skill_name, ws.effect_desc, ws.interval, ws.power, ws.cost_mp
            FROM player_weapons AS pw
            JOIN weapon_defs AS wd ON wd.weapon_def_id = pw.weapon_def_id
            LEFT JOIN weapon_skill_defs AS ws ON ws.skill_id = wd.skill_id
            WHERE pw.holder_id = ? AND pw.equipped = 1
            LIMIT 1
            """,
            (client_id,),
        )
        base_context = {
            "exists": True,
            "level": int(player["level"]),
            "hp": int(player["hp"]),
            "max_hp": int(player["max_hp"]),
            "mp": int(player["mp"]),
            "max_mp": int(player["max_mp"]),
            "source_stones": int(player["source_stones"]),
            "status": str(player["status"]),
            "location_name": str(player["location_name"]),
        }
        if weapon:
            weapon_name = weapon["custom_name"] or weapon["name"]
            skill_name = str(weapon["skill_name"])
            weapon_id = int(weapon["weapon_id"])
            custom_rows = self.db.fetch_all(
                "SELECT slot_no, custom_name FROM weapon_enchant_names WHERE weapon_id = ?",
                (weapon_id,),
            )
            custom_names = {int(row["slot_no"]): str(row["custom_name"]) for row in custom_rows if row["custom_name"]}
            if custom_names.get(0):
                skill_name = _display_alias(skill_name, custom_names[0])
            enchant_ids = load_json(weapon["enchant_effects"], [])
            if not isinstance(enchant_ids, list):
                enchant_ids = []
            base_enchants = self._enchant_names(enchant_ids)
            enchant_names = [
                _display_alias(base_name, custom_names.get(slot_no, ""))
                for slot_no, base_name in enumerate(base_enchants, start=1)
            ]
            return PlayerContext(
                **base_context,
                weapon_name=str(weapon_name),
                weapon_type=str(weapon["weapon_type"]),
                weapon_quality=str(weapon["quality"]),
                weapon_source=str(weapon["drop_location"]),
                weapon_level=int(weapon["level"]),
                weapon_max_level=int(weapon["max_level"]),
                weapon_attack=computed_weapon_attack(weapon),
                skill_name=skill_name,
                skill_desc=str(weapon["effect_desc"] or ""),
                interval=_safe_int(weapon["interval"]),
                power=_safe_float(weapon["power"]),
                cost_mp=_safe_int(weapon["cost_mp"]),
                enchant_names=tuple(enchant_names),
            )
        return PlayerContext(**base_context)

    def _search(self, query: str) -> list[tuple[KnowledgeEntry, int]]:
        """按关键词做轻量检索。"""

        terms = _query_terms(query)
        intent = _query_intent(query)
        scored: list[tuple[KnowledgeEntry, int]] = []
        for entry in self._entries:
            haystack = _normalize(f"{entry.title} {entry.group} {entry.kind} {entry.body} {' '.join(entry.keywords)}")
            score = 0
            title_text = _normalize(entry.title)
            if title_text and title_text in _normalize(query):
                score += 80
            if _core_query(query) and _core_query(query) in title_text:
                score += 40
            for term in terms:
                if not term:
                    continue
                if term in title_text:
                    score += 12
                if term in _normalize(entry.group):
                    score += 5
                if term in _normalize(entry.kind):
                    score += 4
                if term in haystack:
                    score += max(1, min(8, len(term)))
            if entry.kind != "文档":
                score += 8
            if intent == "source" and any(word in haystack for word in ("掉落", "产出", "奖励", "首领", "概率")):
                score += 18
            if intent == "usage" and any(word in haystack for word in ("效果", "提高", "恢复", "技能", "流派", "用途")):
                score += 10
            if intent == "build" and any(word in haystack for word in ("流派", "武器", "技能", "附魔", "蓄势")):
                score += 12
            if score:
                scored.append((entry, score))
        scored.sort(key=lambda item: (item[1], _priority(item[0])), reverse=True)
        return scored[:24]

    def _search_with_player_names(self, client_id: str, query: str) -> list[tuple[KnowledgeEntry, int]]:
        """检索全局知识，并把玩家自己的铭刻名也纳入匹配。"""

        personal = self._player_named_matches(client_id, query)
        if not personal:
            return self._search(query)

        merged = [*personal, *self._search(query)]
        seen: set[tuple[str, str, str]] = set()
        result: list[tuple[KnowledgeEntry, int]] = []
        for entry, score in merged:
            key = (_normalize(entry.title), entry.group, entry.kind)
            if key in seen:
                continue
            seen.add(key)
            result.append((entry, score))
        result.sort(key=lambda item: (item[1], _priority(item[0])), reverse=True)
        return result[:24]

    def _player_named_matches(self, client_id: str, query: str) -> list[tuple[KnowledgeEntry, int]]:
        """让玩家自己的铭刻武器名、技能名、附魔名也能被百科识别。"""

        normalized_query = _normalize(query)
        if not normalized_query:
            return []

        rows = self.db.fetch_all(
            """
            SELECT pw.*, wd.name AS base_name, wd.drop_location, wd.base_attack, wd.skill_id, wd.weapon_type,
                   ws.name AS skill_name, ws.effect_desc, ws.interval, ws.power, ws.cost_mp
            FROM player_weapons AS pw
            JOIN weapon_defs AS wd ON wd.weapon_def_id = pw.weapon_def_id
            LEFT JOIN weapon_skill_defs AS ws ON ws.skill_id = wd.skill_id
            WHERE pw.holder_id = ?
            """,
            (client_id,),
        )
        matches: list[tuple[KnowledgeEntry, int]] = []
        for row in rows:
            custom_name = str(row.get("custom_name") or "").strip()
            base_name = str(row.get("base_name") or "")
            if _name_hits(normalized_query, custom_name, base_name):
                title = f"{custom_name}（{base_name}）" if custom_name else base_name
                body = _join_parts(
                    f"武器类型：{row.get('weapon_type', '')}",
                    f"当前攻击：{computed_weapon_attack(row)}",
                    f"模板基础攻击：{row.get('base_attack', 0)}",
                    f"掉落范围：{row.get('drop_location') or '全地图随机'}",
                    f"自带技能：{row.get('skill_name', '无')}",
                    f"技能节奏：蓄势基准 {row.get('interval', '')}，倍率 {row.get('power', '')}，精神消耗 {row.get('cost_mp', '')}",
                    str(row.get("effect_desc", "")),
                )
                matches.append(
                    (
                        KnowledgeEntry(
                            title=title,
                            group="武器",
                            kind=str(row.get("weapon_type") or "武器"),
                            body=body,
                            keywords=_keywords(custom_name, base_name, row.get("weapon_def_id"), row.get("skill_name")),
                        ),
                        140,
                    )
                )

            weapon_id = int(row["weapon_id"])
            custom_rows = self.db.fetch_all(
                "SELECT slot_no, custom_name FROM weapon_enchant_names WHERE weapon_id = ?",
                (weapon_id,),
            )
            custom_names = {int(item["slot_no"]): str(item["custom_name"]) for item in custom_rows if item["custom_name"]}
            skill_custom = custom_names.get(0, "")
            if _name_hits(normalized_query, skill_custom):
                title = f"{skill_custom}（{row.get('skill_name', '普通攻击')}）"
                body = _join_parts(
                    f"精神消耗：{row.get('cost_mp', 0)}",
                    f"蓄势基准：{row.get('interval', 0)}",
                    f"技能倍率：{row.get('power', 1)}",
                    str(row.get("effect_desc", "")),
                )
                matches.append(
                    (
                        KnowledgeEntry(
                            title=title,
                            group="武器",
                            kind="自带技能",
                            body=body,
                            keywords=_keywords(skill_custom, row.get("skill_name"), row.get("skill_id")),
                        ),
                        135,
                    )
                )

            enchant_ids = load_json(row.get("enchant_effects"), [])
            if not isinstance(enchant_ids, list):
                enchant_ids = []
            for slot_no, enchant_id in enumerate(enchant_ids, start=1):
                custom = custom_names.get(slot_no, "")
                if not _name_hits(normalized_query, custom):
                    continue
                enchant = self.db.fetch_one("SELECT * FROM weapon_enchants WHERE enchant_id = ?", (str(enchant_id),))
                if not enchant:
                    continue
                effect = _effect_text(enchant["effect"])
                title = f"{custom}（{enchant['name']}）"
                body = _join_parts(
                    f"精神消耗修正：{enchant['mp_delta']}",
                    f"效果：{effect}" if effect else "",
                    _enchant_style(str(enchant["name"])),
                )
                matches.append(
                    (
                        KnowledgeEntry(
                            title=title,
                            group="武器",
                            kind="技能书附魔",
                            body=body,
                            keywords=_keywords(custom, enchant["name"], enchant_id, effect),
                        ),
                        135,
                    )
                )
        return matches

    def _focused_matches(self, query: str, matches: list[tuple[KnowledgeEntry, int]]) -> list[tuple[KnowledgeEntry, int]]:
        """把搜索结果收束成真正用于回答的资料。"""

        if not matches:
            return []

        normalized_query = _normalize(query)
        core = _core_query(query)
        intent = _query_intent(query)
        exact_structured = [
            item for item in matches
            if item[0].kind != "文档" and _normalize(item[0].title) and _normalize(item[0].title) in normalized_query
        ]
        if exact_structured:
            exact_structured.sort(key=lambda item: _answer_entry_rank(item[0], intent), reverse=True)
            if intent in {"source", "usage", "lookup"}:
                return exact_structured[:1]
            related = [
                item for item in matches
                if item not in exact_structured
                and item[0].kind != "文档"
                and _related_to(exact_structured[0][0], item[0])
            ]
            return (exact_structured + related)[:4]

        if core:
            core_structured = [
                item for item in matches
                if item[0].kind != "文档" and core in _normalize(f"{item[0].title} {' '.join(item[0].keywords)}")
            ]
            if core_structured:
                if "铭刻之羽" in query and intent == "source":
                    core_structured.sort(key=lambda item: _reward_rank(item[0]), reverse=True)
                return core_structured[:4]

        if _should_use_document_synthesis(query, matches):
            doc_matches = [item for item in matches if item[0].kind == "文档"]
            structured_matches = [item for item in matches if item[0].kind != "文档"]
            return (doc_matches[:8] + structured_matches[:4])[:10]

        structured = [item for item in matches if item[0].kind != "文档"]
        if structured and intent in {"usage", "source", "build"}:
            return structured[:4]
        return matches[:8]

    def _load_markdown_entries(self) -> list[KnowledgeEntry]:
        """读取 Markdown 文档章节，作为说明类知识。"""

        entries: list[KnowledgeEntry] = []
        for path in _markdown_paths():
            content = path.read_text(encoding="utf-8")
            group = _doc_group(path)
            source = path.relative_to(XIUXIAN_DIR).as_posix()
            for title, body in _markdown_sections(content, path.stem):
                if not body.strip():
                    continue
                heading, _, summary = body.partition("\n")
                body_text = summary.strip() or body.strip()
                entries.append(
                    KnowledgeEntry(
                        title=heading.strip() or title,
                        group=group,
                        kind="文档",
                        body=body_text,
                        keywords=_keywords(title, group, source, body_text),
                        source=source,
                    )
                )
        return entries

    def _load_database_entries(self) -> list[KnowledgeEntry]:
        """读取 xiuxian.db 中的结构化知识。"""

        entries: list[KnowledgeEntry] = []
        entries.extend(self._item_entries())
        entries.extend(self._weapon_entries())
        entries.extend(self._skill_entries())
        entries.extend(self._enchant_entries())
        entries.extend(self._trade_location_entries())
        entries.extend(self._exploration_entries())
        entries.extend(self._monster_entries())
        entries.extend(self._buyer_entries())
        entries.extend(self._recycle_entries())
        entries.extend(self._seasonal_reward_entries())
        return entries

    def _item_entries(self) -> list[KnowledgeEntry]:
        """物品、消耗品、宝石和技能书资料。"""

        entries: list[KnowledgeEntry] = []
        for table, id_key in (("item_defs", "item_id"), ("ring_item_defs", "ring_item_id")):
            for row in self.db.fetch_all(f"SELECT * FROM {table}"):
                effect = _effect_text(row.get("effect"))
                body = _join_parts(
                    f"分类：{row.get('category', '')}",
                    f"品质：{row.get('quality', '')}",
                    f"基础价格：{row.get('base_price', 0)}" if "base_price" in row else "",
                    f"重量：{row.get('weight', 0)}" if "weight" in row else "",
                    f"目标：{row.get('target_type', '')}" if "target_type" in row else "",
                    f"效果：{effect}" if effect else "",
                    str(row.get("desc", "")),
                )
                entries.append(
                    KnowledgeEntry(
                        title=str(row.get("name") or row.get(id_key)),
                        group="修仙物品",
                        kind=str(row.get("category") or "物品"),
                        body=body,
                        keywords=_keywords(row.get("name"), row.get(id_key), row.get("category"), effect),
                    )
                )
        return entries

    def _weapon_entries(self) -> list[KnowledgeEntry]:
        """武器资料。"""

        rows = self.db.fetch_all(
            """
            SELECT w.weapon_def_id, w.name, w.drop_location, w.base_attack, w.weapon_type,
                   s.name AS skill_name, s.effect_desc, s.interval, s.power, s.cost_mp
            FROM weapon_defs AS w
            LEFT JOIN weapon_skill_defs AS s ON s.skill_id = w.skill_id
            ORDER BY w.weapon_type, w.base_attack, w.name
            """
        )
        entries: list[KnowledgeEntry] = []
        for row in rows:
            body = _join_parts(
                f"武器类型：{row.get('weapon_type', '')}",
                f"基础攻击：{row.get('base_attack', 0)}",
                f"掉落范围：{row.get('drop_location') or '全地图随机'}",
                f"自带技能：{row.get('skill_name', '无')}",
                f"技能节奏：蓄势基准 {row.get('interval', '')}，倍率 {row.get('power', '')}，精神消耗 {row.get('cost_mp', '')}",
                str(row.get("effect_desc", "")),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name") or row.get("weapon_def_id")),
                    group="武器",
                    kind=str(row.get("weapon_type") or "武器"),
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("weapon_def_id"), row.get("weapon_type"), row.get("skill_name")),
                )
            )
        return entries

    def _skill_entries(self) -> list[KnowledgeEntry]:
        """武器自带技能资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM weapon_skill_defs ORDER BY interval, name"):
            body = _join_parts(
                f"精神消耗：{row.get('cost_mp', 0)}",
                f"蓄势基准：{row.get('interval', 0)}",
                f"技能倍率：{row.get('power', 1)}",
                str(row.get("effect_desc", "")),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name") or row.get("skill_id")),
                    group="武器",
                    kind="自带技能",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("skill_id"), row.get("effect_desc")),
                )
            )
        return entries

    def _enchant_entries(self) -> list[KnowledgeEntry]:
        """技能书附魔资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM weapon_enchants ORDER BY name"):
            effect = _effect_text(row.get("effect"))
            body = _join_parts(
                f"精神消耗修正：{row.get('mp_delta', 0)}",
                f"效果：{effect}" if effect else "",
                _enchant_style(str(row.get("name", ""))),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name") or row.get("enchant_id")),
                    group="武器",
                    kind="技能书附魔",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("enchant_id"), effect, _enchant_style(str(row.get("name", "")))),
                )
            )
        return entries

    def _trade_location_entries(self) -> list[KnowledgeEntry]:
        """跑商地点资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM trade_locations ORDER BY name"):
            body = _join_parts(
                f"坐标：({row.get('x', 0)},{row.get('y', 0)})",
                f"特产：{row.get('specialties', '')}",
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name")),
                    group="贸易服务",
                    kind="跑商地点",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("specialties"), "商场", "跑商", "导航"),
                )
            )
        return entries

    def _exploration_entries(self) -> list[KnowledgeEntry]:
        """探险地点资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM exploration_locations ORDER BY recommended_level, name"):
            body = _join_parts(
                f"坐标：({row.get('x', 0)},{row.get('y', 0)})",
                f"推荐等级：{row.get('recommended_level', 0)}",
                f"怪物等级：{row.get('min_level', 0)}-{row.get('max_level', 0)}",
                str(row.get("desc", "")),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name")),
                    group="探险",
                    kind="探险地点",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("desc"), "探险", "地图"),
                )
            )
        return entries

    def _monster_entries(self) -> list[KnowledgeEntry]:
        """怪物和掉落资料。"""

        rows = self.db.fetch_all(
            """
            SELECT m.*, COALESCE(i.name, e.name, m.drop_item_id) AS drop_name
            FROM monster_defs AS m
            LEFT JOIN item_defs AS i ON i.item_id = m.drop_item_id
            LEFT JOIN ring_item_defs AS e ON e.ring_item_id = m.drop_item_id
            ORDER BY m.level, m.name
            """
        )
        entries: list[KnowledgeEntry] = []
        for row in rows:
            body = _join_parts(
                f"等级：{row.get('level', 0)}",
                f"类型：{row.get('kind', '')}",
                f"血气/攻击/防御：{row.get('hp', 0)} / {row.get('attack', 0)} / {row.get('defense', 0)}",
                f"偏向掉落：{row.get('drop_name') or '无'}，概率 {row.get('drop_chance', 0)}",
                "实际探险会再按族群自然池滚动战利品，怪物原掉落只是偏向，不是唯一结果。",
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name") or row.get("monster_id")),
                    group="探险",
                    kind="怪物",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("monster_id"), row.get("kind"), row.get("drop_name")),
                )
            )
        return entries

    def _buyer_entries(self) -> list[KnowledgeEntry]:
        """特殊收购资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM special_buyers ORDER BY buyer_name"):
            names = self._item_names(str(row.get("item_ids", "")))
            body = _join_parts(
                f"坐标：({row.get('x', 0)},{row.get('y', 0)})",
                f"收购倍率：{row.get('price_factor', 1)}",
                f"收购物品：{'、'.join(names) if names else row.get('item_ids', '')}",
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("buyer_name")),
                    group="贸易服务",
                    kind="特殊收购",
                    body=body,
                    keywords=_keywords(row.get("buyer_name"), *names, "商场", "出售", "自动出售"),
                )
            )
        return entries

    def _recycle_entries(self) -> list[KnowledgeEntry]:
        """回收地点资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM recycle_locations ORDER BY recycle_type, name"):
            body = _join_parts(
                f"回收类型：{row.get('recycle_type', '')}",
                f"坐标：({row.get('x', 0)},{row.get('y', 0)})",
                f"价格倍率：{row.get('price_factor', 1)}",
                str(row.get("desc", "")),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("name")),
                    group="贸易服务",
                    kind="回收地点",
                    body=body,
                    keywords=_keywords(row.get("name"), row.get("recycle_type"), row.get("desc"), "商场", "回收"),
                )
            )
        return entries

    def _seasonal_reward_entries(self) -> list[KnowledgeEntry]:
        """首领奖励概率资料。"""

        entries: list[KnowledgeEntry] = []
        for row in self.db.fetch_all("SELECT * FROM seasonal_boss_reward_rates ORDER BY weight_type"):
            body = _join_parts(
                f"铭刻之羽基础独立概率：{_percent(row.get('feather_chance'))}",
                f"开孔器/洗髓液：{_percent(row.get('material_chance'))}",
                f"宝石：{_percent(row.get('gem_chance'))}",
                f"技能书：{_percent(row.get('book_chance'))}",
                f"武器：{_percent(row.get('weapon_chance'))}",
                "铭刻之羽每次珍贵抽取先独立判定，同一次首领奖励最多 1 枚。",
                str(row.get("desc", "")),
            )
            entries.append(
                KnowledgeEntry(
                    title=str(row.get("weight_type")),
                    group="首领",
                    kind="奖励概率",
                    body=body,
                    keywords=_keywords(row.get("weight_type"), row.get("desc"), "首领", "铭刻之羽", "开孔器", "洗髓液"),
                )
            )
        return entries

    def _item_names(self, item_ids: str) -> list[str]:
        """把逗号分隔 item_id 转成物品名。"""

        result: list[str] = []
        for item_id in [item.strip() for item in item_ids.split(",") if item.strip()]:
            row = self.db.fetch_one(
                """
                SELECT name FROM item_defs WHERE item_id = ?
                UNION ALL
                SELECT name FROM ring_item_defs WHERE ring_item_id = ?
                LIMIT 1
                """,
                (item_id, item_id),
            )
            result.append(str(row["name"] if row else item_id))
        return result

    def _enchant_names(self, enchant_ids: list[Any]) -> list[str]:
        """读取附魔名。"""

        result: list[str] = []
        for enchant_id in enchant_ids:
            row = self.db.fetch_one("SELECT name FROM weapon_enchants WHERE enchant_id = ?", (str(enchant_id),))
            result.append(str(row["name"] if row else enchant_id))
        return result


def _markdown_paths() -> list[Path]:
    """收集修仙组件内 Markdown 文件。"""

    return sorted(XIUXIAN_DIR.rglob("*.md"), key=lambda item: item.relative_to(XIUXIAN_DIR).as_posix())


def _doc_group(path: Path) -> str:
    """根据文档路径推断分组。"""

    relative = path.relative_to(XIUXIAN_DIR)
    return "设定文档" if len(relative.parts) == 1 else relative.parts[0]


def _markdown_sections(content: str, fallback_title: str) -> list[tuple[str, str]]:
    """把 Markdown 切成可检索章节。"""

    sections: list[tuple[str, tuple[str, ...], list[str]]] = []
    heading_stack: list[str] = [fallback_title]
    current_title = fallback_title
    current_lines: list[str] = []
    for line in content.splitlines():
        heading = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if heading:
            if current_lines:
                sections.append((current_title, tuple(heading_stack), current_lines))
            current_title = heading.group(2).strip()
            level = len(heading.group(1))
            heading_stack = heading_stack[:level]
            heading_stack.append(current_title)
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_title, tuple(heading_stack), current_lines))

    result: list[tuple[str, str]] = []
    for title, heading_path, lines in sections:
        body = "\n".join(line.strip() for line in lines if line.strip())
        body = re.sub(r"```.*?```", "", body, flags=re.S)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            display_path = " > ".join(dict.fromkeys(part.strip() for part in heading_path if part.strip()))
            result.append((title, f"{display_path}\n{body[:1100]}"))
    return result


def _query_terms(query: str) -> list[str]:
    """生成检索词。"""

    normalized = _normalize(query)
    tokens = [item for item in re.split(r"[\s,，。？?、/|]+", normalized) if item]
    terms = [normalized, *tokens]
    if len(normalized) > 2:
        terms.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
    return list(dict.fromkeys(term for term in terms if term))


def _normalize(value: object) -> str:
    """检索用标准化。"""

    return re.sub(r"\s+", "", str(value).lower())


def _name_hits(normalized_query: str, *names: object) -> bool:
    """判断玩家输入是否命中某个名称或铭刻名。"""

    for name in names:
        value = _normalize(name)
        if value and value in normalized_query:
            return True
    return False


def _display_alias(base_name: object, custom_name: object) -> str:
    """按 铭刻名（原名） 显示。"""

    base = str(base_name).strip()
    custom = str(custom_name).strip()
    return f"{custom}（{base}）" if custom else base


def _keywords(*values: object) -> tuple[str, ...]:
    """生成关键词。"""

    result: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            result.append(text)
    return tuple(dict.fromkeys(result))


def _join_parts(*parts: object) -> str:
    """拼接非空资料段。"""

    return "；".join(str(part).strip() for part in parts if str(part).strip())


def _effect_text(raw: object) -> str:
    """把 JSON 效果转成短文本。"""

    if raw is None:
        return ""
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw).strip()
        if not text:
            return ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
    if not isinstance(data, dict) or not data:
        return ""
    parts: list[str] = []
    for key, value in data.items():
        if isinstance(value, float):
            parts.append(f"{key}={value:g}")
        else:
            parts.append(f"{key}={value}")
    return "，".join(parts)


def _human_effect_text(effect: str) -> str:
    """把效果字段翻译成人话。"""

    if not effect:
        return ""

    labels = {
        "explore_bonus": "探险效率",
        "trade_bonus": "跑商收益",
        "recover_bonus": "恢复效果",
        "dodge_bonus": "闪避",
        "hit_bonus": "命中",
        "combo_bonus": "连击倾向",
        "crit_resist_bonus": "抗暴和承伤稳定",
        "max_hp_bonus": "血气上限",
        "max_mp_bonus": "精神上限",
        "mp_bonus": "精神上限",
        "defense_bonus": "防御",
        "hp_ratio": "恢复血气",
        "mp_ratio": "恢复精神",
        "skill_power_bonus": "技能威力",
        "single_hit_bonus": "单段伤害",
        "interval_delta": "蓄势节奏",
        "burn_rate": "灼烧",
        "bleed_rate": "流血",
        "mp_suppress": "精神压制",
        "stun_rate": "行动扰乱",
        "defense_suppress": "防御压制",
        "pierce_bonus": "防御穿透",
        "heavy_bonus": "重击威力",
        "combo_damage_bonus": "连击伤害",
        "shield_bonus": "护身",
        "life_steal": "吸血",
        "damage_reduce": "减伤",
        "counter_rate": "反击",
        "enchant_id": "对应附魔",
        "wash_physique": "洗髓体质",
    }

    result: list[str] = []
    for part in re.split(r"[，,]", effect):
        if "=" not in part:
            continue
        key, raw_value = [item.strip() for item in part.split("=", 1)]
        label = labels.get(key, key)
        result.append(_format_effect_value(label, raw_value, key))
    return "，".join(result)


def _format_effect_value(label: str, raw_value: str, key: str) -> str:
    """格式化单个效果值。"""

    try:
        value = float(raw_value)
    except ValueError:
        return f"{label} {raw_value}"

    if key in {"hp_ratio", "mp_ratio", "explore_bonus", "trade_bonus", "recover_bonus", "dodge_bonus", "hit_bonus", "combo_bonus", "crit_resist_bonus", "skill_power_bonus", "single_hit_bonus", "burn_rate", "bleed_rate", "mp_suppress", "stun_rate", "defense_suppress", "pierce_bonus", "heavy_bonus", "combo_damage_bonus", "shield_bonus", "life_steal", "damage_reduce", "counter_rate"}:
        sign = "+" if value > 0 else ""
        return f"{label} {sign}{value * 100:.0f}%"
    if key == "interval_delta":
        if value < 0:
            return f"{label} 加快 {abs(int(value))} 档"
        if value > 0:
            return f"{label} 变慢 {int(value)} 档"
        return f"{label} 不变"
    sign = "+" if value > 0 else ""
    return f"{label} {sign}{int(value) if value.is_integer() else value:g}"


def _enchant_style(name: str) -> str:
    """根据技能书名给出流派提示。"""

    style_map = {
        "风刃": "高频连击流派",
        "沙影": "高频连击流派",
        "流光": "高频连击流派",
        "追星": "高频连击流派",
        "破甲": "重击破防流派",
        "崩山": "重击破防流派",
        "穿云": "重击破防流派",
        "镇岳": "重击破防流派",
        "灼心": "持续伤害流派",
        "血雨": "持续伤害流派",
        "毒云": "持续伤害流派",
        "残焰": "持续伤害流派",
        "断念": "压制控制流派",
        "镇魂": "压制控制流派",
        "天机": "压制控制流派",
        "梦雾": "压制控制流派",
        "回春": "生存续航流派",
        "玄盾": "生存续航流派",
        "血契": "生存续航流派",
        "灵木": "生存续航流派",
        "反震": "反击护身流派",
        "归刃": "反击护身流派",
        "借势": "反击护身流派",
        "玄曜": "反击护身流派",
        "无相": "斩杀收割流派",
        "断海": "斩杀收割流派",
        "绝影": "斩杀收割流派",
        "破军": "斩杀收割流派",
        "星落": "首领协作流派",
        "乾坤": "首领协作流派",
        "破阵": "首领协作流派",
        "玉京": "首领协作流派",
        "月蚀": "决斗扰乱流派",
        "镜湖": "决斗扰乱流派",
        "影叶": "决斗扰乱流派",
        "清心": "决斗扰乱流派",
    }
    for key, style in style_map.items():
        if key in name:
            return style
    return ""


def _percent(value: object) -> str:
    """格式化概率。"""

    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _slot_order(slot: object) -> int:
    """装备位排序。"""

    try:
        return EQUIPMENT_SLOTS.index(str(slot))
    except ValueError:
        return len(EQUIPMENT_SLOTS)


def _equipment_hole_label(row: dict[str, Any]) -> str:
    """装备孔位短展示。"""

    return f"{fixed_equipment_label(row)} {int(row.get('hole_count') or 0)}/{EQUIPMENT_MAX_HOLES}孔"


def _answer_buttons(query: str, entries: list[KnowledgeEntry]) -> str:
    """按回答内容给 2-4 个下一步按钮。"""

    commands: list[str] = ["帮助"]
    primary = entries[0] if entries else None
    if _is_broad_document_query(query) or (primary and primary.kind == "文档"):
        commands.extend(_document_answer_commands(query, entries))
        return T.buttons(*commands[:5])
    if "跑商" in query or "商场" in query:
        commands.extend(["商场推荐", "自动出售", "背包"])
    elif "开荒" in query or "探险" in query:
        commands.extend(["探险状态", "地图", "修仙信息"])
    elif "首领" in query or "虫洞" in query:
        commands.extend(["首领", "虫洞", "武器", "修仙信息"])
    elif primary and primary.group == "武器":
        commands.extend(["武器", "修仙信息", "修仙百科 武器流派"])
    elif primary and primary.group == "修仙物品":
        if primary.title == "开孔器":
            commands.extend(["孔位", "开孔 头部", "首领", "纳戒"])
        elif primary.title == "洗髓液":
            commands.extend(["洗髓", "纳戒", "首领", "虫洞"])
        elif primary.title == "淬锋丹":
            commands.extend(["武器淬锋", "宗门战", "纳戒", "武器"])
        elif primary.kind == "宝石":
            commands.extend(["宝石", "装备", "修仙百科 宝石"])
        elif primary.kind == "技能书":
            commands.extend(["武器", "纳戒", "修仙百科 技能书"])
        else:
            commands.extend(["背包", "纳戒", "查看修仙物品"])
    else:
        commands.extend(["修仙百科 武器", "修仙百科 宝石", "修仙百科 跑商"])
    return T.buttons(*commands[:5])


def _document_answer_commands(query: str, entries: list[KnowledgeEntry]) -> list[str]:
    """综合文档回答的下一步入口。"""

    commands: list[str] = []
    text = f"{query} {' '.join(entry.group for entry in entries)} {' '.join(entry.title for entry in entries)}"
    if any(word in text for word in ("按钮", "富文本", "帮助", "指南")):
        commands.extend(["指南", "修仙帮助"])
    if any(word in text for word in ("宗门", "宗门战")):
        commands.extend(["指南 世界", "宗门", "宗门战"])
    if any(word in text for word in ("商场", "跑商", "出售", "交易")):
        commands.extend(["指南 交易", "商场推荐", "自动出售"])
    if any(word in text for word in ("探险", "地图", "秘境")):
        commands.extend(["指南 战斗", "地图", "探险列表"])
    if any(word in text for word in ("武器", "装备", "宝石", "纳戒", "背包")):
        commands.extend(["指南 行囊", "武器", "纳戒"])
    if any(word in text for word in ("玩家", "状态", "休息", "源库", "成长")):
        commands.extend(["指南 成长", "状态", "源库"])
    commands.extend(["指南", "修仙帮助"])
    return list(dict.fromkeys(commands))


def _smart_answer_lines(
    query: str,
    entries: list[KnowledgeEntry],
    context: PlayerContext | None = None,
    item_helper: Any = None,
) -> list[str]:
    """把命中资料合成答案，而不是展示搜索结果。"""

    if not entries:
        return ["结论：没有找到足够资料。"]

    primary = entries[0]
    intent = _query_intent(query)
    document_answer = _document_synthesis_lines(query, entries)
    if document_answer and (primary.kind == "文档" or _is_broad_document_query(query)):
        return document_answer

    clarify = _personal_clarify_lines(query, primary, context)
    if clarify:
        return clarify

    personal_entry = _personal_weapon_entry(query, primary, context)
    if personal_entry:
        return _weapon_answer_lines(query, personal_entry, [personal_entry], "build", context)

    topic = _topic_answer_lines(query, entries)
    if topic and _should_answer_as_topic(query, primary):
        return topic

    if "铭刻之羽" in query and intent == "source":
        return [
            "结论：铭刻之羽只从岁时情劫首领奖励产出，不是普通探险、虫洞、商场或回收产物。",
            "基础独立概率从高到低大致是：高权重传统节日 10%，普通传统节日 7%，节气 5%，普通日 2.5%。",
            "贡献、排名和宗门影响力只做小幅修正；同一次首领奖励最多获得 1 枚。",
        ]

    if intent == "source":
        return _source_answer_lines(primary)

    if primary.group == "修仙物品" and item_helper:
        helped_lines = item_helper(primary, intent)
        if helped_lines:
            return helped_lines

    if primary.group == "修仙物品":
        return _item_answer_lines(primary, intent)

    if primary.group == "武器" and primary.kind not in {"自带技能", "技能书附魔"}:
        return _weapon_answer_lines(query, primary, entries, intent, context)

    if primary.kind == "技能书附魔":
        return _enchant_answer_lines(query, primary, context)

    if primary.kind == "自带技能":
        return _skill_answer_lines(primary)

    if topic:
        return topic

    if document_answer:
        return document_answer

    return [
        _answer_conclusion(query, entries),
        "如果你想要更具体的建议，可以直接问物品名、武器名、技能书名，或者问“怎么配”。",
    ]


def _source_answer_lines(entry: KnowledgeEntry) -> list[str]:
    """来源类回答。"""

    source = _extract_field(entry.body, "掉落范围") or _extract_field(entry.body, "掉落") or _extract_field(entry.body, "坐标")
    if not source:
        return [f"结论：{entry.title} 的来源没有单独字段，通常要看对应玩法入口或奖励表。"]

    if entry.group == "武器":
        return [
            f"结论：{entry.title} 在 {source} 掉落。",
            "这是武器模板来源，不受玩家等级绑定；能不能拿到主要看掉落随机和武器上限随机。",
            f"想刷它就围绕 {source} 相关探险走，刷到后再看品质、等级上限和自带技能是否值得培养。",
        ]

    return [
        f"结论：{entry.title} 的来源是 {source}。",
        "如果它是奖励类物品，就优先看对应玩法的奖励入口；如果是地点类物品，就去对应地点处理。",
    ]


def _item_answer_lines(entry: KnowledgeEntry, intent: str) -> list[str]:
    """物品用途类回答。"""

    category = entry.kind
    quality = _extract_field(entry.body, "品质")
    effect = _extract_field(entry.body, "效果")
    desc = _last_plain_part(entry.body).rstrip("。")
    effect_text = _human_effect_text(effect)

    if category == "宝石":
        lines = [
            f"结论：{entry.title} 是{quality or ''}宝石，核心用途是{desc or '给装备提供额外属性'}。",
            f"实际收益：{effect_text or effect or '看宝石效果字段'}。",
        ]
        if "探险" in desc or "explore_bonus" in effect:
            lines.append("它偏长期收益，不是战斗爆发宝石；想多刷探险、掉落和路线效率时值得留。")
        elif "精神" in desc or "mp" in effect:
            lines.append("它偏续航和技能释放，适合精神吃紧、技能消耗高的武器。")
        elif "防御" in desc or "defense" in effect:
            lines.append("它偏承伤，适合打首领、虫洞或越级探险时补稳定性。")
        return lines

    if category == "恢复类":
        return [
            f"结论：{entry.title} 是恢复类物品，主要用于补状态，不该和普通材料混着看。",
            f"效果：{effect_text or desc or effect}。",
            "它一般通过“使用 物品名 数量”消耗，也会被探险自动用药逻辑按开关使用。",
        ]

    if category == "技能书":
        style = _enchant_style(entry.title) or "特化附魔"
        return [
            f"结论：{entry.title} 是技能书，用来附魔武器，不是直接吃的消耗品。",
            f"定位：{style}；{desc or '它会改变武器战斗倾向'}。",
            "同一把武器不能重复附魔同一本技能书；附魔后不可撤销，所以要按武器流派来配。",
        ]

    if category == "消耗品":
        return [
            f"结论：{entry.title} 是消耗品，作用是{desc or effect_text or effect or '触发对应成长效果'}。",
            "下一步：先查它的专属入口；如果没有明确入口，就发“查看修仙物品 物品名”确认来源和归属，不要直接按普通恢复药处理。",
        ]

    return [
        f"结论：{entry.title} 属于{category}。",
        f"用途：{desc or effect_text or effect or '看对应玩法说明'}。",
        "如果它是怪物战利品，主要价值通常在特殊收购或回收体系里。",
    ]


def _weapon_answer_lines(
    query: str,
    entry: KnowledgeEntry,
    entries: list[KnowledgeEntry],
    intent: str,
    context: PlayerContext | None = None,
) -> list[str]:
    """武器玩法类回答。"""

    profile = _weapon_profile(entry, entries)
    goal = _goal_from_query(query)
    role = profile.style or _role_from_traits(profile.traits)

    lines = [
        f"结论：{profile.title} 是{profile.weapon_type}，核心不是基础攻击，而是「{profile.skill}」带来的{role or '专属节奏'}。",
        _weapon_goal_summary(profile, goal),
        f"数值判断：{_tempo_text(profile.interval)}；{_power_text(profile.power)}；{_cost_text(profile.cost_mp)}；来源 {profile.source or '全地图随机'}。",
    ]
    strengths = _weapon_strengths(profile)
    if strengths:
        lines.append(f"强项：{'；'.join(strengths)}。")
    weakness = _weapon_weakness(profile)
    if weakness:
        lines.append(f"短板：{weakness}")
    lines.append(f"场景评分：{_score_text(_weapon_scores(profile))}")
    lines.extend(_weapon_goal_advice(profile, goal))
    lines.extend(_weapon_plan_lines(profile, goal))
    lines.extend(_context_weapon_lines(context, profile, goal))
    if intent == "build" and goal != "general":
        lines.append("取舍：同一把武器不能重复附魔同一本技能书，优先补当前目标最缺的一项，不要把栏位塞成四不像。")
    return [line for line in lines if line]


def _enchant_answer_lines(query: str, entry: KnowledgeEntry, context: PlayerContext | None = None) -> list[str]:
    """技能书回答。"""

    style = _enchant_style(entry.title) or "特化附魔"
    effect = _extract_field(entry.body, "效果")
    mp_delta = _extract_field(entry.body, "精神消耗修正")
    effects = _parse_effect_values(effect)
    goal = _goal_from_query(query)
    scene = _enchant_best_scene(style, effects, goal)
    suffix = "，不是万能增伤件" if "不是首选" not in scene else ""
    return [
        f"结论：{entry.title} 是{style}技能书，适合{scene}{suffix}。",
        f"效果拆解：{_human_effect_text(effect) or effect}；精神消耗修正 {mp_delta or '0'}。",
        f"场景评分：{_score_text(_enchant_scores(style, effects))}",
        f"适合：{_enchant_fit_text(style, effects, goal)}",
        f"不适合：{_enchant_bad_text(effects)}",
        _enchant_plan_text(style, effects, goal),
        *_context_enchant_lines(context, entry.title, style, effects, goal),
        "附魔提醒：同一把武器不能重复附魔同一本技能书，且附魔不可撤销；先看武器自带技能，再决定是否占栏位。",
    ]


def _skill_answer_lines(entry: KnowledgeEntry) -> list[str]:
    """自带技能回答。"""

    cost = _extract_field(entry.body, "精神消耗")
    interval = _extract_field(entry.body, "蓄势基准")
    power = _extract_field(entry.body, "技能倍率")
    desc = _last_plain_part(entry.body).rstrip("。")
    return [
        f"结论：{entry.title} 是武器自带技能，真正影响打法的是蓄势、倍率和附带效果。",
        f"参数：精神消耗 {cost}，蓄势基准 {interval}，技能倍率 {power}。",
        f"理解：{desc or '蓄势越小越快，倍率越高越偏爆发'}。",
    ]


def _topic_answer_lines(query: str, entries: list[KnowledgeEntry]) -> list[str]:
    """玩法主题回答。"""

    if "跑商" in query or "商场" in query:
        return [
            "结论：跑商是资金玩法，核心不是“随便买随便卖”，而是当前位置能买、背包装得下、卖出后扣掉成本和手续费仍有净利润。",
            "优先用“商场推荐”，它只推荐当前能购买的跑商商品；背包战利品和世界物资走“自动出售”，别和普通跑商净利润混在一起。",
            "赚不到钱时先查三件事：当前位置是否有货、背包容量和负重是否够、出售地点是否真的有价差。",
        ]
    if "首领" in query or "岁时" in query:
        return [
            "结论：首领是珍贵物品主产出玩法，铭刻之羽、开孔器、洗髓液都应重点看首领奖励。",
            "普通日也有首领，但珍贵物品概率较低；节气、传统节日，尤其高权重节日更值得组织挑战。",
            "如果目标是铭刻或装备成长，优先盯首领，而不是普通探险。",
        ]
    if "宝石" in query:
        return [
            "结论：宝石是装备孔位的长期成长线，强在可叠加到全身装备，但每颗宝石应该服务一个明确方向。",
            "探险向看轻身水晶，跑商向看聚财紫晶，生存看护心玉、玄龟石、抗暴符文，精神看明心佛珠、清心玛瑙。",
            "不要只问等级高不高，要看当前缺的是输出、生存、精神、探险收益还是跑商收益。",
        ]
    if any(word in query for word in ("开荒", "前期", "新手", "初期")):
        return [
            "结论：前期开荒优先选快节奏、低精神消耗、有续航或命中稳定的武器，不要一上来迷信最高攻击。",
            "推荐方向：短剑、飞刃、匕这类高频武器刷图最舒服；万药藤杖这类续航武器适合稳过连续战斗；重斧、重戟更适合后面打硬目标。",
            "附魔思路：先补风刃书、流光书、追星书提高节奏，血气吃紧再补回春书或玄盾书；早期不建议把栏位全塞慢速爆发书。",
        ]
    if "武器" in query or "流派" in query:
        return [
            "结论：武器不该只看攻击力。真正决定玩法的是武器类型、速度负重、自带技能、蓄势基准、技能书附魔组合。",
            "快节奏武器适合连击和命中，重武器适合破防和爆发，杖类更适合精神压制、续航或控制。",
            "附魔时按流派堆：高频连击、重击破防、持续伤害、压制控制、生存续航、反击护身、斩杀收割、首领协作、决斗扰乱。",
        ]
    return []


def _document_synthesis_lines(query: str, entries: list[KnowledgeEntry]) -> list[str]:
    """把多个 Markdown 章节综合成百科式回答。"""

    documents = _dedupe_entries([entry for entry in entries if entry.kind == "文档"])
    if not documents:
        return []

    relevant = _rank_document_sentences(query, documents)
    if not relevant:
        return []

    lines = [f"结论：这个问题要综合 {len(documents)} 个文档章节看，核心是{_document_topic(query, documents)}。"]
    for label, sentence, source in relevant[:4]:
        prefix = f"{label}：" if label else ""
        lines.append(f"{prefix}{sentence}")

    sources = _document_sources(documents)
    if sources:
        lines.append(f"参考：{'；'.join(sources[:4])}。")
    return lines


def _dedupe_entries(entries: list[KnowledgeEntry]) -> list[KnowledgeEntry]:
    """按来源和标题去重。"""

    result: list[KnowledgeEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        key = (entry.source, entry.group, entry.title)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _rank_document_sentences(query: str, documents: list[KnowledgeEntry]) -> list[tuple[str, str, str]]:
    """从文档章节中抽取最有用的句子。"""

    terms = _query_terms(query)
    scored: list[tuple[int, str, str, str]] = []
    for entry in documents:
        source = _document_source_text(entry)
        for sentence in _document_sentences(entry.body):
            normalized_sentence = _normalize(sentence)
            if not normalized_sentence:
                continue
            score = 0
            for term in terms:
                if term and term in normalized_sentence:
                    score += max(2, min(10, len(term)))
            if _normalize(entry.title) and _normalize(entry.title) in _normalize(query):
                score += 10
            if _normalize(entry.group) and _normalize(entry.group) in _normalize(query):
                score += 6
            if any(word in sentence for word in ("必须", "不能", "只", "统一", "当前", "核心", "上限", "入口")):
                score += 3
            if any(word in query for word in ("怎么", "如何", "流程", "玩法", "规则")) and any(word in sentence for word in ("发送", "入口", "流程", "规则", "命令")):
                score += 5
            if "按钮" in query:
                if any(word in sentence for word in ("手写按钮", "预测按钮", "默认按钮", "回复层", "CONTEXT_BUTTONS_BY_GROUP", "T.buttons")):
                    score += 12
                if any(word in sentence for word in ("图片消息", "调试回复", "例外")):
                    score -= 6
            if "宗门战" in query or ("宗门" in query and "奖励" in query):
                if any(word in sentence for word in ("周期", "周日", "领取", "奖励", "影响力", "贡献", "淬锋丹", "成员")):
                    score += 10
                if any(word in sentence for word in ("建立宗门", "加入宗门", "退出宗门")):
                    score -= 4
            if "世界物资" in query or "闭环" in query:
                if any(word in sentence for word in ("闭环", "六大类", "出售", "自动出售", "流入", "城池", "承接", "状态", "回收")):
                    score += 10
                if any(word in sentence for word in ("极·技能书", "角色成长道具")):
                    score -= 4
            if score > 0:
                label = _sentence_label(sentence)
                scored.append((score, label, sentence, source))
    scored.sort(key=lambda item: (item[0], len(item[2])), reverse=True)

    result: list[tuple[str, str, str]] = []
    seen_sentences: set[str] = set()
    for _score, label, sentence, source in scored:
        key = _normalize(sentence)
        if key in seen_sentences:
            continue
        seen_sentences.add(key)
        result.append((label, sentence, source))
        if len(result) >= 6:
            break
    return result


def _document_sentences(body: str) -> list[str]:
    """把文档正文拆成适合回答的短句。"""

    text = re.sub(r"[`*_>#-]+", "", body)
    raw_parts = re.split(r"[。；;]\s*|(?<=\S)\s+-\s+|(?<=\S)\s+[-*]\s+", text)
    result: list[str] = []
    for part in raw_parts:
        value = re.sub(r"\s+", " ", part).strip(" ，,。；;")
        if len(value) < 8:
            continue
        if len(value) > 120:
            value = value[:117].rstrip() + "..."
        result.append(value)
    return result


def _sentence_label(sentence: str) -> str:
    """给文档句子加短标签。"""

    if any(word in sentence for word in ("不能", "不得", "不再", "只", "必须", "上限", "限制")):
        return "规则"
    if any(word in sentence for word in ("发送", "命令", "入口", "按钮")):
        return "入口"
    if any(word in sentence for word in ("奖励", "产出", "来源", "掉落", "获得")):
        return "来源"
    if any(word in sentence for word in ("流程", "先", "再", "领取", "结算")):
        return "流程"
    if any(word in sentence for word in ("收益", "价格", "经验", "等级", "概率", "增益")):
        return "数值"
    return "要点"


def _document_topic(query: str, documents: list[KnowledgeEntry]) -> str:
    """生成文档综合回答的主题短句。"""

    if any(word in query for word in ("宗门", "宗门战")):
        return "宗门归属、周期结算、领取制奖励和世界增益之间的关系"
    if any(word in query for word in ("商场", "跑商", "出售", "交易")):
        return "商路交易、自动出售和世界物资流向之间的分工"
    if any(word in query for word in ("探险", "地图", "秘境")):
        return "地点、探险状态、奖励领取和特殊秘境的边界"
    if any(word in query for word in ("按钮", "富文本", "回复")):
        return "手写按钮优先、预测按钮克制、入口页分流"
    if any(word in query for word in ("物品", "世界物资", "古物", "民生", "建设", "药路")):
        return "物品分类、流入地点和后续世界状态影响"
    if any(word in query for word in ("武器", "附魔", "技能书", "装备", "宝石")):
        return "装备成长、武器技能和附魔流派的配合"
    title = documents[0].title if documents else query
    return f"{title} 相关规则"


def _document_sources(documents: list[KnowledgeEntry]) -> list[str]:
    """列出文档来源。"""

    result: list[str] = []
    seen: set[str] = set()
    for entry in documents:
        source = _document_source_text(entry)
        if not source or source in seen:
            continue
        seen.add(source)
        result.append(source)
    return result


def _document_source_text(entry: KnowledgeEntry) -> str:
    """格式化文档来源。"""

    source = entry.source or entry.group
    return f"{source}｜{entry.title}"


def _is_broad_document_query(query: str) -> bool:
    """判断问题是否更像综合设定问答。"""

    return any(
        word in query
        for word in (
            "设定",
            "规则",
            "流程",
            "系统",
            "机制",
            "怎么运作",
            "怎么玩",
            "为什么",
            "限制",
            "入口",
            "按钮",
            "关系",
            "影响",
            "闭环",
            "规划",
        )
    )


def _should_use_document_synthesis(query: str, matches: list[tuple[KnowledgeEntry, int]]) -> bool:
    """判断检索结果是否应保留更多文档章节。"""

    if _is_broad_document_query(query):
        return True
    doc_count = sum(1 for entry, _score in matches if entry.kind == "文档")
    structured_count = sum(1 for entry, _score in matches if entry.kind != "文档")
    return doc_count >= 3 and structured_count == 0


def _should_answer_as_topic(query: str, primary: KnowledgeEntry) -> bool:
    """泛问题优先回答玩法策略，具体实体问题再回答实体。"""

    normalized_query = _normalize(query)
    normalized_title = _normalize(primary.title)
    generic_words = ("武器", "流派", "宝石", "跑商", "商场", "探险", "首领", "虫洞", "玩法", "推荐")
    if any(word in query for word in generic_words) and normalized_title not in normalized_query:
        return True
    if any(word in query for word in ("开荒", "前期", "新手", "初期", "为什么", "怎么赚", "赚不到")):
        return True
    core = _core_query(query)
    if not core:
        return True
    return primary.kind == "文档"


def _personal_clarify_lines(
    query: str,
    primary: KnowledgeEntry,
    context: PlayerContext | None,
) -> list[str]:
    """个人推荐问题缺少角色或武器时，给出下一步而不是硬答。"""

    if not _is_personal_advice_query(query):
        return []
    if _normalize(primary.title) in _normalize(query):
        return []
    if context is None:
        return []
    if not context.exists:
        return [
            "结论：我还没有读到你的修仙角色，暂时不能按你的当前状态推荐。",
            "下一步：先创建角色或换成具体问题，例如“修仙百科 断念杖怎么配”。",
        ]
    if not context.weapon_name:
        return [
            "结论：你现在没有装备中的武器，没法判断“我的武器怎么配”。",
            "下一步：先发送“武器”查看并装备目标武器，或者直接问具体武器名。",
        ]
    return []


def _personal_weapon_entry(
    query: str,
    primary: KnowledgeEntry,
    context: PlayerContext | None,
) -> KnowledgeEntry | None:
    """把当前装备武器临时转换成百科条目。"""

    if not context or not context.exists or not context.weapon_name:
        return None
    if not _is_personal_advice_query(query):
        return None
    if _normalize(primary.title) in _normalize(query):
        return None

    body = _join_parts(
        f"武器类型：{context.weapon_type}",
        f"当前攻击：{context.weapon_attack}",
        f"掉落范围：{context.weapon_source or '已拥有'}",
        f"自带技能：{context.skill_name or '无'}",
        f"技能节奏：蓄势基准 {context.interval or ''}，倍率 {context.power or ''}，精神消耗 {context.cost_mp or ''}",
        context.skill_desc,
    )
    return KnowledgeEntry(
        title=context.weapon_name,
        group="武器",
        kind=context.weapon_type or "武器",
        body=body,
        keywords=_keywords(context.weapon_name, context.skill_name, context.weapon_type),
    )


def _is_personal_advice_query(query: str) -> bool:
    """判断是否是“按我当前情况推荐”的问题。"""

    return any(word in query for word in ("我", "我的", "当前", "现在")) and any(
        word in query for word in ("武器", "附魔", "怎么配", "怎么玩", "推荐", "流派")
    )


def _answer_conclusion(query: str, entries: list[KnowledgeEntry]) -> str:
    """根据问题意图生成一句直接结论。"""

    if not entries:
        return "结论：没有找到足够资料。"

    primary = entries[0]
    intent = _query_intent(query)
    body = primary.body

    if "铭刻之羽" in query and intent == "source":
        return "结论：铭刻之羽只从岁时情劫首领奖励产出；按首领权重独立判定，同一次首领奖励最多 1 枚。"

    if intent == "source":
        source = _extract_field(body, "掉落范围") or _extract_field(body, "掉落") or _extract_field(body, "坐标")
        if source:
            return f"结论：{primary.title} 的来源是 {source}。"
        return f"结论：{primary.title} 的来源需要结合对应玩法奖励规则判断。"

    if primary.group == "修仙物品":
        desc = _last_plain_part(body)
        effect = _extract_field(body, "效果")
        if intent == "usage":
            if desc and effect:
                return f"结论：{primary.title} 是{primary.kind}，主要作用是{desc.rstrip('。')}；数值效果为 {effect}。"
            if desc:
                return f"结论：{primary.title} 是{primary.kind}，主要作用是{desc}"
        return f"结论：{primary.title} 是{primary.kind}，属于修仙物品。"

    if primary.group == "武器" and primary.kind not in {"自带技能", "技能书附魔"}:
        weapon_type = _extract_field(body, "武器类型") or primary.kind
        skill = _extract_field(body, "自带技能")
        drop = _extract_field(body, "掉落范围")
        if intent == "build":
            style = _style_from_entries(entries)
            if style:
                return f"结论：{primary.title} 是{weapon_type}，适合围绕「{skill}」走{style}；来源：{drop}。"
            return f"结论：{primary.title} 是{weapon_type}，核心看自带技能「{skill}」和蓄势节奏；来源：{drop}。"
        return f"结论：{primary.title} 是{weapon_type}，自带技能「{skill}」，来源：{drop}。"

    if primary.kind == "技能书附魔":
        style = _enchant_style(primary.title)
        return f"结论：{primary.title} 属于{style or '技能书附魔'}，适合给同方向武器补强，不建议当万能书乱塞。"

    if primary.kind == "自带技能":
        return f"结论：{primary.title} 是武器自带技能，核心参数是{body}。"

    if primary.kind in {"跑商地点", "探险地点", "特殊收购", "回收地点"}:
        return f"结论：{primary.title} 是{primary.kind}；具体坐标、特产或规则看下面。"

    return f"结论：这个问题和「{primary.title}」最相关。"


def _query_intent(query: str) -> str:
    """识别问题意图。"""

    if any(word in query for word in ("谁掉", "哪里掉", "哪掉", "掉落", "产出", "来源", "哪里有", "从哪")):
        return "source"
    if any(word in query for word in ("怎么玩", "怎么配", "推荐", "适合", "搭配", "配什么", "流派")):
        return "build"
    if any(word in query for word in ("怎么用", "什么用", "有啥用", "作用", "效果", "干嘛", "用途")):
        return "usage"
    return "lookup"


def _core_query(query: str) -> str:
    """去掉常见问法，留下实体词。"""

    value = _normalize(query)
    for word in (
        "我的",
        "我",
        "有什么用",
        "有啥用",
        "什么用",
        "怎么用",
        "怎么玩",
        "怎么配",
        "配什么",
        "适合",
        "推荐",
        "搭配",
        "流派",
        "谁掉落",
        "谁掉",
        "哪里掉落",
        "哪里掉",
        "哪掉",
        "掉落",
        "产出",
        "来源",
        "从哪",
        "吗",
        "呢",
        "？",
        "?",
    ):
        value = value.replace(_normalize(word), "")
    return value.strip()


def _extract_field(body: str, label: str) -> str:
    """从 `标签：值；` 这种正文里取字段。"""

    match = re.search(rf"{re.escape(label)}：([^；。]+)", body)
    return match.group(1).strip() if match else ""


def _last_plain_part(body: str) -> str:
    """取正文最后一段自然语言描述。"""

    for part in reversed([item.strip() for item in re.split(r"[；。]", body) if item.strip()]):
        if "：" not in part and "=" not in part:
            return f"{part}。"
    return ""


def _weapon_profile(entry: KnowledgeEntry, entries: list[KnowledgeEntry]) -> WeaponProfile:
    """把武器条目转换成更容易推理的画像。"""

    tempo = _extract_field(entry.body, "技能节奏")
    desc = _last_plain_part(entry.body).rstrip("。")
    interval = _number_after(tempo, "蓄势基准", int)
    power = _number_after(tempo, "倍率", float)
    cost_mp = _number_after(tempo, "精神消耗", int)
    style = _style_from_entries(entries) or _style_from_weapon(entry, desc)
    traits = _weapon_traits(entry, desc, interval, power, cost_mp)
    return WeaponProfile(
        title=entry.title,
        weapon_type=_extract_field(entry.body, "武器类型") or entry.kind,
        attack=_safe_int(_extract_field(entry.body, "基础攻击")),
        source=_extract_field(entry.body, "掉落范围"),
        skill=_extract_field(entry.body, "自带技能") or "无",
        interval=interval,
        power=power,
        cost_mp=cost_mp,
        desc=desc,
        style=style or _role_from_traits(traits),
        traits=frozenset(traits),
    )


def _number_after(text: str, label: str, caster: Any) -> Any:
    """从短句里取某个标签后的数字。"""

    match = re.search(rf"{re.escape(label)}\s*([-+]?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return caster(match.group(1))
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    """宽松转换整数。"""

    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float | None:
    """宽松转换浮点数。"""

    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _weapon_traits(
    entry: KnowledgeEntry,
    desc: str,
    interval: int | None,
    power: float | None,
    cost_mp: int | None,
) -> set[str]:
    """识别武器强项和代价。"""

    text = f"{entry.title} {entry.body} {desc}"
    traits: set[str] = set()
    if interval is not None:
        if interval <= 3:
            traits.add("fast")
        elif interval >= 5:
            traits.add("slow")
        else:
            traits.add("medium_tempo")
    if power is not None:
        if power >= 1.30:
            traits.add("burst")
        elif power <= 1.08:
            traits.add("light_hit")
        else:
            traits.add("steady_power")
    if cost_mp is not None:
        if cost_mp >= 12:
            traits.add("high_cost")
        elif cost_mp <= 7:
            traits.add("low_cost")
    if any(word in text for word in ("削弱精神", "压精神", "断念", "镇魂", "摄心", "扰乱", "拖慢", "打断行动条", "压制", "扰神", "压行动条")):
        traits.add("control")
    if any(word in text for word in ("灼烧", "流血", "毒", "残火", "燃")):
        traits.add("dot")
    if any(word in text for word in ("穿透", "破甲", "破防", "压低防御", "压防", "贯日", "破阵")):
        traits.add("pierce")
    if any(word in text for word in ("回春", "回血", "回复血气", "吸血", "续航", "护身", "减伤", "承伤")):
        traits.add("sustain")
    if any(word in text for word in ("反击", "反震", "格挡", "借势回击")):
        traits.add("counter")
    if any(word in text for word in ("连击", "多段", "高频", "双斩", "乱刃", "飞刃", "短刃", "匕")):
        traits.add("combo")
    if any(word in text for word in ("命中", "闪避", "身形", "更活")):
        traits.add("accuracy")
    if any(word in text for word in ("一击", "单次爆发", "斩杀", "收割", "点杀")):
        traits.add("execute")
    if any(word in text for word in ("星落", "玉京", "协作", "贡献")):
        traits.add("boss")
    return traits


def _role_from_traits(traits: set[str] | frozenset[str]) -> str:
    """根据特征给武器一个主定位。"""

    if "control" in traits:
        return "压制控制流派"
    if "dot" in traits:
        return "持续伤害流派"
    if "combo" in traits or "fast" in traits:
        return "高频连击流派"
    if "pierce" in traits or "burst" in traits:
        return "重击破防流派"
    if "sustain" in traits:
        return "生存续航流派"
    if "counter" in traits:
        return "反击护身流派"
    if "execute" in traits:
        return "斩杀收割流派"
    if "boss" in traits:
        return "首领协作流派"
    return "特化武器"


def _goal_from_query(query: str) -> str:
    """识别玩家问的是哪类目标。"""

    if any(word in query for word in ("决斗", "切磋", "抢劫", "报复", "围殴", "玩家", "对战", "pvp", "PVP")):
        return "pvp"
    if any(word in query for word in ("首领", "虫洞", "boss", "Boss", "BOSS", "团战", "贡献", "长战")):
        return "boss"
    if any(word in query for word in ("探险", "刷图", "打怪", "日常", "开荒", "升级", "练级", "刷怪")):
        return "daily"
    return "general"


def _tempo_text(interval: int | None) -> str:
    """技能节奏说明。"""

    if interval is None:
        return "技能节奏未知"
    if interval <= 3:
        label = "高频"
    elif interval == 4:
        label = "中速"
    else:
        label = "慢速"
    return f"技能节奏：{label}，约每 {interval} 次出手触发；蓄势基准 {interval}（越小越快）"


def _power_text(power: float | None) -> str:
    """技能倍率说明。"""

    if power is None:
        return "技能倍率未知"
    if power >= 1.35:
        level = "爆发高"
    elif power >= 1.16:
        level = "伤害中高"
    elif power <= 1.08:
        level = "单次偏轻"
    else:
        level = "伤害稳定"
    return f"技能倍率 {power:g}，{level}"


def _cost_text(cost_mp: int | None) -> str:
    """精神消耗说明。"""

    if cost_mp is None:
        return "精神消耗未知"
    if cost_mp >= 12:
        level = "偏高，要重视精神续航"
    elif cost_mp <= 7:
        level = "偏低，适合频繁释放"
    else:
        level = "中等"
    return f"精神消耗 {cost_mp}，{level}"


def _weapon_goal_summary(profile: WeaponProfile, goal: str) -> str:
    """给出当前目标下的一句核心判断。"""

    traits = profile.traits
    if goal == "daily":
        if "fast" in traits or "combo" in traits:
            return "日常探险判断：适合刷图，胜在节奏快、出手多，清普通怪会比较舒服。"
        if "sustain" in traits:
            return "日常探险判断：适合稳扎稳打，越级或连续战斗更舒服，但不一定是最快清怪。"
        if "control" in traits:
            return "日常探险判断：能用，但不是最快刷怪武器；它更擅长压住高压目标的技能节奏。"
        if "slow" in traits:
            return "日常探险判断：清小怪偏慢，适合打硬目标，不适合只追求刷图效率。"
        return "日常探险判断：能用，关键看精神续航和实际清怪速度。"
    if goal == "boss":
        if "pierce" in traits or "dot" in traits or "boss" in traits:
            return "首领虫洞判断：契合长战，穿透、持续伤害或协作收益会比单纯面板更重要。"
        if "control" in traits:
            return "首领虫洞判断：控制能帮忙拖节奏，但贡献未必比穿透、持续伤害和稳定命中高。"
        if "sustain" in traits:
            return "首领虫洞判断：偏保命和稳定站场，适合活到后段，但输出贡献要靠附魔补。"
        return "首领虫洞判断：能参与，但要看它是否能在长战里持续贡献。"
    if goal == "pvp":
        if "control" in traits or "accuracy" in traits:
            return "决斗抢劫判断：很契合，压精神、扰乱行动、命中闪避会直接影响对方出手质量。"
        if "burst" in traits or "execute" in traits:
            return "决斗抢劫判断：可以走爆发收割，但要避免技能蓄势太慢被对方先打乱。"
        if "sustain" in traits or "counter" in traits:
            return "决斗抢劫判断：适合拖长战，把对方输出转成你的承伤收益。"
        return "决斗抢劫判断：能打，但最好用附魔补扰乱、命中或生存。"
    if "control" in traits:
        return "总体判断：它偏对战和高压目标，不是最快刷图路线；用它要接受清小怪不如快武器。"
    if "fast" in traits or "combo" in traits:
        return "总体判断：它偏日常效率和多次出手，适合先把刷图手感做起来。"
    if "pierce" in traits or "dot" in traits or "burst" in traits:
        return "总体判断：它偏打硬目标，首领、虫洞、越级怪会比低级小怪更能体现价值。"
    if "sustain" in traits or "counter" in traits:
        return "总体判断：它偏稳定长战，强在活得久和容错高。"
    return "总体判断：它要结合自带技能和附魔方向来看。"


def _weapon_strengths(profile: WeaponProfile) -> list[str]:
    """按画像总结强项。"""

    traits = profile.traits
    result: list[str] = []
    if "fast" in traits or "combo" in traits:
        result.append("技能来得快，适合堆命中、连击和多次触发收益")
    if "burst" in traits or "execute" in traits:
        result.append("单次技能有爆发，适合压血线或收割")
    if "pierce" in traits:
        result.append("能处理高防目标，打硬怪更有价值")
    if "dot" in traits:
        result.append("持续伤害适合血厚长战")
    if "control" in traits:
        result.append("压精神和扰乱行动，适合限制对方技能")
    if "sustain" in traits:
        result.append("有续航或护身，连续战斗容错更高")
    if "counter" in traits:
        result.append("承伤后能转成反击收益")
    if "accuracy" in traits:
        result.append("命中或闪避更稳，对战手感更好")
    return result[:4]


def _weapon_weakness(profile: WeaponProfile) -> str:
    """总结武器主要短板。"""

    traits = profile.traits
    weakness: list[str] = []
    if "slow" in traits:
        weakness.append("蓄势偏慢，打低级怪和短战会显得不够利索")
    if "high_cost" in traits:
        weakness.append("精神消耗偏高，精神不足时会断技能节奏")
    if "control" in traits and "burst" not in traits:
        weakness.append("压制不等于高爆发，追贡献时不能只堆削精神")
    if "light_hit" in traits:
        weakness.append("单次伤害偏轻，需要靠频率或特效补收益")
    return "；".join(weakness) + "。" if weakness else ""


def _weapon_goal_advice(profile: WeaponProfile, goal: str) -> list[str]:
    """按目标给武器附魔建议。"""

    traits = profile.traits
    if goal == "daily":
        if "control" in traits:
            return [
                "探险配法：回春书或玄盾书先保证连续战斗，再补断念书、梦雾书这类压制；别把所有栏位都堆控制。",
                "效率提醒：如果只是平刷低级怪，高频短刃、飞刃、匕类通常会比它更快。",
            ]
        if "fast" in traits or "combo" in traits:
            return ["探险配法：风刃书、沙影书、流光书、追星书优先，缺生存再补回春书或玄盾书。"]
        if "sustain" in traits:
            return ["探险配法：回春书、玄盾书、灵木书能把稳字拉满；想提速再补风刃书或流光书。"]
        return ["探险配法：优先补命中、续航和节奏，别为了单次伤害把蓄势堆得太慢。"]
    if goal == "boss":
        if "control" in traits:
            return [
                "首领虫洞配法：破阵书、玉京书、星落书优先补贡献和稳定命中，镇魂书或天机书保留一格做压制就够。",
                "不要只叠断念系：Boss 长战更看穿透、持续伤害、命中和站场时间。",
            ]
        if "dot" in traits:
            return ["首领虫洞配法：灼心书、血雨书、毒云书、残焰书能把长战收益拉起来，再补玉京书保命中。"]
        if "pierce" in traits or "burst" in traits:
            return ["首领虫洞配法：破阵书、穿云书、玉京书、星落书优先；精神吃紧时少塞高消耗书。"]
        return ["首领虫洞配法：优先补破阵书、玉京书、星落书；站不住再补玄盾书或灵木书。"]
    if goal == "pvp":
        if "control" in traits:
            return [
                "决斗抢劫配法：断念书、镇魂书、天机书、梦雾书、清心书最贴合，核心是让对方技能慢、精神亏、出手乱。",
                "如果你自己容易被秒，玄盾书或镜湖书比继续堆输出更有用。",
            ]
        if "burst" in traits or "execute" in traits:
            return ["决斗抢劫配法：无相书、断海书、破军书打爆发，镜湖书或清心书补稳定。"]
        return ["决斗抢劫配法：月蚀书、镜湖书、影叶书、清心书能补扰乱和容错。"]
    if "control" in traits:
        return [
            "分场景配法：日常探险用回春书/玄盾书加一到两本压制书；首领虫洞用破阵书/玉京书/星落书补贡献；决斗抢劫才全力考虑断念书、镇魂书、天机书、梦雾书、清心书。",
            "一句话：它不是刷低级怪的最快答案，但在对战和高压长战里很有特色。",
        ]
    if "fast" in traits or "combo" in traits:
        return ["分场景配法：日常优先风刃书、沙影书、流光书、追星书；打硬目标再补破甲书或穿云书。"]
    if "dot" in traits:
        return ["分场景配法：灼心书、血雨书、毒云书、残焰书适合长战；短战想提速可补流光书或玉京书。"]
    if "pierce" in traits or "burst" in traits:
        return ["分场景配法：破甲书、崩山书、穿云书、镇岳书打硬目标；日常探险要防蓄势过慢。"]
    if "sustain" in traits:
        return ["分场景配法：回春书、玄盾书、血契书、灵木书走长战；想打贡献再补破阵书或玉京书。"]
    return ["搭配建议：优先强化自带技能方向，再补一个当前最缺的生存、命中或精神续航。"]


def _weapon_scores(profile: WeaponProfile) -> dict[str, int]:
    """按场景给武器打分，满分 10。"""

    traits = profile.traits
    scores = {
        "日常探险": 5,
        "首领虫洞": 5,
        "决斗抢劫": 5,
        "越级生存": 5,
        "开荒": 5,
    }
    if "fast" in traits or "combo" in traits:
        scores["日常探险"] += 3
        scores["开荒"] += 2
    if "low_cost" in traits:
        scores["日常探险"] += 1
        scores["开荒"] += 1
    if "slow" in traits:
        scores["日常探险"] -= 2
        scores["开荒"] -= 1
    if "high_cost" in traits:
        scores["日常探险"] -= 1
        scores["开荒"] -= 1
    if "pierce" in traits:
        scores["首领虫洞"] += 3
        scores["越级生存"] += 1
    if "dot" in traits:
        scores["首领虫洞"] += 3
        scores["决斗抢劫"] += 1
    if "burst" in traits or "execute" in traits:
        scores["首领虫洞"] += 1
        scores["决斗抢劫"] += 2
    if "control" in traits:
        scores["决斗抢劫"] += 4
        scores["越级生存"] += 2
        scores["日常探险"] -= 1
    if "sustain" in traits:
        scores["越级生存"] += 4
        scores["开荒"] += 2
        scores["首领虫洞"] += 1
    if "counter" in traits:
        scores["越级生存"] += 2
        scores["决斗抢劫"] += 2
    if "accuracy" in traits:
        scores["日常探险"] += 1
        scores["决斗抢劫"] += 1
        scores["开荒"] += 1
    return {key: _clamp_score(value) for key, value in scores.items()}


def _enchant_scores(style: str, effects: dict[str, float]) -> dict[str, int]:
    """按场景给技能书打分，满分 10。"""

    scores = {
        "日常探险": 5,
        "首领虫洞": 5,
        "决斗抢劫": 5,
        "越级生存": 5,
        "开荒": 5,
    }
    if "高频连击" in style:
        scores["日常探险"] += 3
        scores["开荒"] += 2
    if "重击破防" in style or "首领协作" in style:
        scores["首领虫洞"] += 3
    if "持续伤害" in style:
        scores["首领虫洞"] += 3
        scores["决斗抢劫"] += 1
    if "压制控制" in style or "决斗扰乱" in style:
        scores["决斗抢劫"] += 4
        scores["越级生存"] += 1
    if "生存续航" in style or "反击护身" in style:
        scores["越级生存"] += 3
        scores["开荒"] += 1
    if "斩杀收割" in style:
        scores["决斗抢劫"] += 2
        scores["首领虫洞"] += 1
    if effects.get("interval_delta", 0) < 0:
        scores["日常探险"] += 2
        scores["开荒"] += 1
    if effects.get("interval_delta", 0) > 0:
        scores["日常探险"] -= 2
        scores["开荒"] -= 1
    if any(key in effects for key in ("pierce_bonus", "defense_suppress", "combo_damage_bonus")):
        scores["首领虫洞"] += 1
    if any(key in effects for key in ("mp_suppress", "stun_rate", "dodge_bonus")):
        scores["决斗抢劫"] += 1
    if any(key in effects for key in ("life_steal", "shield_bonus", "damage_reduce", "counter_rate")):
        scores["越级生存"] += 1
    if effects.get("skill_power_bonus", 0) < 0 or effects.get("single_hit_bonus", 0) < 0:
        scores["首领虫洞"] -= 1
    return {key: _clamp_score(value) for key, value in scores.items()}


def _clamp_score(value: int) -> int:
    """限制评分范围。"""

    return max(1, min(10, int(value)))


def _score_text(scores: dict[str, int]) -> str:
    """格式化评分。"""

    order = ("日常探险", "首领虫洞", "决斗抢劫", "越级生存", "开荒")
    return "｜".join(f"{name} {scores.get(name, 5)}/10" for name in order)


def _weapon_plan_lines(profile: WeaponProfile, goal: str) -> list[str]:
    """给武器生成最优和替代附魔方案。"""

    best, alternatives, avoid = _weapon_plan(profile, goal)
    lines = [f"最优方案：{best}。"]
    if alternatives:
        lines.append(f"替代方案：{alternatives}。")
    if avoid:
        lines.append(f"不建议：{avoid}。")
    return lines


def _weapon_plan(profile: WeaponProfile, goal: str) -> tuple[str, str, str]:
    """按武器和场景选择附魔方案。"""

    traits = profile.traits
    if goal == "daily":
        if "control" in traits:
            return (
                "回春书/玄盾书 + 断念书/梦雾书，先稳连续战斗",
                "风刃书或流光书补节奏",
                "全堆断念系，清小怪会慢",
            )
        if "fast" in traits or "combo" in traits:
            return (
                "风刃书 + 沙影书 + 流光书/追星书",
                "缺生存补回春书或玄盾书",
                "把蓄势堆慢的重击书塞太多",
            )
        return ("风刃书/流光书 + 回春书/玄盾书", "缺伤害再补破甲书或穿云书", "只看单次爆发")
    if goal == "boss":
        if "control" in traits:
            return (
                "破阵书 + 玉京书 + 星落书，保留镇魂书/天机书一格做压制",
                "穿云书、灼心书、玄盾书按缺口替换",
                "全堆削精神，贡献会虚",
            )
        if "dot" in traits:
            return (
                "灼心书 + 血雨书 + 毒云书/残焰书",
                "玉京书补命中，玄盾书补站场",
                "短战思路，持续伤害没时间滚起来",
            )
        if "pierce" in traits or "burst" in traits:
            return (
                "破阵书 + 穿云书 + 玉京书/星落书",
                "精神吃紧时用玄盾书或灵木书替换一格",
                "高消耗书堆太满导致放不出技能",
            )
        return ("破阵书 + 玉京书 + 星落书", "玄盾书/灵木书补生存", "只堆控制或只堆回血")
    if goal == "pvp":
        if "control" in traits:
            return (
                "断念书 + 镇魂书 + 天机书 + 梦雾书/清心书",
                "怕被秒就用玄盾书或镜湖书替一格",
                "照搬首领贡献配法",
            )
        if "burst" in traits or "execute" in traits:
            return ("无相书 + 断海书 + 破军书", "镜湖书或清心书补稳定", "完全不补命中和容错")
        return ("月蚀书 + 镜湖书 + 影叶书/清心书", "玄盾书补生存", "只堆刷图提速书")
    if "control" in traits:
        return (
            "按目标切换：探险稳，首领补贡献，决斗堆压制",
            "不确定目标时先回春书/玄盾书保底",
            "一套附魔想通吃所有玩法",
        )
    if "fast" in traits or "combo" in traits:
        return ("风刃书 + 沙影书 + 流光书/追星书", "破甲书或穿云书补硬目标", "为了面板把节奏拖慢")
    if "sustain" in traits:
        return ("回春书 + 玄盾书 + 灵木书", "破阵书或玉京书补贡献", "只有回血没有输出")
    return ("围绕自带技能强化，再补一个生存或命中位", "按目标在破阵书、风刃书、玄盾书里选", "只看稀有度")


def _enchant_plan_text(style: str, effects: dict[str, float], goal: str) -> str:
    """给技能书生成落地建议。"""

    if "首领协作" in style:
        if goal == "pvp":
            return "搭配建议：它可以在决斗里客串稳控或减伤，但第一优先仍是月蚀书、镜湖书、影叶书、清心书。"
        return "搭配建议：优先给打首领、虫洞的武器；日常探险慎用，容易为了贡献牺牲刷图节奏。"
    if "决斗扰乱" in style:
        if goal == "boss":
            return "搭配建议：打首领时它只能补扰乱或容错，主要贡献仍应靠破阵书、玉京书、星落书。"
        return "搭配建议：优先给决斗、抢劫武器；日常探险和首领贡献都不是它的主场。"
    if "压制控制" in style:
        return "搭配建议：给断念、镇魂、梦雾这类控制武器最顺；打首领时至少搭一格破阵书、玉京书或星落书补贡献。"
    if "高频连击" in style:
        return "搭配建议：给短刃、飞刃、匕、多段技能武器最舒服；重武器用它主要是补节奏。"
    if "持续伤害" in style:
        return "搭配建议：适合血厚长战，最好配命中或站场；刷低级怪不用急着上。"
    if "生存续航" in style or "反击护身" in style:
        return "搭配建议：适合越级、长战和怕暴毙的配置；输出不够时要留一格给破防或增伤。"
    if effects.get("interval_delta", 0) < 0:
        return "搭配建议：它能提速，适合给慢一点但想刷图的武器补手感。"
    return "搭配建议：先看武器自带技能是否同方向，再决定是否占栏位。"


def _context_weapon_lines(context: PlayerContext | None, profile: WeaponProfile, goal: str) -> list[str]:
    """根据玩家当前情况给武器回答补一句具体建议。"""

    if not context or not context.exists:
        if _needs_personal_context(profile.title):
            return ["个人建议：还没读取到你的角色数据，先创建角色或装备武器后再问“我的武器怎么配”。"]
        return []
    lines: list[str] = []
    if context.weapon_name:
        same_weapon = _normalize(profile.title) in _normalize(context.weapon_name) or _normalize(context.weapon_name) in _normalize(profile.title)
        if same_weapon:
            lines.append(
                f"结合你当前武器：{context.weapon_name}[{context.weapon_quality}] Lv{context.weapon_level}/{context.weapon_max_level}，"
                f"精神 {context.mp}/{context.max_mp}，已附魔 {('、'.join(context.enchant_names) if context.enchant_names else '无')}。"
            )
        elif goal != "general":
            lines.append(f"结合你当前武器：你现在装备的是 {context.weapon_name}，如果要换成 {profile.title}，要重新按它的蓄势和技能方向配。")
    if context.max_mp and context.mp / max(1, context.max_mp) < 0.35:
        lines.append("个人提醒：你当前精神偏低，先别优先堆高消耗技能书，恢复或补精神上限更稳。")
    if context.level and context.level < 25 and ("slow" in profile.traits or "high_cost" in profile.traits):
        lines.append("个人提醒：你等级还偏前期，慢速或高消耗武器会拖开荒节奏，先用高频或续航配置过渡更舒服。")
    return lines


def _context_enchant_lines(
    context: PlayerContext | None,
    enchant_name: str,
    style: str,
    effects: dict[str, float],
    goal: str,
) -> list[str]:
    """根据玩家当前情况给技能书回答补一句具体建议。"""

    if not context or not context.exists:
        return []
    if not context.weapon_name:
        return ["个人建议：你当前没有装备武器，先装备目标武器后再决定是否附魔。"]
    lines = [
        f"结合你当前武器：{context.weapon_name}[{context.weapon_quality}]，技能 {context.skill_name or '未知'}，已附魔 {('、'.join(context.enchant_names) if context.enchant_names else '无')}。"
    ]
    if any(_normalize(enchant_name) in _normalize(name) for name in context.enchant_names):
        lines.append("个人提醒：这本书你当前武器已经装过，同一把武器不能重复附魔同一本。")
    if context.interval is not None and context.interval >= 5 and effects.get("interval_delta", 0) > 0:
        lines.append("个人提醒：你的武器本来就慢，这本书还会拖慢蓄势，除非是首领长战，否则慎用。")
    if context.max_mp and context.mp / max(1, context.max_mp) < 0.35 and (_safe_int(effects.get("mp_delta")) or 0) > 0:
        lines.append("个人提醒：你当前精神偏低，高消耗附魔会让技能节奏更容易断。")
    if goal != "general" and not _style_matches_goal(style, goal):
        lines.append(f"个人取舍：你问的是{_goal_name(goal)}，这本书可以客串，但不是第一优先。")
    return lines


def _parse_effect_values(effect: str) -> dict[str, float]:
    """解析 `key=value` 效果字段。"""

    result: dict[str, float] = {}
    for part in re.split(r"[，,]", effect):
        if "=" not in part:
            continue
        key, raw_value = [item.strip() for item in part.split("=", 1)]
        try:
            result[key] = float(raw_value)
        except ValueError:
            continue
    return result


def _enchant_best_scene(style: str, effects: dict[str, float], goal: str) -> str:
    """技能书最适合的场景。"""

    if "首领协作" in style:
        if goal == "pvp":
            return "首领、虫洞和团队长战；决斗里只能客串稳控，不是首选"
        return "首领、虫洞和团队长战"
    if "决斗扰乱" in style:
        if goal == "boss":
            return "决斗、抢劫这类玩家对战；首领里只能补扰乱，不是贡献首选"
        return "决斗、抢劫这类玩家对战"
    if "压制控制" in style:
        if goal == "boss":
            return "对战、抢劫、越级高压目标；首领里要搭配穿透或命中补贡献"
        return "对战、抢劫、越级高压目标"
    if "持续伤害" in style:
        return "血厚目标和长战"
    if "高频连击" in style:
        return "日常探险和高频技能武器"
    if "生存续航" in style or "反击护身" in style:
        return "越级探险、长战和保命"
    if "重击破防" in style or "斩杀收割" in style:
        return "高防目标、压血线和爆发窗口"

    if goal == "pvp":
        if any(key in effects for key in ("mp_suppress", "stun_rate", "defense_suppress", "dodge_bonus")):
            return "决斗、抢劫这类玩家对战"
    if goal == "boss":
        if any(key in effects for key in ("pierce_bonus", "defense_suppress", "burn_rate", "bleed_rate", "combo_damage_bonus", "hit_bonus")):
            return "首领、虫洞这种长战贡献"
    if goal == "daily":
        if effects.get("interval_delta", 0) < 0 or any(key in effects for key in ("combo_bonus", "hit_bonus", "life_steal", "shield_bonus")):
            return "日常探险和连续刷怪"
    return "同方向武器"


def _enchant_fit_text(style: str, effects: dict[str, float], goal: str) -> str:
    """说明技能书适合什么武器。"""

    fits: list[str] = []
    if "首领协作" in style:
        fits.append("首领、虫洞和团队长战武器")
    elif "决斗扰乱" in style:
        fits.append("决斗、抢劫和玩家对战武器")
    elif "压制控制" in style:
        fits.append("压精神、扰乱行动的控制武器")
    elif "高频连击" in style:
        fits.append("高频、多段、连击武器")
    elif "持续伤害" in style:
        fits.append("血厚目标和长战武器")
    elif "生存续航" in style:
        fits.append("需要站场、回血或护身的武器")
    elif "反击护身" in style:
        fits.append("承伤反击和护身武器")
    elif "重击破防" in style:
        fits.append("高防目标和重击武器")
    elif "斩杀收割" in style:
        fits.append("压血线和爆发收割武器")
    if effects.get("interval_delta", 0) < 0:
        fits.append("本身技能慢一点但想提速的武器")
    if effects.get("interval_delta", 0) > 0:
        fits.append("能接受蓄势变慢、追求长战收益的武器")
    if any(key in effects for key in ("combo_bonus", "combo_damage_bonus")):
        fits.append("高频、多段、连击武器")
    if any(key in effects for key in ("pierce_bonus", "defense_suppress", "heavy_bonus")):
        fits.append("打高防怪、首领或虫洞的武器")
    if any(key in effects for key in ("burn_rate", "bleed_rate")):
        fits.append("能把战斗拖长的持续伤害武器")
    if any(key in effects for key in ("mp_suppress", "stun_rate")) and not any(word in style for word in ("首领协作", "持续伤害")):
        fits.append("决斗、抢劫或压制控制武器")
    if any(key in effects for key in ("life_steal", "shield_bonus", "damage_reduce", "counter_rate")):
        fits.append("需要站场和容错的武器")
    if any(key in effects for key in ("hit_bonus", "dodge_bonus")):
        fits.append("需要命中、闪避稳定性的武器")
    if not fits:
        fits.append(style.replace("流派", "") or "同方向武器")
    scene_fit = _style_matches_goal(style, goal)
    if goal != "general" and scene_fit:
        fits.append(f"当前问题偏{_goal_name(goal)}，优先取与这个目标有关的收益")
    elif goal != "general" and not scene_fit:
        fits.append(f"当前问题偏{_goal_name(goal)}，但这本书不是该场景首选")
    return "、".join(dict.fromkeys(fits)) + "。"


def _enchant_bad_text(effects: dict[str, float]) -> str:
    """说明技能书不适合的情况。"""

    bad: list[str] = []
    if effects.get("interval_delta", 0) > 0:
        bad.append("追求快速刷图时慎用，因为它会拖慢蓄势")
    if effects.get("skill_power_bonus", 0) < 0 or effects.get("single_hit_bonus", 0) < 0:
        bad.append("追求单次爆发时慎用，因为它牺牲直接伤害")
    if effects.get("damage_reduce", 0) < 0:
        bad.append("身板薄时慎用，因为自身承伤会更重")
    if effects.get("mp_suppress", 0) > 0 and len(effects) <= 2:
        bad.append("纯刷低级怪时收益一般，因为削精神不等于更快击杀")
    if not bad:
        bad.append("不适合和自带技能方向完全相反的武器硬配")
    return "；".join(bad) + "。"


def _goal_name(goal: str) -> str:
    """目标名。"""

    return {
        "daily": "日常探险",
        "boss": "首领虫洞",
        "pvp": "决斗抢劫",
    }.get(goal, "通用玩法")


def _style_matches_goal(style: str, goal: str) -> bool:
    """判断技能书流派是否贴合当前问题场景。"""

    if goal == "general":
        return True
    if goal == "boss":
        return any(word in style for word in ("首领协作", "持续伤害", "重击破防", "斩杀收割"))
    if goal == "pvp":
        return any(word in style for word in ("决斗扰乱", "压制控制", "反击护身", "生存续航"))
    if goal == "daily":
        return any(word in style for word in ("高频连击", "生存续航"))
    return True


def _style_from_entries(entries: list[KnowledgeEntry]) -> str:
    """从命中的技能书或正文里推断流派。"""

    styles = (
        "高频连击流派",
        "重击破防流派",
        "持续伤害流派",
        "压制控制流派",
        "生存续航流派",
        "反击护身流派",
        "斩杀收割流派",
        "首领协作流派",
        "决斗扰乱流派",
    )
    text = " ".join(f"{entry.title} {entry.body}" for entry in entries)
    for style in styles:
        if style in text:
            return style
    return ""


def _style_from_weapon(entry: KnowledgeEntry, desc: str) -> str:
    """根据武器描述推断流派。"""

    text = f"{entry.title} {entry.body} {desc}"
    if any(word in text for word in ("削弱精神", "压精神", "断念", "镇魂", "扰乱")):
        return "压制控制流派"
    if any(word in text for word in ("灼烧", "流血", "毒", "残焰")):
        return "持续伤害流派"
    if any(word in text for word in ("连击", "高频", "命中稳定")):
        return "高频连击流派"
    if any(word in text for word in ("穿透", "破甲", "贯日", "破防")):
        return "重击破防流派"
    if any(word in text for word in ("护身", "回春", "吸血", "减伤")):
        return "生存续航流派"
    if any(word in text for word in ("反击", "反震")):
        return "反击护身流派"
    if any(word in text for word in ("斩杀", "收割", "无相")):
        return "斩杀收割流派"
    return ""


def _related_to(primary: KnowledgeEntry, candidate: KnowledgeEntry) -> bool:
    """判断候选资料是否与精确命中项同主题。"""

    primary_text = _normalize(f"{primary.title} {primary.body} {' '.join(primary.keywords)}")
    candidate_text = _normalize(f"{candidate.title} {candidate.body} {' '.join(candidate.keywords)}")
    if primary.group == "武器" and candidate.group == "武器":
        return any(word in candidate_text and word in primary_text for word in ("断念", "赤霞", "破军", "回春", "反震", "流派"))
    if primary.group == "修仙物品" and candidate.group == "修仙物品":
        return primary.kind == candidate.kind and any(word in candidate_text and word in primary_text for word in ("探险", "恢复", "精神", "血气", "跑商"))
    return False


def _reward_rank(entry: KnowledgeEntry) -> int:
    """奖励概率资料排序。"""

    ranks = {
        "高权重传统节日": 4,
        "普通传统节日": 3,
        "普通节气": 2,
        "每日旧愿": 1,
    }
    return ranks.get(entry.title, 0)


def _needs_personal_context(query: str) -> bool:
    """判断是否需要读取当前玩家上下文。"""

    return any(word in query for word in ("我", "我的", "推荐", "怎么配", "怎么玩", "适合", "搭配", "配什么", "流派"))


def _priority(entry: KnowledgeEntry) -> int:
    """同分排序：结构化资料优先于长文档。"""

    return 1 if entry.kind != "文档" else 0


def _answer_entry_rank(entry: KnowledgeEntry, intent: str) -> int:
    """同名资料优先级：技能书问题优先使用真实附魔效果。"""

    if intent == "usage" and entry.group == "修仙物品":
        return 35
    if entry.kind == "技能书附魔":
        return 30
    if entry.kind == "自带技能":
        return 20
    if entry.group == "武器":
        return 15
    if entry.group == "修仙物品" and entry.kind == "技能书":
        return 8
    if intent == "source" and entry.group == "修仙物品":
        return 10
    return 1


service = EncyclopediaService(db)
