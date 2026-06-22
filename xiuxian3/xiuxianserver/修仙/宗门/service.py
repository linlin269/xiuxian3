"""宗门组件服务。"""

from __future__ import annotations

from datetime import datetime, timedelta
import random as random_module
import sqlite3

from ..common import CoreService, split_words, to_int, ts, validate_name
from ..constants import WORLD_COORD_MAX, WORLD_COORD_MIN
from ..format_text import T
from ..sect_war import (
    SECT_WAR_REWARD_ITEM_ID,
    SECT_WAR_REWARD_ITEM_NAME,
    ensure_sect_stats_conn,
    sect_bonus_conn,
    sect_city_bonus_conn,
    sect_war_cycle_finished,
    sect_war_cycle_bounds,
    sect_war_display_cycle_end,
    sect_war_in_battle_window,
    sect_war_in_reward_claim_window,
    sect_war_is_member_locked,
    sect_war_member_lock_text,
    sect_war_personal_reward_count,
    sect_war_qualified_count,
    sect_war_reward_member_count,
    sect_war_robbery_influence,
    record_sect_robbery_influence_conn,
    SECT_WAR_REWARD_TYPE_PERSONAL_TOP,
    SECT_WAR_REWARD_TYPE_SECT_RANDOM,
)
from ..sql import db


