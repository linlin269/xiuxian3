"""武器组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    computed_weapon_enchant_slots,
    computed_weapon_potential_slots,
    computed_weapon_attack,
    dump_json,
    enchant_label_name,
    load_json,
    money,
    parse_weapon_ref,
    quality_factor,
    split_words,
    ts,
    weapon_id_label,
    weapon_label_name,
)
from ..rules import (
    weapon_upgrade_cost,
    weapon_exp_for_level,
    weapon_exp_progress,
    weapon_level_from_exp,
)
from ..sql import db
from ..weapon_core import WeaponCore


class WeaponService(WeaponCore):
    """武器持有、切换、升级、附魔和掉落。"""

    def list_weapons(self, client_id: str) -> str:
        """查看武器简表。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.ensure_starter_weapon(client_id)
        rows = self.weapons(client_id)
        panel = T.panel()
        panel.section(f"武器 共{len(rows)}把")
        for row in rows:
            panel.line(self._weapon_summary_text(row))
        equipped = next((row for row in rows if int(row["equipped"])), None)
        if not equipped:
            return panel.render()
        return panel.render() + f"<升级武器 {equipped['weapon_id']}:武器升级>"

    def detail(self, client_id: str, message: str) -> str:
        """查看单把武器详情。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        player = self.recalc_player(client_id)
        self.ensure_starter_weapon(client_id)

        rows = self.weapons(client_id)
        weapon = self._resolve_default_weapon(rows, message)
        wanted_weapon_id = parse_weapon_ref(message)
        if not weapon and message.strip() and wanted_weapon_id <= 0:
            return T.hint("查看武器格式不正确。", "发送：查看武器，或发送：查看武器 武器ID，例如：查看武器 武器#1")
        if not weapon and wanted_weapon_id > 0:
            return T.hint(f"没有找到武器 {weapon_id_label(wanted_weapon_id)}。", "发送：武器 查看自己的武器列表。<武器>")
        if not weapon:
            return T.hint("没有找到可查看的武器。", "发送：武器 查看自己的武器列表。<武器>")

        panel = T.panel()
        panel.section("武器详情")
        panel.lines(self._weapon_detail_lines(client_id, player, weapon, len(rows)))
        return panel.render()

    def legend(self, client_id: str, message: str) -> str:
        """查看一把武器的完整传奇记录。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.ensure_starter_weapon(client_id)
        weapon_id = parse_weapon_ref(message)
        if weapon_id <= 0:
            return T.hint("缺少武器 ID。", "发送：武器 查看编号，再发送：武器传奇 武器ID<武器>")
        weapon = self.weapon(client_id, weapon_id)
        if not weapon:
            return T.hint(f"没有找到武器 {weapon_id_label(weapon_id)}。", "发送：武器 查看自己的武器列表。<武器>")
        with self.db.transaction() as conn:
            self.record_weapon_created_conn(conn, client_id, weapon_id)
        legend = self.db.fetch_one("SELECT * FROM weapon_legends WHERE weapon_id = ?", (weapon_id,))
        weapon["legend"] = dict(legend) if legend else {}
        return self._weapon_legend_text(weapon)

    def switch(self, client_id: str, message: str) -> str:
        """切换武器。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        weapon_id = parse_weapon_ref(message)
        weapon = self.weapon(client_id, weapon_id)
        if not weapon:
            return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
        with self.db.transaction() as conn:
            conn.execute("UPDATE player_weapons SET equipped = 0 WHERE holder_id = ?", (client_id,))
            conn.execute("UPDATE player_weapons SET equipped = 1 WHERE holder_id = ? AND weapon_id = ?", (client_id, weapon_id))
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '切换武器', ?, ?)",
                (client_id, f"weapon_id={weapon_id}, name={weapon_label_name(weapon)}", ts()),
            )
        return f"已切换武器：{weapon_label_name(weapon)}。"

    def upgrade(self, client_id: str, message: str) -> str:
        """升级武器；只受武器自身等级上限限制，不受玩家等级限制。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.ensure_starter_weapon(client_id)
        weapon_id = parse_weapon_ref(message)
        if weapon_id <= 0 and message.strip():
            return T.hint("升级武器格式不正确。", "发送：升级武器，或发送：升级武器 武器ID，例如：升级武器 武器#1")
        with self.db.transaction() as conn:
            if weapon_id <= 0:
                equipped = conn.execute(
                    """
                    SELECT weapon_id
                    FROM player_weapons
                    WHERE holder_id = ? AND equipped = 1
                    ORDER BY weapon_id
                    LIMIT 1
                    """,
                    (client_id,),
                ).fetchone()
                weapon_id = int(equipped["weapon_id"]) if equipped else 0
            weapon = conn.execute(
                """
                SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到可升级的武器。", "发送：武器 查看自己的武器列表。<武器>")
            max_level = int(weapon["max_level"])
            current_exp = min(max(0, int(weapon["exp"] or 0)), weapon_exp_for_level(max_level))
            current_level = weapon_level_from_exp(current_exp, max_level)
            if current_level >= max_level:
                return T.hint("这把武器已经到达自身等级上限。", "可以切换或继续探险获取更高上限武器。")
            next_level = current_level + 1
            cost = weapon_upgrade_cost(next_level, quality_factor(weapon["quality"]))
            if not self.spend_stones_conn(conn, client_id, cost):
                return T.hint(f"源石不足，升级需要 {money(cost)}。", "发送：源库 查看存量，或通过签到、探险、出售物品获取源石。<签到><探险>")
            target_exp = weapon_exp_for_level(next_level)
            exp_gain = max(0, target_exp - current_exp)
            next_weapon = dict(weapon)
            next_weapon["exp"] = target_exp
            next_weapon["level"] = next_level
            slots = computed_weapon_enchant_slots(next_weapon)
            conn.execute(
                """
                UPDATE player_weapons
                SET level = ?, exp = ?
                WHERE weapon_id = ? AND holder_id = ?
                """,
                (next_level, next_weapon["exp"], weapon_id, client_id),
            )
            attack = computed_weapon_attack(next_weapon)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '升级武器', ?, ?)",
                (
                    client_id,
                    (
                        f"weapon_id={weapon_id}, level={next_level}, exp={next_weapon['exp']}, "
                        f"exp_gain={exp_gain}, attack={attack}, cost={cost}, slots={slots}"
                    ),
                    ts(),
                ),
            )
        return (
            f"升级成功，{weapon_label_name(weapon)} 等级 {next_level}/{max_level}，"
            f"经验补满 +{exp_gain}，攻击 {attack}，附魔栏 {slots}。"
        )

    @staticmethod
    def _resolve_default_weapon(rows: list[dict], message: str) -> dict | None:
        """空参数时默认当前装备武器，有参数时按 ID 精确查找。"""

        if not rows:
            return None
        text = message.strip()
        if not text:
            return next((row for row in rows if int(row["equipped"])), rows[0])
        weapon_id = parse_weapon_ref(text)
        if weapon_id <= 0:
            return None
        return next((row for row in rows if int(row["weapon_id"]) == weapon_id), None)

    def enchant(self, client_id: str, message: str) -> str:
        """给武器附魔。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        if len(parts) < 2:
            return T.hint("附魔格式不正确。", "发送：附魔武器 武器ID 技能书名，例如：附魔武器 武器#1 破甲残卷")
        weapon_id, book_name = self._split_weapon_ref_and_tail(parts)
        if weapon_id <= 0 or not book_name:
            return T.hint("附魔格式不正确。", "发送：附魔武器 武器ID 技能书名，例如：附魔武器 武器#1 破甲残卷")
        book = self.ring_item_def_by_name(book_name)
        if not book or book["category"] != "技能书":
            return T.hint(f"没有找到技能书：{book_name}。", "发送：纳戒 查看已有技能书。<纳戒>")
        effect = load_json(book["effect"], {})
        enchant_id = effect.get("enchant_id")
        base_enchant_id = str(effect.get("base_enchant_id") or enchant_id or "")
        enchant = self.db.fetch_one("SELECT * FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
        if not enchant:
            return T.hint("这本技能书暂时不能附魔。", "换一本技能书，或发送：查看修仙物品 技能书名 查看说明。")
        with self.db.transaction() as conn:
            weapon = conn.execute(
                """
                SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
            current = load_json(weapon["enchant_effects"], [])
            if not isinstance(current, list):
                current = []
            current_bases = self._base_enchant_ids(current)
            if base_enchant_id in current_bases:
                return T.hint(
                    f"这把武器已经附魔过《{self._enchant_base_name(base_enchant_id)}》，同模板技能书不能重复附魔。",
                    "可以选择同流派的其他技能书继续组合。",
                )
            if len(current) >= computed_weapon_enchant_slots(weapon):
                return T.hint("这把武器没有空余附魔栏。", "升级武器可能解锁附魔栏，或换一把更高上限武器。")
            if not self.remove_ring_conn(conn, client_id, book["ring_item_id"], 1):
                return T.hint(f"纳戒里没有 {book['name']}。", "发送：纳戒 确认库存，或继续探险获取技能书。<纳戒>")
            current.append(enchant_id)
            conn.execute(
                "UPDATE player_weapons SET enchant_effects = ? WHERE weapon_id = ? AND holder_id = ?",
                (dump_json(current), weapon_id, client_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '附魔武器', ?, ?)",
                (client_id, f"weapon_id={weapon_id}, book={book['ring_item_id']}, enchant={enchant_id}", ts()),
            )
        return f"附魔成功：{weapon_label_name(weapon)} 获得 {book['name']}。"

    @staticmethod
    def _split_weapon_ref_and_tail(parts: list[str]) -> tuple[int, str]:
        """解析 武器#12 名称 / #12 名称 / 武器 12 名称 / 12 名称。"""

        if not parts:
            return 0, ""
        if parts[0] in {"武器", "武器ID"} and len(parts) >= 2:
            return parse_weapon_ref(parts[1]), " ".join(parts[2:]).strip()
        return parse_weapon_ref(parts[0]), " ".join(parts[1:]).strip()

    def _base_enchant_ids(self, enchant_ids: list[object]) -> set[str]:
        """把普通/极版附魔都折算成基础模板，避免极版绕过同书判重。"""

        result: set[str] = set()
        for enchant_id in enchant_ids:
            value = str(enchant_id)
            base_id = value
            for row in self.db.fetch_all("SELECT effect FROM ring_item_defs WHERE category = '技能书'"):
                effect = load_json(row["effect"], {})
                if str(effect.get("enchant_id") or "") == value:
                    base_id = str(effect.get("base_enchant_id") or value)
                    break
            result.add(base_id)
        return result

    def _enchant_base_name(self, base_enchant_id: str) -> str:
        """读取附魔基础模板名，提示里尽量说人话。"""

        row = self.db.fetch_one("SELECT name FROM weapon_enchants WHERE enchant_id = ?", (base_enchant_id,))
        return row["name"] if row else base_enchant_id

    def _enchant_text(self, weapon_id: int, enchant_ids: object) -> str:
        """把武器已附魔技能书按槽位展示出来。"""

        if not isinstance(enchant_ids, list) or not enchant_ids:
            return ""
        custom_rows = self.db.fetch_all(
            "SELECT slot_no, custom_name FROM weapon_enchant_names WHERE weapon_id = ?",
            (weapon_id,),
        )
        custom_names = {int(row["slot_no"]): row["custom_name"] for row in custom_rows}
        labels = []
        for slot_no, enchant_id in enumerate(enchant_ids, start=1):
            row = self.db.fetch_one("SELECT name FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
            base_name = row["name"] if row else str(enchant_id)
            labels.append(f"{slot_no}.{enchant_label_name(base_name, custom_names.get(slot_no, ''))}")
        return "（" + "、".join(labels) + "）"

    def _weapon_summary_text(self, weapon: dict) -> str:
        """格式化武器简表中的一行。"""

        mark = "已装备" if int(weapon["equipped"]) else "备用"
        skill = self.skill(weapon["skill_id"])
        skill_name = self.weapon_skill_label(int(weapon["weapon_id"]), skill)
        enchant_ids = load_json(weapon["enchant_effects"], [])
        if not isinstance(enchant_ids, list):
            enchant_ids = []
        return (
            f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}[{weapon['quality']}] {mark} "
            f"等级:{weapon['level']}/{weapon['max_level']} 经验:{self._exp_progress_text(weapon)} 攻击:{computed_weapon_attack(weapon)} "
            f"技能:{skill_name} 附魔:{len(enchant_ids)}/{computed_weapon_enchant_slots(weapon)}"
            f"{self._enchant_text(int(weapon['weapon_id']), enchant_ids)}"
        )

    def _weapon_detail_lines(self, client_id: str, player: dict, weapon: dict, total_count: int) -> list[str]:
        """格式化一把武器的完整展示。"""

        skill = self.skill(weapon["skill_id"])
        enchant_ids = load_json(weapon["enchant_effects"], [])
        if not isinstance(enchant_ids, list):
            enchant_ids = []
        effects = self._merge_effects(self.equipment_bonuses(client_id), self.weapon_effects_from_ids(enchant_ids))
        combat_info = self.combat_profile(int(player["level"]), weapon, skill, effects)
        used_slots = len(enchant_ids)
        unlocked_slots = computed_weapon_enchant_slots(weapon)
        potential_slots = computed_weapon_potential_slots(weapon)
        status = "已装备" if int(weapon["equipped"]) else "备用"
        next_cost = self._next_upgrade_text(weapon)
        recycle_text = self._recycle_state_text(weapon, total_count)
        legend_text = self._legend_text(int(weapon["weapon_id"]))
        return [
            f"{weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}[{weapon['quality']}] {status}",
            (
                f"模板：{weapon['name']}｜类型：{weapon['weapon_type']}｜"
                f"掉落：{weapon['drop_location']}｜模板基础攻击：{weapon['base_attack']}"
            ),
            f"定位：{combat_info['weapon_style']}",
            f"速度：**{combat_info['speed']}**（{combat_info['speed_grade']}）",
            f"技能节奏：{combat_info['skill_tempo']}",
            f"蓄势基准：{combat_info['skill_interval']}（越小越快）",
            (
                f"成长：等级 {weapon['level']}/{weapon['max_level']}｜"
                f"经验 {self._exp_progress_text(weapon)}｜当前攻击 {computed_weapon_attack(weapon)}｜下级：{next_cost}"
            ),
            "自带技能：" + self._skill_text(skill, weapon, effects),
            (
                f"附魔栏：已用 {used_slots}/已解锁 {unlocked_slots}/潜力 {potential_slots}；"
                f"{self._next_slot_text(weapon, unlocked_slots, potential_slots)}"
            ),
            "附魔：" + self._enchant_detail_text(int(weapon["weapon_id"]), enchant_ids),
            f"传奇：{legend_text}",
            f"回收：{recycle_text}",
        ]

    def _legend_text(self, weapon_id: int) -> str:
        """展示武器轻量传奇记录。"""

        row = self.db.fetch_one("SELECT * FROM weapon_legends WHERE weapon_id = ?", (weapon_id,))
        if not row:
            return "暂无记录"
        return (
            f"斩怪{row['monster_kills']} Boss{row['boss_challenges']} "
            f"决斗胜{row['duel_wins']} 最高伤害{row['highest_damage']}"
        )

    def _weapon_legend_text(self, weapon: dict) -> str:
        """格式化完整武器传奇记录。"""

        legend = weapon.get("legend") or {}
        fallback_holder = weapon.get("holder_id", "")
        original = self.format_player_name(legend.get("original_owner_id", fallback_holder))
        current = self.format_player_name(legend.get("current_owner_id", fallback_holder))
        panel = T.panel()
        panel.section(f"武器传奇 {weapon_id_label(weapon['weapon_id'])} {weapon_label_name(weapon)}")
        panel.line(f"品质：{weapon['quality']}｜类型：{weapon['weapon_type']}｜等级：**{weapon['level']}/{weapon['max_level']}**")
        panel.line(f"武器经验：**{self._exp_progress_text(weapon)}**")
        panel.line(f"初主：{original}｜现主：{current}")
        panel.line(
            f"斩怪：**{legend.get('monster_kills', 0)}** 次｜"
            f"Boss：**{legend.get('boss_challenges', 0)}** 战｜"
            f"决斗胜：**{legend.get('duel_wins', 0)}** 场"
        )
        panel.line(f"最高单次伤害：**{legend.get('highest_damage', 0)}**")
        return panel.render()

    @staticmethod
    def _next_upgrade_text(weapon: dict) -> str:
        """展示下一次升级花费。"""

        if int(weapon["level"]) >= int(weapon["max_level"]):
            return "已到上限"
        next_level = int(weapon["level"]) + 1
        cost = weapon_upgrade_cost(next_level, quality_factor(weapon["quality"]))
        current, need = weapon_exp_progress(int(weapon["exp"]), int(weapon["level"]), int(weapon["max_level"]))
        left = max(0, need - current)
        return f"{next_level}级需源石{money(cost)}，补经验{left}"

    @staticmethod
    def _exp_progress_text(weapon: dict) -> str:
        """展示武器当前等级内的经验进度。"""

        exp = int(weapon["exp"])
        current, need = weapon_exp_progress(exp, int(weapon["level"]), int(weapon["max_level"]))
        if need <= 0:
            return "已满级"
        return f"{current}/{need}"

    @staticmethod
    def _recycle_state_text(weapon: dict, total_count: int) -> str:
        """展示当前武器是否能通过商场统一入口出售。"""

        if int(weapon["equipped"]):
            return "已装备，不能出售；先切换到其他武器"
        if total_count <= 1:
            return "最后一把武器，不能出售"
        return "可发送：出售全部 武器；或发送：出售 武器ID 1"

    @staticmethod
    def _next_slot_text(weapon: dict, unlocked_slots: int, potential_slots: int) -> str:
        """展示下一格附魔栏的解锁条件。"""

        if potential_slots <= 0:
            return "这把武器等级上限不足，无法解锁附魔栏"
        if unlocked_slots >= potential_slots:
            return "附魔栏已按当前等级上限全部解锁"
        for level in (10, 25, 40, 60, 80, 95, 100):
            if int(weapon["max_level"]) >= level and int(weapon["level"]) < level:
                return f"下个附魔栏在武器{level}级解锁"
        return "继续升级可解锁更多附魔栏"

    def _skill_text(self, skill: dict, weapon: dict, effects: dict[str, float]) -> str:
        """展示武器自带技能本身，不混入速度面板。"""

        cost = self._skill_cost(skill, effects)
        power = self._skill_power(skill, effects)
        name = self.weapon_skill_label(int(weapon["weapon_id"]), skill)
        desc = skill.get("effect_desc") or skill.get("desc", "")
        text = f"{name} | 威力{power:.2f}倍 | 消耗精神{cost}"
        return f"{text} | {desc}" if desc else text

    def _enchant_detail_text(self, weapon_id: int, enchant_ids: list[str]) -> str:
        """展示附魔名称和效果。"""

        if not enchant_ids:
            return "无"
        custom_rows = self.db.fetch_all(
            "SELECT slot_no, custom_name FROM weapon_enchant_names WHERE weapon_id = ?",
            (weapon_id,),
        )
        custom_names = {int(row["slot_no"]): row["custom_name"] for row in custom_rows}
        lines = []
        for slot_no, enchant_id in enumerate(enchant_ids, start=1):
            row = self.db.fetch_one("SELECT name, effect, mp_delta FROM weapon_enchants WHERE enchant_id = ?", (enchant_id,))
            if not row:
                lines.append(f"{slot_no}.{enchant_id}")
                continue
            name = enchant_label_name(row["name"], custom_names.get(slot_no, ""))
            effect_text = self._effect_text(load_json(row["effect"], {}), int(row["mp_delta"]))
            lines.append(f"{slot_no}.{name}({effect_text})")
        return "；".join(lines)

    @staticmethod
    def _effect_text(effect: dict, mp_delta: int) -> str:
        """把附魔效果翻译成玩家可读文本。"""

        labels = {
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
            "dodge_bonus": "闪避",
            "burn_rate": "灼烧",
            "bleed_rate": "流血",
            "stun_rate": "行动条压制",
        }
        texts = []
        for key, label in labels.items():
            value = effect.get(key)
            if isinstance(value, int | float) and value:
                texts.append(f"{label}{value * 100:+.1f}%")
        interval_delta = effect.get("interval_delta")
        if isinstance(interval_delta, int | float) and interval_delta:
            if interval_delta > 0:
                texts.append(f"技能间隔+{int(interval_delta)}")
            else:
                texts.append(f"技能间隔{int(interval_delta)}")
        if mp_delta:
            texts.append(f"精神消耗{mp_delta:+d}")
        return "、".join(texts) if texts else "无数值效果"

service = WeaponService(db)

__all__ = ["WeaponService", "service"]
