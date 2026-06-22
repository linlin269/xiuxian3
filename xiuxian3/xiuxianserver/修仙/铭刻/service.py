"""铭刻组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    CoreService,
    custom_label,
    enchant_label_name,
    load_json,
    parse_weapon_ref,
    split_words,
    to_int,
    ts,
    weapon_label_name,
)
from ..constants import EQUIPMENT_SLOTS
from ..sql import db


MAX_INSCRIPTION_NAME_LENGTH = 12


class InscriptionService(CoreService):
    """消耗铭刻之羽，为自己的装备显示名做个性化。"""

    def guide(self, client_id: str) -> str:
        """查看铭刻格式。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        panel = T.panel()
        panel.section("铭刻")
        panel.line("铭刻 装备 头部 新名字")
        panel.line("铭刻 武器 武器#12 新名字")
        panel.line("铭刻 技能 武器#12 新名字")
        panel.line("铭刻 附魔 武器#12 1 新名字")
        panel.line("也可在末尾指定铭刻之羽编号，例如：铭刻武器 武器#12 青云剑 #1")
        panel.line("铭刻之羽只由岁时情劫首领产出，每枚都有自己的文案，铭刻后直接消散。")
        return T.attach(panel.render(), buttons=("岁时情劫", "铭刻之羽"))

    def feathers(self, client_id: str) -> str:
        """查看未使用的铭刻之羽。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all(
            """
            SELECT * FROM inscription_feathers
            WHERE client_id = ?
            ORDER BY feather_id
            """,
            (client_id,),
        )
        if not rows:
            return T.hint("你还没有铭刻之羽。", "铭刻之羽只能由岁时情劫首领产出，发送：首领 查看今日是否有岁时情劫。<首领>")
        panel = T.panel()
        panel.section("铭刻之羽")
        for row in rows:
            panel.line(f"〔{row['feather_id']}〕 {row['title']}")
            panel.line(row["flavor_text"])
            panel.blank()
        return panel.render()

    def dispatch(self, client_id: str, message: str) -> str:
        """按第一个参数分发到具体铭刻类型。"""

        parts = split_words(message)
        if not parts:
            return self.guide(client_id)

        target = parts[0]
        rest = " ".join(parts[1:])
        if target == "装备":
            return self.fixed_equipment(client_id, rest)
        if target == "武器":
            return self.weapon(client_id, rest)
        if target in {"附魔", "技能书"}:
            return self.enchant(client_id, rest)
        if target in {"技能", "武器技能", "自带技能"}:
            return self.skill_or_enchant(client_id, rest)
        return T.hint("铭刻目标不正确。", "发送：铭刻 装备/武器/技能/附魔 目标 新名字")

    def fixed_equipment(self, client_id: str, message: str) -> str:
        """铭刻装备：装备位 + 新名字。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        if len(parts) < 2:
            return T.hint("铭刻装备格式不正确。", "发送：铭刻 装备 头部 新名字")

        slot = parts[0]
        new_name_text, feather_id = self._split_feather_ref(" ".join(parts[1:]))
        new_name, name_error = self._clean_name(new_name_text)
        if name_error:
            return T.hint(name_error, "换一个 1 到 12 个字符、且不含空白的名字。")
        if slot not in EQUIPMENT_SLOTS:
            return T.hint(f"装备位只能是：{'、'.join(EQUIPMENT_SLOTS)}", "发送：装备 查看已有装备位。<装备>")
        if new_name == slot:
            return T.hint("新名字和原装备位一样。", "换一个更有辨识度的名字再铭刻。")

        self.db.ensure_fixed_equipment(client_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM fixed_equipment WHERE client_id = ? AND slot = ?",
                (client_id, slot),
            ).fetchone()
            if not row:
                return T.hint("没有找到这个装备位。", "发送：装备 查看已有装备位。<装备>")
            if row["custom_name"] == new_name:
                return T.hint("这个装备已经叫这个名字了。", "换一个新名字后再铭刻。")
            feather, feather_error = self._take_feather_conn(conn, client_id, feather_id)
            if feather_error:
                return feather_error
            conn.execute(
                "UPDATE fixed_equipment SET custom_name = ? WHERE client_id = ? AND slot = ?",
                (new_name, client_id, slot),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '铭刻装备', ?, ?)",
                (client_id, f"{slot}->{new_name},feather={feather['feather_id']}", ts()),
            )
        return self._success_text(custom_label(slot, new_name), feather)

    def weapon(self, client_id: str, message: str) -> str:
        """铭刻武器：武器实例 ID + 新名字。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        weapon_id, new_name, parse_error = self._parse_weapon_name(message)
        if parse_error:
            return T.hint(parse_error, "发送：铭刻 武器 武器#12 新名字")
        if weapon_id <= 0:
            return T.hint("武器 ID 不正确。", "发送：武器 查看自己的武器 ID。")
        new_name, feather_id = self._split_feather_ref(new_name)
        name, name_error = self._clean_name(new_name)
        if name_error:
            return T.hint(name_error, "换一个 1 到 12 个字符、且不含空白的名字。")

        with self.db.transaction() as conn:
            weapon = conn.execute(
                """
                SELECT w.*, d.name
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
            if name == weapon["name"]:
                return T.hint("新名字和武器原名一样。", "换一个更有辨识度的名字再铭刻。")
            if weapon["custom_name"] == name:
                return T.hint("这把武器已经叫这个名字了。", "换一个新名字后再铭刻。")
            feather, feather_error = self._take_feather_conn(conn, client_id, feather_id)
            if feather_error:
                return feather_error
            conn.execute(
                "UPDATE player_weapons SET custom_name = ? WHERE holder_id = ? AND weapon_id = ?",
                (name, client_id, weapon_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '铭刻武器', ?, ?)",
                (client_id, f"weapon={weapon_id},{weapon['name']}->{name},feather={feather['feather_id']}", ts()),
            )
        return self._success_text(custom_label(weapon["name"], name), feather)

    def enchant(self, client_id: str, message: str) -> str:
        """铭刻武器附魔：武器实例 ID + 附魔槽位序号 + 新名字。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        weapon_id, slot_no, new_name, parse_error = self._parse_enchant_name(message)
        if parse_error:
            return T.hint(parse_error, "发送：铭刻 附魔 武器#12 1 新名字")
        if weapon_id <= 0 or slot_no <= 0:
            return T.hint("武器 ID 或附魔序号不正确。", "发送：武器 查看自己的武器 ID 和附魔序号。<武器>")
        new_name, feather_id = self._split_feather_ref(new_name)
        name, name_error = self._clean_name(new_name)
        if name_error:
            return T.hint(name_error, "换一个 1 到 12 个字符、且不含空白的名字。")

        with self.db.transaction() as conn:
            weapon = conn.execute(
                """
                SELECT w.*, d.name
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
            enchant_ids = load_json(weapon["enchant_effects"], [])
            if not isinstance(enchant_ids, list) or slot_no < 1 or slot_no > len(enchant_ids):
                return T.hint("这把武器没有这个已附魔槽位。", "发送：武器 查看附魔序号后再铭刻。<武器>")
            enchant_id = enchant_ids[slot_no - 1]
            enchant = conn.execute(
                "SELECT name FROM weapon_enchants WHERE enchant_id = ?",
                (enchant_id,),
            ).fetchone()
            base_name = enchant["name"] if enchant else str(enchant_id)
            current = conn.execute(
                "SELECT custom_name FROM weapon_enchant_names WHERE weapon_id = ? AND slot_no = ?",
                (weapon_id, slot_no),
            ).fetchone()
            if name == base_name:
                return T.hint("新名字和附魔原名一样。", "换一个更有辨识度的名字再铭刻。")
            if current and current["custom_name"] == name:
                return T.hint("这个附魔已经叫这个名字了。", "换一个新名字后再铭刻。")
            feather, feather_error = self._take_feather_conn(conn, client_id, feather_id)
            if feather_error:
                return feather_error
            conn.execute(
                """
                INSERT INTO weapon_enchant_names (weapon_id, slot_no, custom_name)
                VALUES (?, ?, ?)
                ON CONFLICT(weapon_id, slot_no)
                DO UPDATE SET custom_name = excluded.custom_name
                """,
                (weapon_id, slot_no, name),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '铭刻附魔', ?, ?)",
                (client_id, f"weapon={weapon_id},slot={slot_no},{base_name}->{name},feather={feather['feather_id']}", ts()),
            )
        label = f"{weapon_label_name(weapon)}的{enchant_label_name(base_name, name)}"
        return self._success_text(label, feather)

    def skill_or_enchant(self, client_id: str, message: str) -> str:
        """铭刻技能入口：带槽位号时铭刻附魔，不带槽位号时铭刻自带技能。"""

        if self._message_has_enchant_slot(message):
            return self.enchant(client_id, message)
        return self.skill(client_id, message)

    def skill(self, client_id: str, message: str) -> str:
        """铭刻武器自带技能：武器实例 ID + 新名字。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        weapon_id, new_name, parse_error = self._parse_weapon_name(message)
        if parse_error:
            return T.hint(parse_error, "发送：铭刻 技能 武器#12 新名字<武器>")
        if weapon_id <= 0:
            return T.hint("武器 ID 不正确。", "发送：武器 查看自己的武器 ID。<武器>")
        new_name, feather_id = self._split_feather_ref(new_name)
        name, name_error = self._clean_name(new_name)
        if name_error:
            return T.hint(name_error, "换一个 1 到 12 个字符、且不含空白的名字。")

        with self.db.transaction() as conn:
            weapon = conn.execute(
                """
                SELECT w.*, d.name, s.name AS skill_name
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                LEFT JOIN weapon_skill_defs s ON s.skill_id = d.skill_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if not weapon:
                return T.hint("没有找到这把武器。", "发送：武器 查看自己的武器 ID。<武器>")
            base_name = weapon["skill_name"] or "普通攻击"
            current = conn.execute(
                "SELECT custom_name FROM weapon_enchant_names WHERE weapon_id = ? AND slot_no = 0",
                (weapon_id,),
            ).fetchone()
            if name == base_name:
                return T.hint("新名字和自带技能原名一样。", "换一个更有辨识度的名字再铭刻。")
            if current and current["custom_name"] == name:
                return T.hint("这个自带技能已经叫这个名字了。", "换一个新名字后再铭刻。")
            feather, feather_error = self._take_feather_conn(conn, client_id, feather_id)
            if feather_error:
                return feather_error
            conn.execute(
                """
                INSERT INTO weapon_enchant_names (weapon_id, slot_no, custom_name)
                VALUES (?, 0, ?)
                ON CONFLICT(weapon_id, slot_no)
                DO UPDATE SET custom_name = excluded.custom_name
                """,
                (weapon_id, name),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '铭刻自带技能', ?, ?)",
                (client_id, f"weapon={weapon_id},{base_name}->{name},feather={feather['feather_id']}", ts()),
            )
        label = f"{weapon_label_name(weapon)}的{enchant_label_name(base_name, name)}"
        return self._success_text(label, feather)

    @staticmethod
    def _split_feather_ref(text: str) -> tuple[str, int]:
        """从新名字末尾拆出可选的铭刻之羽编号。"""

        parts = split_words(text)
        if not parts:
            return "", 0
        last = parts[-1]
        if last.startswith("#") and last[1:].isdigit():
            return " ".join(parts[:-1]), to_int(last[1:])
        return text.strip(), 0

    @staticmethod
    def _take_feather_conn(conn, client_id: str, feather_id: int):
        """取走一枚铭刻之羽；铭刻成功后故事直接消散。"""

        if feather_id > 0:
            row = conn.execute(
                """
                SELECT * FROM inscription_feathers
                WHERE client_id = ? AND feather_id = ?
                """,
                (client_id, feather_id),
            ).fetchone()
            if not row:
                return None, T.hint("没有找到这枚铭刻之羽。", "发送：铭刻之羽 查看可用编号。")
        else:
            row = conn.execute(
                """
                SELECT * FROM inscription_feathers
                WHERE client_id = ?
                ORDER BY feather_id
                LIMIT 1
                """,
                (client_id,),
            ).fetchone()
            if not row:
                return None, T.hint("你还没有铭刻之羽。", "挑战岁时情劫首领后，可从首领奖励中获得。")

        conn.execute(
            "DELETE FROM inscription_feathers WHERE client_id = ? AND feather_id = ?",
            (client_id, row["feather_id"]),
        )
        return row, None

    @staticmethod
    def _feather_fade_text(feather) -> str:
        """铭刻成功后，展示这枚羽毛消散。"""

        return f"{feather['title']}散作微光，旧念已入其名。"

    def _success_text(self, label: str, feather) -> str:
        """铭刻成功文本统一使用 铭刻名（原名）。"""

        return f"铭刻成功：{label}。\n{self._feather_fade_text(feather)}"

    @staticmethod
    def _clean_name(name: str) -> tuple[str, str | None]:
        """校验铭刻名。"""

        clean = name.strip()
        if not clean:
            return "", "缺少新名字。"
        if len(clean) > MAX_INSCRIPTION_NAME_LENGTH:
            return "", f"名字不能超过 {MAX_INSCRIPTION_NAME_LENGTH} 个字符。"
        if any(ch.isspace() for ch in clean):
            return "", "名字中不能包含空白字符。"
        return clean, None

    @staticmethod
    def _parse_weapon_name(message: str) -> tuple[int, str, str | None]:
        """解析：武器#12 新名字 / 武器 12 新名字 / 12 新名字。"""

        parts = split_words(message)
        if len(parts) < 2:
            return 0, "", "武器铭刻格式不正确。"
        if parts[0] in {"武器", "武器ID"}:
            if len(parts) < 3:
                return 0, "", "武器铭刻格式不正确。"
            return parse_weapon_ref(parts[1]), " ".join(parts[2:]), None
        return parse_weapon_ref(parts[0]), " ".join(parts[1:]), None

    @staticmethod
    def _parse_enchant_name(message: str) -> tuple[int, int, str, str | None]:
        """解析：武器#12 1 新名字 / 武器 12 1 新名字 / 12 1 新名字。"""

        parts = split_words(message)
        if len(parts) < 3:
            return 0, 0, "", "附魔铭刻格式不正确。"
        if parts[0] in {"武器", "武器ID"}:
            if len(parts) < 4:
                return 0, 0, "", "附魔铭刻格式不正确。"
            return parse_weapon_ref(parts[1]), to_int(parts[2]), " ".join(parts[3:]), None
        return parse_weapon_ref(parts[0]), to_int(parts[1]), " ".join(parts[2:]), None

    @staticmethod
    def _message_has_enchant_slot(message: str) -> bool:
        """判断“铭刻技能”是否写了附魔槽位号。"""

        parts = split_words(message)
        if not parts:
            return False
        if parts[0] in {"武器", "武器ID"}:
            return len(parts) >= 3 and to_int(parts[2]) > 0
        return len(parts) >= 2 and to_int(parts[1]) > 0


service = InscriptionService(db)

__all__ = ["InscriptionService", "service"]
