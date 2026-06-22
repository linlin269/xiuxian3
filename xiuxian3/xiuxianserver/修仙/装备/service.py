"""装备组件服务。"""

from __future__ import annotations

from ..format_text import T

from ..common import (
    CoreService,
    fixed_equipment_label,
    money,
    parse_name_level,
    split_words,
    to_int,
    ts,
)
from ..constants import (
    EQUIPMENT_DEFAULT_HOLES,
    EQUIPMENT_MAX_HOLES,
    EQUIPMENT_SLOTS,
    FIXED_EQUIPMENT_SLOT_FACTORS,
)
from ..rules import (
    equipment_upgrade_cost,
    gem_upgrade_cost,
)
from ..sql import db


class EquipmentService(CoreService):
    """装备升级和镶嵌。"""

    def list_equipment(self, client_id: str) -> str:
        """查看装备。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.db.ensure_fixed_equipment(client_id)
        rows = self.db.fetch_all(
            "SELECT * FROM fixed_equipment WHERE client_id = ? ORDER BY slot",
            (client_id,),
        )
        bonuses = self.equipment_bonuses(client_id)
        panel = T.panel()
        panel.section("装备")
        for row in rows:
            panel.line(f"{fixed_equipment_label(row)}｜**{row['level']}** 级｜孔位 **{row['hole_count']}/{EQUIPMENT_MAX_HOLES}**")
        panel.hr()
        panel.section("总加成")
        panel.line(f"血气 +**{int(bonuses['max_hp_bonus'])}**｜" f"精神 +**{int(bonuses['max_mp_bonus'])}**｜" f"防御 +**{int(bonuses['defense_bonus'])}**")
        return panel.render() + "<升 左手><升 右手><升 左脚><升 右脚>" + "<升 头部><升 护甲><升 饰品>"

    def upgrade(self, client_id: str, slot: str) -> str:
        """升级装备。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        slot = slot.strip()
        if not slot:
            return T.hint("装备升级需要指定装备位。", f"发送：装备升级 装备位，例如：装备升级 护甲。可选：{'、'.join(EQUIPMENT_SLOTS)}。<装备>")
        if slot not in EQUIPMENT_SLOTS:
            return T.hint(f"装备位只能是：{'、'.join(EQUIPMENT_SLOTS)}", "发送：装备 查看已有装备位。<装备>")
        self.db.ensure_fixed_equipment(client_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM fixed_equipment WHERE client_id = ? AND slot = ?",
                (client_id, slot),
            ).fetchone()
            level = row["level"] if row else 0
            if level >= 100:
                return T.hint(f"{slot} 已满级。", "可以升级其他装备位，或继续镶嵌、升级宝石。")
            cost = equipment_upgrade_cost(level + 1, FIXED_EQUIPMENT_SLOT_FACTORS[slot])
            if not self.spend_stones_conn(conn, client_id, cost):
                return T.hint(f"源石不足，升级需要 {money(cost)}。", "发送：源库 查看存量，或通过签到、探险、出售物品获取源石。<自动出售>")
            conn.execute(
                "UPDATE fixed_equipment SET level = level + 1 WHERE client_id = ? AND slot = ?",
                (client_id, slot),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '升级装备', ?, ?)",
                (client_id, f"slot={slot}, level={level + 1}, cost={cost}", ts()),
            )
        self.recalc_player(client_id)
        return f"{fixed_equipment_label(row) if row else slot} 升级成功，当前 {level + 1} 级。"

    def holes(self, client_id: str, slot: str) -> str:
        """查看装备孔位。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        slot = slot.strip()
        if not slot:
            return self._holes_overview(client_id)
        if slot not in EQUIPMENT_SLOTS:
            return T.hint(f"装备位只能是：{'、'.join(EQUIPMENT_SLOTS)}", "发送：装备 查看已有装备位。<装备>")
        equipment = self._equipment_row(client_id, slot)
        hole_count = int(equipment["hole_count"]) if equipment else EQUIPMENT_DEFAULT_HOLES
        rows = self.db.fetch_all(
            """
            SELECT i.hole_no, i.level, e.name
            FROM fixed_equipment_inlays i
            LEFT JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
            WHERE i.client_id = ? AND i.slot = ?
            ORDER BY i.hole_no
            """,
            (client_id, slot),
        )
        used = {row["hole_no"]: f"{row['name']} {row['level']}级" for row in rows}
        panel = T.panel()
        panel.section(f"{fixed_equipment_label(equipment) if equipment else slot}孔位")
        panel.line(f"孔位：**{hole_count}/{EQUIPMENT_MAX_HOLES}**")
        for index in range(1, EQUIPMENT_MAX_HOLES + 1):
            if index > hole_count:
                panel.line(f"{index}：未开孔")
            else:
                panel.line(f"{index}：{used.get(index, '空')}")
        return panel.render() + (
            f"<宝石升级 {slot} 1:升级1孔><宝石升级 {slot} 2:升级2孔><宝石升级 {slot} 3:升级3孔>"
            + f"<宝石升级 {slot} 4:升级4孔><宝石升级 {slot} 5:升级5孔><宝石升级 {slot} 6:升级6孔>"
            + f"<宝石升级 {slot} 7:升级7孔><宝石升级 {slot} 8:升级8孔><宝石升级 {slot} 9:升级9孔>"
        )

    def _holes_overview(self, client_id: str) -> str:
        """查看七件装备的全部孔位。"""

        self.db.ensure_fixed_equipment(client_id)
        equipment_rows = self.db.fetch_all(
            "SELECT * FROM fixed_equipment WHERE client_id = ?",
            (client_id,),
        )
        inlay_rows = self.db.fetch_all(
            """
            SELECT i.slot, i.hole_no, i.level, e.name
            FROM fixed_equipment_inlays i
            LEFT JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
            WHERE i.client_id = ?
            ORDER BY i.slot, i.hole_no
            """,
            (client_id,),
        )
        equipment_by_slot = {row["slot"]: row for row in equipment_rows}
        gems_by_slot: dict[str, dict[int, str]] = {}
        for row in inlay_rows:
            gems_by_slot.setdefault(row["slot"], {})[int(row["hole_no"])] = f"{row['name']} {row['level']}级"

        panel = T.panel()
        panel.section("装备孔位总览")
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            equipment = equipment_by_slot.get(slot)
            hole_count = int(equipment["hole_count"]) if equipment else EQUIPMENT_DEFAULT_HOLES
            level = int(equipment["level"]) if equipment else 0
            if index:
                panel.blank()
            panel.section(f"{fixed_equipment_label(equipment) if equipment else slot}｜Lv{level}｜{hole_count}/{EQUIPMENT_MAX_HOLES}孔")
            gems = gems_by_slot.get(slot, {})
            panel.line(self._hole_row(1, hole_count, gems))
            panel.line(self._hole_row(4, hole_count, gems))
            panel.line(self._hole_row(7, hole_count, gems))

        bonuses = self.equipment_bonuses(client_id)
        panel.hr()
        panel.section("总加成")
        panel.line(f"血气 +**{int(bonuses['max_hp_bonus'])}**｜" f"精神 +**{int(bonuses['max_mp_bonus'])}**｜" f"防御 +**{int(bonuses['defense_bonus'])}**")
        return panel.render()

    def _hole_row(self, start: int, hole_count: int, gems: dict[int, str]) -> str:
        """格式化 3 个孔位为一行。"""

        return "｜".join(self._hole_text(index, hole_count, gems) for index in range(start, start + 3))

    def _hole_text(self, index: int, hole_count: int, gems: dict[int, str]) -> str:
        """格式化单个孔位状态。"""

        marks = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨")
        if index > hole_count:
            return f"{marks[index - 1]} 未开孔"
        return f"{marks[index - 1]} {gems.get(index, '空')}"

    def inlay(self, client_id: str, message: str) -> str:
        """镶嵌装备。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        if len(parts) < 3:
            return T.hint("镶嵌格式不正确。", "发送：镶嵌 装备位 孔位号 宝石名称，例如：镶嵌 护甲 1 护心玉")
        slot = parts[0]
        hole_no = to_int(parts[1])
        item_name, wanted_level = parse_name_level(" ".join(parts[2:]))
        if slot not in EQUIPMENT_SLOTS or hole_no < 1 or hole_no > EQUIPMENT_MAX_HOLES:
            return T.hint("装备位或孔位号不正确。", f"装备位只能是：{'、'.join(EQUIPMENT_SLOTS)}；孔位号只能是 1 到 {EQUIPMENT_MAX_HOLES}。")
        item = self.ring_item_def_by_name(item_name)
        if not item or item["category"] != "宝石":
            return T.hint(f"没有找到宝石：{item_name}。", "发送：宝石 查看已有宝石名称。<宝石>")
        with self.db.transaction() as conn:
            equipment = conn.execute(
                "SELECT hole_count FROM fixed_equipment WHERE client_id = ? AND slot = ?",
                (client_id, slot),
            ).fetchone()
            hole_count = int(equipment["hole_count"]) if equipment else EQUIPMENT_DEFAULT_HOLES
            if hole_no > hole_count:
                return T.hint(f"{slot} 当前只开启到 {hole_count} 号孔。", "先发送：开孔 装备位，消耗开孔器后再镶嵌。")
            exists = conn.execute(
                """
                SELECT 1 FROM fixed_equipment_inlays
                WHERE client_id = ? AND slot = ? AND hole_no = ?
                """,
                (client_id, slot, hole_no),
            ).fetchone()
            if exists:
                return T.hint("该孔位已经有宝石。", "发送：拆卸 装备位 孔位号 后再重新镶嵌。")
            gem_level, level_error = self.resolve_gem_level_conn(
                conn,
                client_id,
                item["ring_item_id"],
                item["name"],
                wanted_level,
                "镶嵌 护甲 1 {name} {level}级",
            )
            if level_error:
                return level_error
            assert gem_level is not None
            if not self.remove_gem_conn(conn, client_id, item["ring_item_id"], gem_level, 1):
                return T.hint(f"纳戒里没有 {item['name']} {gem_level}级。", "发送：宝石 查看已有宝石等级，或继续探险获取。<宝石><探险>")
            conn.execute(
                """
                INSERT INTO fixed_equipment_inlays (client_id, slot, hole_no, gem_id, level)
                VALUES (?, ?, ?, ?, ?)
                """,
                (client_id, slot, hole_no, item["ring_item_id"], gem_level),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '镶嵌宝石', ?, ?)",
                (client_id, f"slot={slot}, hole={hole_no}, gem={item['ring_item_id']}, level={gem_level}", ts()),
            )
        self.recalc_player(client_id)
        equipment = self._equipment_row(client_id, slot)
        return f"镶嵌成功：{fixed_equipment_label(equipment) if equipment else slot} {hole_no}号孔 → {item['name']} {gem_level}级。"

    def remove_inlay(self, client_id: str, message: str) -> str:
        """拆卸宝石。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        if len(parts) < 2:
            return T.hint("拆卸格式不正确。", "发送：拆卸 装备位 孔位号，例如：拆卸 护甲 1")
        slot = parts[0]
        hole_no = to_int(parts[1])
        with self.db.transaction() as conn:
            row = conn.execute(
                """
                SELECT i.*, e.name
                FROM fixed_equipment_inlays i
                JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
                WHERE i.client_id = ? AND i.slot = ? AND i.hole_no = ?
                """,
                (client_id, slot, hole_no),
            ).fetchone()
            if not row:
                return T.hint("该孔位没有宝石。", "发送：孔位 装备位 查看当前孔位。")
            self.add_gem_conn(conn, client_id, row["gem_id"], row["level"], 1)
            conn.execute(
                "DELETE FROM fixed_equipment_inlays WHERE client_id = ? AND slot = ? AND hole_no = ?",
                (client_id, slot, hole_no),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '拆卸宝石', ?, ?)",
                (client_id, f"slot={slot}, hole={hole_no}, gem={row['gem_id']}, level={row['level']}", ts()),
            )
        self.recalc_player(client_id)
        return f"拆卸成功：{row['name']} {row['level']}级已回到纳戒。"

    def my_inlays(self, client_id: str) -> str:
        """查看纳戒里尚未镶嵌的宝石库存。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.gem_rows(client_id)
        if not rows:
            return T.hint("纳戒中没有宝石。", "继续探险有概率获得宝石。")
        panel = T.panel()
        panel.section("宝石")
        for row in rows:
            panel.line(f"{row['name']} {row['level']}级｜x{row['quantity']}")
        return panel.render()

    def upgrade_inlay(self, client_id: str, message: str) -> str:
        """升级已镶嵌的宝石。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        text = message.strip()
        if not text:
            return T.hint("宝石升级格式不正确。", "发送：宝石升级 装备位 孔位号，例如：宝石升级 护甲 1")
        with self.db.transaction() as conn:
            row, error = self._upgrade_target_conn(conn, client_id, text)
            if error:
                return error
            assert row is not None
            if row["level"] >= 10:
                return T.hint(f"{row['name']} 已经 10 级。", "可以升级其他宝石，或镶嵌到其他装备孔位。")
            next_level = row["level"] + 1
            cost = gem_upgrade_cost(next_level)
            if not self.spend_stones_conn(conn, client_id, cost):
                return T.hint(f"源石不足，升级需要 {money(cost)}。", "发送：源库 查看存量，或通过签到、探险、出售物品获取源石。<自动出售>")
            conn.execute(
                """
                UPDATE fixed_equipment_inlays
                SET level = ?
                WHERE client_id = ? AND slot = ? AND hole_no = ?
                """,
                (next_level, client_id, row["slot"], row["hole_no"]),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '升级宝石', ?, ?)",
                (
                    client_id,
                    f"slot={row['slot']}, hole={row['hole_no']}, gem={row['gem_id']}, level={next_level}, cost={cost}",
                    ts(),
                ),
            )
        self.recalc_player(client_id)
        return f"{row['slot']} {row['hole_no']}号孔 {row['name']} 升级成功：{row['level']} → {next_level}，消耗源石 {money(cost)}。"

    def _upgrade_target_conn(self, conn, client_id: str, text: str):
        """解析宝石升级目标；只按 装备位+孔位 精确定位。"""

        parts = split_words(text)
        if len(parts) >= 2 and parts[0] in EQUIPMENT_SLOTS:
            hole_no = to_int(parts[1])
            row = conn.execute(
                """
                SELECT i.*, e.name
                FROM fixed_equipment_inlays i
                JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
                WHERE i.client_id = ? AND i.slot = ? AND i.hole_no = ?
                """,
                (client_id, parts[0], hole_no),
            ).fetchone()
            if not row:
                return None, T.hint("该孔位没有可升级宝石。", "发送：孔位 装备位 查看当前孔位。")
            return row, None

        item = self.ring_item_def_by_name(text)
        if not item or item["category"] != "宝石":
            return None, T.hint("宝石升级格式不正确。", "发送：宝石升级 装备位 孔位号，例如：宝石升级 护甲 1")
        rows = conn.execute(
            """
            SELECT i.*, e.name
            FROM fixed_equipment_inlays i
            JOIN ring_item_defs e ON e.ring_item_id = i.gem_id
            WHERE i.client_id = ? AND i.gem_id = ?
            ORDER BY i.slot, i.hole_no
            """,
            (client_id, item["ring_item_id"]),
        ).fetchall()
        if not rows:
            return None, T.hint(f"你还没有镶嵌 {item['name']}。", "先发送：镶嵌 装备位 孔位号 宝石名称。")
        options = "、".join(f"{row['slot']}{row['hole_no']}号孔({row['level']}级)" for row in rows)
        return None, T.hint(
            "宝石升级需要用装备位和孔位号定位。",
            f"发送：宝石升级 装备位 孔位号，例如：宝石升级 {rows[0]['slot']} {rows[0]['hole_no']}。可选：{options}",
        )

    def _equipment_row(self, client_id: str, slot: str) -> dict | None:
        """读取某个装备位。"""

        self.db.ensure_fixed_equipment(client_id)
        return self.db.fetch_one(
            "SELECT * FROM fixed_equipment WHERE client_id = ? AND slot = ?",
            (client_id, slot),
        )


service = EquipmentService(db)

__all__ = ["EquipmentService", "service"]