class SectService(CoreService):
    """宗门创建、查看与加入。"""

    def overview(self, client_id: str) -> str:
        """查看自己宗门；无宗门时查看当前位置山门。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        sect = self._member_sect(client_id)
        if sect:
            return self._sect_panel(sect, client_id, joined=True)

        current_sect = self._sect_by_xy(int(player["x"]), int(player["y"]))
        if current_sect:
            return self._sect_panel(current_sect, client_id, joined=False)

        return T.hint(
            "你还没有宗门。",
            "需要先导航到某个宗门山门，再发送：加入宗门 宗门名；也可以建立自己的宗门。",
            buttons=("建立宗门", "地图"),
        )

    def join(self, client_id: str, message: str) -> str:
        """加入当前位置的宗门。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        if self._is_member_locked():
            return T.hint(
                "周六和周日不能加入宗门。",
                "宗门战结算和奖励领取期间会锁定成员名单；周一到周五可以加入。",
                buttons=("宗门", "宗门战", "地图"),
            )

        current_membership = self._member_sect(client_id)
        if current_membership:
            return T.hint(
                f"你已经加入宗门：{current_membership['name']}。",
                "需要先退出当前宗门，才能加入其它宗门。",
                buttons=("退出宗门", "宗门", "地图"),
            )

        sect_name = message.strip()
        if not sect_name:
            current_sect = self._sect_by_xy(int(player["x"]), int(player["y"]))
            if current_sect:
                return T.hint(
                    "加入宗门需要带宗门名。",
                    f"发送：加入宗门 {current_sect['name']}",
                    buttons=(f"加入宗门 {current_sect['name']}", "宗门"),
                )
            return T.hint(
                "当前位置不是宗门山门。",
                "先导航到宗门山门，再发送：加入宗门 宗门名。",
                buttons=("地图", "宗门"),
            )

        sect = self._sect_by_name(sect_name)
        if not sect:
            return T.hint(
                f"没有找到宗门：{sect_name}。",
                "请确认宗门名，或在山门处发送：宗门 查看此处宗门。",
                buttons=("宗门", "地图"),
            )
        if int(player["x"]) != int(sect["location_x"]) or int(player["y"]) != int(sect["location_y"]):
            return T.hint(
                f"加入 {sect['name']} 需要到山门所在地。",
                f"山门坐标：({sect['location_x']},{sect['location_y']})，当前位置：{player['location_name']} ({player['x']},{player['y']})。",
                buttons=(f"导航 {sect['location_x']} {sect['location_y']}:去山门", "地图"),
            )

        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO sect_members (client_id, sect_id, role, joined_at)
                    VALUES (?, ?, '成员', ?)
                    """,
                    (client_id, int(sect["sect_id"]), ts()),
                )
                if cursor.rowcount <= 0:
                    return T.hint(
                        "加入宗门失败。",
                        "你可能已经加入其它宗门，请发送：宗门 查看当前宗门。",
                        buttons=("宗门",),
                    )
                conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '加入宗门', ?, ?)",
                    (client_id, str(sect["name"]), ts()),
                )
        except sqlite3.IntegrityError:
            return T.hint(
                "加入宗门失败。",
                "当前一个玩家只能加入一个宗门。",
                buttons=("宗门", "地图"),
            )

        return T.success(f"已加入宗门：{sect['name']}。") + T.buttons("宗门", "地图")

    def quit(self, client_id: str) -> str:
        """退出当前宗门；宗主退出时自动移交或解散空宗门。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        if self._is_member_locked():
            return T.hint(
                "周六和周日不能退出宗门。",
                "宗门战结算和奖励领取期间会锁定成员名单；周一到周五可以退出。",
                buttons=("宗门", "宗门战"),
            )

        with self.db.transaction() as conn:
            membership = conn.execute(
                """
                SELECT s.sect_id, s.name, s.master_client_id, m.role
                FROM sect_members AS m
                JOIN sects AS s ON s.sect_id = m.sect_id
                WHERE m.client_id = ?
                """,
                (client_id,),
            ).fetchone()
            if not membership:
                return T.hint("你还没有加入宗门。", "可以先到宗门山门发送：加入宗门 宗门名。<宗门><地图>")

            sect_id = int(membership["sect_id"])
            sect_name = str(membership["name"])
            is_master = str(membership["master_client_id"]) == client_id
            remaining = conn.execute(
                """
                SELECT client_id
                FROM sect_members
                WHERE sect_id = ? AND client_id != ?
                ORDER BY role = '宗主' DESC, joined_at ASC, client_id ASC
                LIMIT 1
                """,
                (sect_id, client_id),
            ).fetchone()
            conn.execute("DELETE FROM sect_members WHERE client_id = ?", (client_id,))
            if not is_master:
                conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '退出宗门', ?, ?)",
                    (client_id, f"sect={sect_name}", ts()),
                )
                return T.success(f"已退出宗门：{sect_name}。") + T.buttons("宗门", "地图")
            if remaining:
                new_master = str(remaining["client_id"])
                conn.execute(
                    "UPDATE sect_members SET role = CASE WHEN client_id = ? THEN '宗主' ELSE '成员' END WHERE sect_id = ?",
                    (new_master, sect_id),
                )
                conn.execute(
                    "UPDATE sects SET master_client_id = ? WHERE sect_id = ?",
                    (new_master, sect_id),
                )
                detail = f"sect={sect_name}, new_master={new_master}"
                conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '退出宗门', ?, ?)",
                    (client_id, detail, ts()),
                )
                return T.success(
                    f"已退出宗门：{sect_name}。新宗主为 {self.format_player_name(new_master)}。"
                ) + T.buttons("宗门", "地图")

            conn.execute("DELETE FROM sects WHERE sect_id = ?", (sect_id,))
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '解散宗门', ?, ?)",
                (client_id, f"sect={sect_name}", ts()),
            )
        return T.success(f"已退出并解散宗门：{sect_name}。") + T.buttons("宗门", "地图")

    def war(self, client_id: str) -> str:
        """查看本期宗门战影响力和奖励。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        self.ensure_claimable_rewards()
        cycle_start, cycle_end = self._cycle_bounds()
        current_rank = self._cycle_rankings(cycle_start)
        personal_rank = self._personal_rankings(cycle_start)
        pending_rewards = self._pending_rewards(client_id, cycle_start)
        all_pending_rewards = self._pending_rewards_all(client_id)
        own_sect = self._member_sect(client_id)

        panel = T.panel()
        panel.section("宗门战")
        panel.line(f"本期周期：{cycle_start} 到 {self._display_cycle_end(cycle_end)}")
        if self._in_reward_claim_window():
            panel.line("今日为奖励领取日，本期战斗已结束，抢劫不再增加宗门战影响力。")
        else:
            panel.line("战斗日：周一到周六；宗门成员抢劫会按结果和战利品价值增加影响力。")
        panel.line(f"成员变动：{self._member_lock_text()}")
        if own_sect:
            panel.lines(self._sect_bonus_lines(self._sect_bonus(int(own_sect["sect_id"])), compact=True))
        panel.hr()
        panel.section("本期影响力")
        if current_rank:
            qualified = self._qualified_count(len(current_rank))
            if self._in_reward_claim_window():
                panel.line(f"入围宗门：前 {qualified}/{len(current_rank)} 名。")
            for index, row in enumerate(current_rank[:10], start=1):
                mark = ""
                if self._in_reward_claim_window():
                    mark = "｜入围" if index <= qualified else "｜未入围"
                panel.line(f"{index}. {row['name']}：{int(row['influence'])}{mark}")
        else:
            panel.line("暂无宗门产生影响力。")
        panel.hr()
        panel.section("个人贡献")
        if personal_rank:
            personal_qualified = self._personal_reward_count(len(personal_rank))
            if self._in_reward_claim_window():
                panel.line(f"个人奖励：前 {personal_qualified}/{len(personal_rank)} 名，每人 {SECT_WAR_REWARD_ITEM_NAME} x1。")
            for index, row in enumerate(personal_rank[:10], start=1):
                contribution = int(row["influence"])
                percent = self._personal_percent_text(contribution, int(row["sect_influence"]))
                mark = ""
                if self._in_reward_claim_window():
                    mark = "｜个人入围" if index <= personal_qualified else "｜未入围"
                panel.line(
                    f"{index}. {self.format_player_name(str(row['client_id']))}"
                    f"｜{row['sect_name']}：{contribution}（{percent}）{mark}"
                )
        else:
            panel.line("暂无个人贡献。")
        panel.hr()
        if all_pending_rewards:
            panel.section("待领奖励")
            pending_total = sum(int(row["quantity"]) for row in all_pending_rewards)
            pending_cycles = sorted({str(row["cycle_start"]) for row in all_pending_rewards})
            panel.line(f"{SECT_WAR_REWARD_ITEM_NAME} x{pending_total}｜周期：{', '.join(pending_cycles)}")
            panel.hr()
        if self._in_reward_claim_window() and pending_rewards:
            panel.section("本期奖励")
            panel.line(f"待领取：{SECT_WAR_REWARD_ITEM_NAME} x{sum(int(row['quantity']) for row in pending_rewards)}")
            for row in pending_rewards:
                panel.line(f"{self._reward_type_text(str(row['reward_type']))}：{row['ring_item_id']} x{int(row['quantity'])}")
        elif self._in_reward_claim_window():
            panel.section("本期奖励")
            panel.line("你本轮没有待领取宗门战奖励。")
        else:
            panel.section("奖励")
            panel.line("奖励生成后即可领取，通知栏会提示待领奖励。")
        return panel.render() + T.buttons("领取宗门战奖励", "宗门", "地图")

    def claim_war_reward(self, client_id: str) -> str:
        """领取本期宗门战奖励。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.ensure_claimable_rewards()

        cycle_start, cycle_end = self._cycle_bounds()
        with self.db.transaction() as conn:
            if self._in_reward_claim_window():
                self._ensure_rewards_generated_conn(conn, cycle_start, cycle_end)
            rows = self._pending_rewards_conn(conn, client_id)
            if not rows:
                return T.hint(
                    "你没有可领取的宗门战奖励。",
                    "宗门前 20% 会给 30% 成员随机生成淬锋丹待领奖励，个人贡献前 15% 也会额外生成；有待领奖励时通知栏会提醒。",
                    buttons=("宗门战", "宗门"),
                )
            total = sum(max(0, int(row["quantity"])) for row in rows)
            if total <= 0:
                return T.hint("宗门战奖励数量异常。", "请稍后再试。<宗门战>")
            self._claim_reward_rows_conn(conn, client_id, rows)
        return T.success(f"已领取宗门战奖励：{SECT_WAR_REWARD_ITEM_NAME} x{total}。") + T.buttons("纳戒", "宗门战")

    def create(self, client_id: str, message: str) -> str:
        """建立宗门。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        if self._is_member_locked():
            return T.hint(
                "周六和周日不能建立宗门。",
                "宗门战结算和奖励领取期间会锁定成员名单；周一到周五可以建立宗门。",
                buttons=("宗门", "宗门战", "地图"),
            )

        sect = self._member_sect(client_id)
        if sect:
            return T.hint(
                f"你已经有宗门：{sect['name']}。",
                "当前一个玩家只能加入或创建一个宗门。",
                buttons=("宗门", "地图"),
            )
        founded_sect = self._sect_by_founder(client_id)
        if founded_sect:
            return T.hint(
                f"你已经创立过仍存世的宗门：{founded_sect['name']}。",
                "一个玩家只能有一个存世创立宗门；原宗门解散后才能再次建立。",
                buttons=("宗门", "地图"),
            )

        parts = split_words(message)
        if len(parts) < 3:
            return T.hint(
                "建立宗门格式不对。",
                "发送：建立宗门 x y 宗门名\n例如：建立宗门 0 0 青云宗",
                buttons=("建立宗门", "地图"),
            )

        if not parts[0].lstrip("+-").isdigit() or not parts[1].lstrip("+-").isdigit():
            return T.hint(
                "宗门地点需要使用坐标。",
                "发送：建立宗门 x y 宗门名\n例如：建立宗门 0 0 青云宗",
                buttons=("建立宗门", "地图"),
            )
        x = to_int(parts[0])
        y = to_int(parts[1])
        sect_name = " ".join(parts[2:]).strip()
        if not sect_name:
            return T.hint("宗门名不能为空。", "发送：建立宗门 x y 宗门名")

        ok, result = validate_name(sect_name)
        if not ok:
            return T.hint(result, "宗门名请保持 2 到 12 个字符，且不含空白。")
        sect_name = result

        if not self._in_world_bounds(x, y):
            return T.hint(
                f"坐标 ({x},{y}) 超出当前地图范围。",
                f"地图范围：左下角 ({WORLD_COORD_MIN},{WORLD_COORD_MIN})，右上角 ({WORLD_COORD_MAX},{WORLD_COORD_MAX})。",
                buttons=("地图", "建立宗门"),
            )
        occupied_location = self._world_location_by_xy(x, y)
        if occupied_location:
            return T.hint(
                f"坐标 ({x},{y}) 已有特殊地点：{occupied_location['name']}。",
                "换一个空坐标建立宗门。",
                buttons=("地图", "建立宗门"),
            )
        if self._sect_by_xy(x, y):
            return T.hint(
                f"坐标 ({x},{y}) 已经有宗门了。",
                "换一个未被使用的地点再试，或发送：宗门 查看当前宗门。",
                buttons=("宗门", "地图"),
            )
        if self._sect_by_name(sect_name):
            return T.hint(
                f"宗门名 {sect_name} 已被使用。",
                "换一个名字再创建。",
                buttons=("宗门", "地图"),
            )

        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO sects
                    (name, location_name, location_x, location_y, founder_id, master_client_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sect_name,
                        self._sect_location_name(sect_name),
                        x,
                        y,
                        client_id,
                        client_id,
                        ts(),
                    ),
                )
                if cursor.rowcount <= 0:
                    return T.hint("创建宗门失败。", "请稍后重试。<宗门>")
                sect_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO sect_members (client_id, sect_id, role, joined_at)
                    VALUES (?, ?, '宗主', ?)
                    """,
                    (client_id, sect_id, ts()),
                )
                ensure_sect_stats_conn(conn, sect_id)
                conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '建立宗门', ?, ?)",
                    (client_id, sect_name, ts()),
                )
        except sqlite3.IntegrityError:
            return T.hint(
                "宗门创建失败，宗门名、地点或宗主已经被占用。",
                "发送：宗门 查看当前宗门，或换一个坐标和名字再试。",
                buttons=("宗门", "地图"),
            )

        return T.success(
            f"宗门创建成功：{sect_name}。山门坐标：({x},{y})。"
        ) + T.buttons("宗门", "地图")

    def record_robbery_influence_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        *,
        sect_id: int,
        success: bool,
        item_value: int,
        battle: dict,
        detail: str = "",
    ) -> int:
        """把抢劫产生的宗门影响力记到抢劫者当时所属宗门。"""

        return record_sect_robbery_influence_conn(
            conn,
            client_id,
            sect_id=sect_id,
            success=success,
            item_value=item_value,
            battle=battle,
            detail=detail,
        )

    def _sect_panel(self, sect: dict[str, object], client_id: str, joined: bool) -> str:
        """渲染宗门资料。"""

        member_count = self._member_count(int(sect["sect_id"]))
        role = self._member_role(client_id)
        current_start, _current_end = self._cycle_bounds()
        influence = self._sect_influence(int(sect["sect_id"]), current_start)
        sect_bonus = self._sect_bonus(int(sect["sect_id"]))
        panel = T.panel()
        panel.section("宗门")
        if joined:
            panel.line(f"宗门：{sect['name']}")
            panel.line(f"身份：{role or '成员'}")
        else:
            panel.line(f"这里是宗门：{sect['name']}")
        panel.line(f"山门：{sect['location_name']} ({sect['location_x']},{sect['location_y']})")
        panel.line(f"宗主：{self._player_name(str(sect['master_client_id']))}")
        panel.line(f"成员：{member_count}")
        panel.line(f"本期影响力：{influence}")
        panel.lines(self._sect_bonus_lines(sect_bonus, compact=False))
        panel.line(f"成员变动：{self._member_lock_text()}")
        panel.line(f"创建时间：{sect['created_at']}")
        if joined:
            return panel.render() + T.buttons("宗门战", "领取宗门战奖励", "退出宗门", "宗门", "地图")
        return panel.render() + T.buttons(f"加入宗门 {sect['name']}", "宗门战", "宗门", "地图")

    def _sect_by_name(self, name: str) -> dict[str, object] | None:
        """按宗门名读取。"""

        return self.db.fetch_one("SELECT * FROM sects WHERE name = ?", (name.strip(),))

    def _sect_by_xy(self, x: int, y: int) -> dict[str, object] | None:
        """按坐标读取。"""

        return self.db.fetch_one("SELECT * FROM sects WHERE location_x = ? AND location_y = ?", (int(x), int(y)))

    def _member_sect(self, client_id: str) -> dict[str, object] | None:
        """读取玩家已加入的宗门。"""

        return self.db.fetch_one(
            """
            SELECT s.*, m.role, m.joined_at
            FROM sect_members AS m
            JOIN sects AS s ON s.sect_id = m.sect_id
            WHERE m.client_id = ?
            """,
            (client_id,),
        )

    def _member_count(self, sect_id: int) -> int:
        """读取宗门成员数量。"""

        row = self.db.fetch_one("SELECT COUNT(*) AS count FROM sect_members WHERE sect_id = ?", (int(sect_id),))
        return int(row["count"]) if row else 0

    def _member_role(self, client_id: str) -> str:
        """读取玩家在宗门内的身份。"""

        row = self.db.fetch_one("SELECT role FROM sect_members WHERE client_id = ?", (client_id,))
        return str(row["role"]) if row else ""

    def _sect_by_master(self, client_id: str) -> dict[str, object] | None:
        """按宗主读取。"""

        return self.db.fetch_one("SELECT * FROM sects WHERE master_client_id = ?", (client_id,))

    def _sect_by_founder(self, client_id: str) -> dict[str, object] | None:
        """按开宗人读取。"""

        return self.db.fetch_one("SELECT * FROM sects WHERE founder_id = ?", (client_id,))

    def _player_name(self, client_id: str) -> str:
        """读取角色展示名。"""

        row = self.db.fetch_one("SELECT display_name FROM players WHERE client_id = ?", (client_id,))
        return str(row["display_name"]) if row else client_id

    @staticmethod
    def _in_world_bounds(x: int, y: int) -> bool:
        """判断坐标是否在当前世界地图范围内。"""

        return WORLD_COORD_MIN <= int(x) <= WORLD_COORD_MAX and WORLD_COORD_MIN <= int(y) <= WORLD_COORD_MAX

    @staticmethod
    def _sect_location_name(sect_name: str) -> str:
        """生成宗门山门展示名。"""

        return f"{sect_name}山门"

    def _world_location_by_xy(self, x: int, y: int) -> dict[str, object] | None:
        """按坐标读取已存在的特殊地点。"""

        return self.db.fetch_one("SELECT name, x, y FROM world_locations WHERE x = ? AND y = ?", (int(x), int(y)))

    def _sect_influence(self, sect_id: int, cycle_start: str) -> int:
        """读取某宗门某周期影响力。"""

        row = self.db.fetch_one(
            """
            SELECT COALESCE(SUM(influence), 0) AS influence
            FROM sect_influence_records
            WHERE sect_id = ? AND cycle_start = ?
            """,
            (int(sect_id), cycle_start),
        )
        return int(row["influence"]) if row else 0

    def _city_bonus(self, sect_id: int) -> dict[str, object]:
        """读取宗门山门受到的城池范围增益。"""

        with self.db.transaction() as conn:
            return sect_city_bonus_conn(conn, int(sect_id))

    def _sect_bonus(self, sect_id: int) -> dict[str, object]:
        """读取宗门等级、底蕴和最终增益。"""

        with self.db.transaction() as conn:
            return sect_bonus_conn(conn, int(sect_id))

    def _sect_bonus_lines(self, sect_bonus: dict[str, object], compact: bool) -> list[str]:
        """格式化宗门等级、底蕴和增益。"""

        level = int(sect_bonus.get("level", 1) or 1)
        exp = int(sect_bonus.get("exp", 0) or 0)
        next_exp = int(sect_bonus.get("next_exp", 0) or 0)
        total_bonus = float(sect_bonus.get("total_bonus", 0.0) or 0.0)
        base_bonus = float(sect_bonus.get("base_bonus", 0.0) or 0.0)
        city_bonus = float(sect_bonus.get("city_bonus", 0.0) or 0.0)
        carry = float(sect_bonus.get("city_carry_rate", 0.0) or 0.0)
        effective_city = float(sect_bonus.get("effective_city_bonus", 0.0) or 0.0)
        lines = [
            f"宗门等级：Lv.{level}" + (f"｜经验 {exp}/{next_exp}" if next_exp > 0 else "｜已满级"),
            (
                f"宗门加持：总 {total_bonus * 100:.1f}%｜"
                f"自强 {base_bonus * 100:.1f}%｜地利 {effective_city * 100:.1f}%"
            ),
        ]
        if not compact:
            lines.append(
                "底蕴："
                f"影响力 {int(sect_bonus.get('influence_merit', 0) or 0)}｜"
                f"供养 {int(sect_bonus.get('support_merit', 0) or 0)}｜"
                f"山门建设 {int(sect_bonus.get('build_merit', 0) or 0)}"
            )
            lines.append(f"城池承载：{carry * 100:.1f}%｜原始城池增益 {city_bonus * 100:.1f}%")
        city = sect_bonus.get("city") if isinstance(sect_bonus.get("city"), dict) else {}
        lines.extend(self._city_bonus_lines(city))
        return lines

    def _city_bonus_lines(self, city_bonus: dict[str, object]) -> list[str]:
        """格式化宗门面板里的城池影响。"""

        bonus = max(0.0, float(city_bonus.get("bonus", 0.0) or 0.0))
        covers = city_bonus.get("covers") if isinstance(city_bonus.get("covers"), list) else []
        if not covers:
            return ["城池影响：暂无城池覆盖山门。"]
        lines = [f"城池范围：原始地利 {bonus * 100:.1f}%。"]
        for cover in covers[:3]:
            if not isinstance(cover, dict):
                continue
            role = str(cover.get("role") or "协同")
            factor = float(cover.get("synergy_factor", 1.0) or 1.0)
            factor_text = "" if factor >= 1 else f"｜协同 {factor * 100:.0f}%"
            distance = float(cover.get("distance", 0.0) or 0.0)
            distance_text = f"{distance:.1f}".rstrip("0").rstrip(".")
            lines.append(
                f"{role}：{cover.get('location_name')} Lv.{int(cover.get('city_level', 1) or 1)}"
                f"｜距离 {distance_text}/{int(cover.get('radius', 1) or 1)}"
                f"｜贡献 {float(cover.get('applied_bonus', 0.0) or 0.0) * 100:.1f}%{factor_text}"
            )
        extra_count = max(0, int(city_bonus.get("all_cover_count", len(covers)) or len(covers)) - len(covers))
        if extra_count > 0:
            lines.append(f"另有 {extra_count} 座城池覆盖，因协同上限未计入。")
        return lines

    def _cycle_rankings(self, cycle_start: str) -> list[dict[str, object]]:
        """读取周期影响力排行榜，只统计正影响力宗门。"""

        return self.db.fetch_all(
            """
            SELECT s.sect_id, s.name, COALESCE(SUM(r.influence), 0) AS influence
            FROM sect_influence_records AS r
            JOIN sects AS s ON s.sect_id = r.sect_id
            WHERE r.cycle_start = ?
            GROUP BY s.sect_id, s.name
            HAVING influence > 0
            ORDER BY influence DESC, s.sect_id ASC
            """,
            (cycle_start,),
        )

    def _personal_rankings(self, cycle_start: str) -> list[dict[str, object]]:
        """读取周期个人贡献排行；宗门已解散则不参与排行。"""

        return self.db.fetch_all(
            """
            SELECT c.client_id, c.sect_id, s.name AS sect_name, c.influence,
                   r.influence AS sect_influence, c.item_value, c.success
            FROM sect_contribution_records AS c
            JOIN sects AS s ON s.sect_id = c.sect_id
            JOIN sect_influence_records AS r
              ON r.sect_id = c.sect_id AND r.cycle_start = c.cycle_start
            WHERE c.cycle_start = ? AND c.influence > 0
            ORDER BY c.influence DESC, c.item_value DESC, c.client_id ASC
            """,
            (cycle_start,),
        )

    def _pending_rewards(self, client_id: str, cycle_start: str) -> list[dict[str, object]]:
        """读取玩家未领取宗门战奖励。"""

        with self.db.transaction() as conn:
            rows = self._pending_rewards_conn(conn, client_id, cycle_start)
        return [dict(row) for row in rows]

    def _pending_rewards_all(self, client_id: str) -> list[dict[str, object]]:
        """读取玩家全部未领取宗门战奖励。"""

        rows = self.db.fetch_all(
            """
            SELECT *
            FROM sect_war_rewards
            WHERE client_id = ? AND claimed = 0
            ORDER BY cycle_start, reward_id
            """,
            (client_id,),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def _pending_rewards_conn(
        conn: sqlite3.Connection,
        client_id: str,
        cycle_start: str | None = None,
    ) -> list[sqlite3.Row]:
        """读取玩家未领取宗门战奖励；可限制到某周期。"""

        if cycle_start is None:
            return conn.execute(
                """
                SELECT * FROM sect_war_rewards
                WHERE client_id = ? AND claimed = 0
                ORDER BY cycle_start, reward_id
                """,
                (client_id,),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM sect_war_rewards
            WHERE client_id = ? AND cycle_start = ? AND claimed = 0
            ORDER BY reward_id
            """,
            (client_id, cycle_start),
        ).fetchall()

    def _ensure_rewards_generated_conn(self, conn: sqlite3.Connection, cycle_start: str, cycle_end: str) -> None:
        """按周期生成一次宗门战奖励。"""

        if not sect_war_cycle_finished(cycle_end):
            return
        cycle = conn.execute(
            "SELECT rewards_generated FROM sect_war_cycles WHERE cycle_start = ?",
            (cycle_start,),
        ).fetchone()
        if cycle and int(cycle["rewards_generated"]):
            return

        conn.execute(
            """
            INSERT INTO sect_war_cycles (cycle_start, cycle_end, rewards_generated, generated_at)
            VALUES (?, ?, 0, NULL)
            ON CONFLICT(cycle_start) DO NOTHING
            """,
            (cycle_start, cycle_end),
        )
        rankings = conn.execute(
            """
            SELECT s.sect_id, s.name, COALESCE(SUM(r.influence), 0) AS influence
            FROM sect_influence_records AS r
            JOIN sects AS s ON s.sect_id = r.sect_id
            WHERE r.cycle_start = ?
            GROUP BY s.sect_id, s.name
            HAVING influence > 0
            ORDER BY influence DESC, s.sect_id ASC
            """,
            (cycle_start,),
        ).fetchall()
        qualified = self._qualified_count(len(rankings))
        current = ts()
        for rank in rankings[:qualified]:
            members = [
                str(row["client_id"])
                for row in conn.execute(
                    """
                    SELECT client_id
                    FROM sect_members
                    WHERE sect_id = ?
                    ORDER BY client_id
                    """,
                    (int(rank["sect_id"]),),
                ).fetchall()
            ]
            if not members:
                continue
            sect_bonus = sect_bonus_conn(conn, int(rank["sect_id"]))
            reward_count = self._reward_member_count(len(members), float(sect_bonus.get("total_bonus", 0.0) or 0.0))
            rng = random_module.Random(f"{cycle_start}:{int(rank['sect_id'])}:sect-war-rewards")
            winners = rng.sample(members, min(reward_count, len(members)))
            for member_id in winners:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO sect_war_rewards
                    (cycle_start, cycle_end, sect_id, client_id, reward_type, ring_item_id, quantity, claimed, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        cycle_start,
                        cycle_end,
                        int(rank["sect_id"]),
                        member_id,
                        SECT_WAR_REWARD_TYPE_SECT_RANDOM,
                        SECT_WAR_REWARD_ITEM_ID,
                        current,
                    ),
                )
        personal_rankings = conn.execute(
            """
            SELECT c.client_id, c.sect_id, c.influence, c.item_value
            FROM sect_contribution_records AS c
            JOIN sects AS s ON s.sect_id = c.sect_id
            WHERE c.cycle_start = ? AND c.influence > 0
            ORDER BY c.influence DESC, c.item_value DESC, c.client_id ASC
            """,
            (cycle_start,),
        ).fetchall()
        personal_qualified = self._personal_reward_count(len(personal_rankings))
        for row in personal_rankings[:personal_qualified]:
            conn.execute(
                """
                INSERT OR IGNORE INTO sect_war_rewards
                (cycle_start, cycle_end, sect_id, client_id, reward_type, ring_item_id, quantity, claimed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    cycle_start,
                    cycle_end,
                    int(row["sect_id"]),
                    str(row["client_id"]),
                    SECT_WAR_REWARD_TYPE_PERSONAL_TOP,
                    SECT_WAR_REWARD_ITEM_ID,
                    current,
                ),
            )
        conn.execute(
            """
            UPDATE sect_war_cycles
            SET rewards_generated = 1, generated_at = ?
            WHERE cycle_start = ?
            """,
            (current, cycle_start),
        )

    def ensure_claimable_rewards(self) -> int:
        """生成当前或上一周期可领取的宗门战奖励；返回新增周期数。"""

        current_start, _current_end = self._cycle_bounds()
        generated = 0
        with self.db.transaction() as conn:
            if self._in_reward_claim_window():
                cycle = conn.execute(
                    "SELECT rewards_generated FROM sect_war_cycles WHERE cycle_start = ?",
                    (current_start,),
                ).fetchone()
                if not cycle or not int(cycle["rewards_generated"]):
                    self._ensure_rewards_generated_conn(conn, current_start, _current_end)
                    generated += 1
            else:
                previous_start_date = datetime.fromisoformat(current_start).date() - timedelta(days=7)
                previous_start = previous_start_date.isoformat()
                previous_end = current_start
                cycle = conn.execute(
                    "SELECT rewards_generated FROM sect_war_cycles WHERE cycle_start = ?",
                    (previous_start,),
                ).fetchone()
                if not cycle or not int(cycle["rewards_generated"]):
                    self._ensure_rewards_generated_conn(conn, previous_start, previous_end)
                    generated += 1
        return generated

    def _claim_reward_rows_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        rows: list[sqlite3.Row],
    ) -> int:
        """领取一组宗门战奖励，返回奖励行数。"""

        if not rows:
            return 0
        total = sum(max(0, int(row["quantity"])) for row in rows)
        if total <= 0:
            return 0
        self.add_ring_conn(conn, client_id, SECT_WAR_REWARD_ITEM_ID, total)
        reward_ids = [int(row["reward_id"]) for row in rows]
        placeholders = ",".join("?" for _ in reward_ids)
        conn.execute(
            f"""
            UPDATE sect_war_rewards
            SET claimed = 1, claimed_at = ?
            WHERE reward_id IN ({placeholders})
            """,
            (ts(), *reward_ids),
        )
        cycle_start = str(rows[0]["cycle_start"])
        reward_types = ",".join(sorted({str(row["reward_type"]) for row in rows}))
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (
                client_id,
                "宗门战奖励",
                f"cycle_start={cycle_start}, item={SECT_WAR_REWARD_ITEM_ID}, quantity={total}, types={reward_types}",
                ts(),
            ),
        )
        return len(rows)

    @staticmethod
    def _personal_percent_text(influence: int, sect_influence: int) -> str:
        """个人贡献占宗门影响力比例文本。"""

        sect_total = max(1, int(sect_influence))
        value = max(0, int(influence)) / sect_total * 100
        return f"{value:.1f}%"

    @staticmethod
    def _cycle_bounds(value=None) -> tuple[str, str]:
        """返回当前宗门战周期。"""

        return sect_war_cycle_bounds(value)

    @staticmethod
    def _display_cycle_end(cycle_end: str) -> str:
        """周期结束日展示。"""

        return sect_war_display_cycle_end(cycle_end)

    @staticmethod
    def _qualified_count(total: int) -> int:
        """前 20% 宗门入围，向上取整。"""

        return sect_war_qualified_count(total)

    @staticmethod
    def _reward_member_count(total: int, bonus_rate: float = 0.0) -> int:
        """入围宗门 30% 成员获得奖励，向上取整。"""

        return sect_war_reward_member_count(total, bonus_rate)

    @staticmethod
    def _personal_reward_count(total: int) -> int:
        """个人贡献前 15% 获得奖励，向上取整。"""

        return sect_war_personal_reward_count(total)

    @staticmethod
    def _is_member_locked(value=None) -> bool:
        """周六和周日锁定宗门成员变动。"""

        return sect_war_is_member_locked(value)

    @staticmethod
    def _in_battle_window(value=None) -> bool:
        """周一到周六为宗门战斗计分日。"""

        return sect_war_in_battle_window(value)

    @staticmethod
    def _in_reward_claim_window(value=None) -> bool:
        """宗门战奖励领取窗口：周日全天。"""

        return sect_war_in_reward_claim_window(value)

    def _member_lock_text(self) -> str:
        """展示当前成员变动规则。"""

        return sect_war_member_lock_text()

    @staticmethod
    def _robbery_influence(*, success: bool, item_value: int, battle: dict) -> int:
        """按抢劫结果计算宗门影响力。"""

        return sect_war_robbery_influence(success=success, item_value=item_value, battle=battle)

    @staticmethod
    def _reward_type_text(reward_type: str) -> str:
        """奖励来源展示。"""

        if reward_type == SECT_WAR_REWARD_TYPE_PERSONAL_TOP:
            return "个人贡献奖"
        return "宗门随机奖"


service = SectService(db)
