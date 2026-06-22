"""玩家对战组件服务。"""

from __future__ import annotations

from .. import combat_log_text
from ..combat_core import CombatCore, service as combat_service
from ..common import CoreService, business_day, dump_json, load_json, money, random, split_words, to_int, ts, weapon_id_label
from ..format_text import T
from ..sect_war import record_sect_robbery_influence_conn
from ..sql import db
from ..weapon_core import service as weapon_service


class DuelService(CoreService):
    """切磋和押注决斗。"""

    ROBBERY_TARGET_LIMIT = 2
    ROBBERY_REVENGE_EXTRA_LIMIT = 5

    def spar(self, client_id: str, message: str) -> str:
        """发起切磋。"""

        target_id = self.player_id_from_last_arg(message)
        return self._create_request(client_id, target_id, "spar", 0)

    def accept_spar(self, client_id: str, message: str) -> str | dict:
        """接受切磋。"""

        return self._accept(client_id, message, "spar")

    def reject_spar(self, client_id: str, message: str) -> str:
        """拒绝切磋。"""

        return self._reject(client_id, message, "spar")

    def duel(self, client_id: str, message: str) -> str:
        """发起押注决斗。"""

        target_ref, stake = self._parse_duel_message(message)
        if stake <= 0:
            return T.hint("决斗格式不正确。", "发送：决斗 源石数量 对方名称，也可以直接@对方。")
        return self._create_request(client_id, target_ref, "duel", stake)

    def accept_duel(self, client_id: str, message: str) -> str | dict:
        """接受押注决斗。"""

        return self._accept(client_id, message, "duel")

    def reject_duel(self, client_id: str, message: str) -> str:
        """拒绝押注决斗。"""

        return self._reject(client_id, message, "duel")

    def records(self, client_id: str) -> str:
        """查看切磋和押注决斗记录。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()
        rows = self.db.fetch_all(
            """
            SELECT * FROM duel_records
            WHERE from_client_id = ? OR to_client_id = ?
            ORDER BY created_at DESC, record_id DESC
            LIMIT 10
            """,
            (client_id, client_id),
        )
        if not rows:
            return T.hint("暂无切磋/决斗记录。", "发送：切磋 对方名称，或发送：决斗 源石数量 对方名称。")
        panel = T.panel()
        panel.section("最近切磋/决斗记录")
        for row in rows:
            panel.line(f"{row['mode']}：{row['summary']}")
        return panel.render()

    def robbery(self, client_id: str, message: str) -> str | dict:
        """抢劫正在探险中的玩家。"""

        robber, error = self.require_player(client_id)
        if error:
            return error
        assert robber is not None
        self.cleanup_battle_records()

        target_id = self.player_id_from_last_arg(message)
        if not target_id:
            return T.hint("没有找到抢劫目标。", "发送：抢劫 玩家名称，也可以直接@对方。")
        if target_id == client_id:
            return T.hint("不能抢劫自己。", "换一个正在探险中的玩家。")
        if robber["status"] != "空闲":
            return T.hint("你当前不是空闲状态，不能抢劫。", "先处理当前状态，再尝试抢劫。<修仙信息>")
        if int(robber["hp"]) <= 0:
            return T.hint("血气不足，不能抢劫。", "发送：休息，时间到后发送：结束休息。<休息>")

        target = self.player(target_id)
        if not target:
            return T.hint("对方还没有创建用户。", "请对方先发送：创建用户 名称")
        if target["status"] != "探险中":
            return T.hint("对方不在探险中，不能抢劫。", "抢劫只能针对正在探险中的玩家。")

        record = self._active_exploration_record(target_id)
        if not record:
            return T.hint("对方没有可抢劫的探险记录。", "等对方重新开始探险后再尝试。")
        result = load_json(record["result"], {})
        snapshot = result.get("combat_snapshot")
        if not isinstance(snapshot, dict):
            return T.hint("这轮探险缺少战斗快照，不能抢劫。", "等对方重新开始一轮探险后再尝试。")
        robber_sect_id = self._robber_sect_id(client_id)

        battle = combat_service.duel_with_snapshot(client_id, snapshot, write_log=False)
        settled = self._settle_robbery(client_id, target_id, record["record_id"], battle, robber_sect_id=robber_sect_id)
        if isinstance(settled, str):
            return settled

        battle["robbery_record_id"] = int(settled.get("record_id", 0))
        return self._duel_log_block(
            title="抢劫结束",
            result=battle,
            settlement=self._robbery_settlement_text(settled),
            viewer_id=client_id,
            log_kind="robbery",
            record_id=int(settled.get("record_id", 0)),
        )

    def _settle_robbery(
        self,
        robber_id: str,
        target_id: str,
        record_id: int,
        battle: dict,
        *,
        robber_sect_id: int = 0,
    ) -> dict | str:
        """写入抢劫结果，成功时从目标探险结果里移走战利品。"""

        success = battle.get("winner_id") == robber_id
        with self.db.transaction() as conn:
            target = conn.execute("SELECT status FROM players WHERE client_id = ?", (target_id,)).fetchone()
            if not target or target["status"] != "探险中":
                return T.hint("对方已经不在探险中，抢劫失败。", "只能抢劫正在探险中的玩家。")

            record = conn.execute(
                """
                SELECT * FROM exploration_records
                WHERE record_id = ? AND client_id = ? AND claimed = 0
                """,
                (record_id, target_id),
            ).fetchone()
            if not record:
                return T.hint("对方这轮探险已经结束，抢劫失败。", "等对方重新开始探险后再尝试。")

            robbed_count = self._robbed_count_conn(conn, int(record["record_id"]))
            if robbed_count >= self.ROBBERY_TARGET_LIMIT:
                return T.hint("对方这轮探险已经被抢劫过两次。", "这轮探险不能继续抢劫，换一个目标吧。")
            if self._robbed_by_same_player_conn(conn, int(record["record_id"]), robber_id):
                return T.hint("你已经抢过对方这轮探险。", "同一轮探险里，同一个人只能抢劫一次。")

            result = load_json(record["result"], {})
            hate_before = self._hatred_value_conn(conn, robber_id, target_id)
            hate_used = hate_before if success and hate_before > 0 else 0
            wanted_loot_count = 1 + min(hate_before, self.ROBBERY_REVENGE_EXTRA_LIMIT) if success else 0
            loots: list[dict] = []
            if success:
                loots, result = self._take_robbery_loots(result, wanted_loot_count)
                ok, reason = self._can_receive_robbery_loots_conn(conn, robber_id, loots)
                if not ok:
                    return reason
                self._grant_robbery_loots_conn(conn, robber_id, loots)
                if loots:
                    result.setdefault("robbed_loots", []).extend(self._loot_record_texts(loots, robber_id))
                    conn.execute(
                        "UPDATE exploration_records SET result = ? WHERE record_id = ?",
                        (dump_json(result), record["record_id"]),
                    )
                if hate_used:
                    self._clear_hatred_conn(conn, robber_id, target_id)
                self._increase_hatred_conn(conn, target_id, robber_id, "被抢劫")

            hp_left = max(1, int(battle.get("left_hp_left", 1)))
            mp_left = max(0, int(battle.get("left_mp_left", 0)))
            if not success:
                mp_left = 0
            conn.execute("UPDATE players SET hp = ?, mp = ? WHERE client_id = ?", (hp_left, mp_left, robber_id))

            loot_text = self._format_robbery_loots(loots)
            loot_value = self._robbery_loot_value_conn(conn, loots)
            sect_influence = record_sect_robbery_influence_conn(
                conn,
                robber_id,
                sect_id=robber_sect_id,
                success=success,
                item_value=loot_value,
                battle=battle,
                detail=f"target={target_id}, record_id={record['record_id']}, loot={loot_text}",
            )
            conn.execute(
                """
                INSERT INTO robbery_records (
                    exploration_record_id, robber_id, target_id, winner_id, success,
                    loot_text, loot_json, hate_before, hate_used, result, business_day, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["record_id"],
                    robber_id,
                    target_id,
                    str(battle.get("winner_id", "")),
                    1 if success else 0,
                    loot_text,
                    dump_json(loots),
                    hate_before,
                    hate_used,
                    dump_json(battle),
                    business_day(),
                    ts(),
                ),
            )
            robbery_record_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            self._write_robbery_game_log_conn(conn, robber_id, target_id, success, loot_text)
            self._write_robbery_game_log_conn(conn, target_id, robber_id, success, loot_text, target_view=True)
            self._write_duel_weapon_exp_conn(conn, battle)

        return {
            "success": success,
            "target_id": target_id,
            "loot_text": loot_text,
            "loot_value": loot_value,
            "sect_influence": sect_influence,
            "hate_before": hate_before,
            "hate_used": hate_used,
            "hp_left": hp_left,
            "mp_left": mp_left,
            "record_id": robbery_record_id,
        }

    def _robber_sect_id(self, client_id: str) -> int:
        """抢劫发起时读取当时所属宗门。"""

        row = self.db.fetch_one(
            "SELECT sect_id FROM sect_members WHERE client_id = ?",
            (client_id,),
        )
        return int(row["sect_id"]) if row else 0

    def _active_exploration_record(self, client_id: str) -> dict | None:
        """读取玩家当前未领取的探险记录。"""

        return self.db.fetch_one(
            """
            SELECT * FROM exploration_records
            WHERE client_id = ? AND claimed = 0
            ORDER BY record_id DESC
            LIMIT 1
            """,
            (client_id,),
        )

    @staticmethod
    def _robbed_count_conn(conn, record_id: int) -> int:
        """读取一轮探险已经被抢劫次数。"""

        row = conn.execute(
            "SELECT COUNT(*) AS total FROM robbery_records WHERE exploration_record_id = ?",
            (record_id,),
        ).fetchone()
        return int(row["total"]) if row else 0

    @staticmethod
    def _robbed_by_same_player_conn(conn, record_id: int, robber_id: str) -> bool:
        """同一个玩家不能重复抢劫同一轮探险。"""

        row = conn.execute(
            "SELECT 1 FROM robbery_records WHERE exploration_record_id = ? AND robber_id = ? LIMIT 1",
            (record_id, robber_id),
        ).fetchone()
        return bool(row)

    @staticmethod
    def _hatred_value_conn(conn, from_id: str, to_id: str) -> int:
        """读取 from_id 对 to_id 的仇恨值。"""

        row = conn.execute(
            "SELECT hate_value FROM player_hatreds WHERE from_client_id = ? AND to_client_id = ?",
            (from_id, to_id),
        ).fetchone()
        return max(0, int(row["hate_value"])) if row else 0

    @staticmethod
    def _clear_hatred_conn(conn, from_id: str, to_id: str) -> None:
        """复仇成功后清空这条仇恨。"""

        conn.execute(
            "DELETE FROM player_hatreds WHERE from_client_id = ? AND to_client_id = ?",
            (from_id, to_id),
        )

    @staticmethod
    def _increase_hatred_conn(conn, from_id: str, to_id: str, reason: str) -> None:
        """被抢方对抢劫方增加仇恨。"""

        conn.execute(
            """
            INSERT INTO player_hatreds
            (from_client_id, to_client_id, hate_value, robbery_count, last_reason, updated_at)
            VALUES (?, ?, 1, 1, ?, ?)
            ON CONFLICT(from_client_id, to_client_id)
            DO UPDATE SET
                hate_value = min(player_hatreds.hate_value + 1, 5),
                robbery_count = player_hatreds.robbery_count + 1,
                last_reason = excluded.last_reason,
                updated_at = excluded.updated_at
            """,
            (from_id, to_id, reason, ts()),
        )

    def _take_robbery_loots(self, result: dict, count: int) -> tuple[list[dict], dict]:
        """从目标探险预计算结果里抽取并移走战利品。"""

        candidates = self._robbery_loot_candidates(result)
        random.shuffle(candidates)
        selected = candidates[: max(0, count)]
        if not selected:
            return [], result

        events = result.get("events")
        if not isinstance(events, list):
            events = []
            result["events"] = events
        for loot in selected:
            if loot["kind"] == "weapon":
                result.pop("weapon_drop", None)
                continue
            event_index = int(loot["event_index"])
            field = str(loot["field"])
            if 0 <= event_index < len(events) and isinstance(events[event_index], dict):
                events[event_index][field] = ""
        return selected, result

    def _robbery_loot_candidates(self, result: dict) -> list[dict]:
        """把探险结果中还未领取的战利品整理成候选池。"""

        candidates: list[dict] = []
        events = result.get("events")
        if isinstance(events, list):
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    continue
                for field in ("drop_item_id", "location_drop_item_id"):
                    item_id = str(event.get(field) or "")
                    if item_id:
                        item = self.item_def(item_id)
                        candidates.append(
                            {
                                "kind": "backpack",
                                "event_index": index,
                                "field": field,
                                "item_id": item_id,
                                "name": item["name"] if item else item_id,
                                "quantity": 1,
                            }
                        )
                ring_id = str(event.get("ring_drop_id") or "")
                if ring_id:
                    item = self.ring_item_def(ring_id)
                    candidates.append(
                        {
                            "kind": "ring",
                            "event_index": index,
                            "field": "ring_drop_id",
                            "item_id": ring_id,
                            "name": item["name"] if item else ring_id,
                            "quantity": 1,
                        }
                    )

        weapon_drop = result.get("weapon_drop")
        if isinstance(weapon_drop, dict) and weapon_drop:
            candidates.append(
                {
                    "kind": "weapon",
                    "item_id": str(weapon_drop.get("weapon_def_id", "")),
                    "name": str(weapon_drop.get("name", "武器")),
                    "quantity": 1,
                    "weapon_drop": dict(weapon_drop),
                }
            )
        return candidates

    def _can_receive_robbery_loots_conn(self, conn, robber_id: str, loots: list[dict]) -> tuple[bool, str]:
        """检查抢劫方能否装下抢到的背包物品。"""

        backpack_counts: dict[str, int] = {}
        for loot in loots:
            if loot.get("kind") == "backpack":
                item_id = str(loot["item_id"])
                backpack_counts[item_id] = backpack_counts.get(item_id, 0) + int(loot.get("quantity", 1))
        for item_id, quantity in backpack_counts.items():
            ok, reason = self.can_add_backpack_conn(conn, robber_id, item_id, quantity)
            if not ok:
                return False, T.hint("抢劫成功但背包装不下战利品，本次抢劫未结算。", f"{reason}<背包><自动出售>")
        return True, ""

    def _grant_robbery_loots_conn(self, conn, robber_id: str, loots: list[dict]) -> None:
        """把抢到的战利品实时发给抢劫方。"""

        for loot in loots:
            kind = loot.get("kind")
            quantity = int(loot.get("quantity", 1))
            if kind == "backpack":
                self.add_backpack_conn(conn, robber_id, str(loot["item_id"]), quantity)
            elif kind == "ring":
                self.add_ring_conn(conn, robber_id, str(loot["item_id"]), quantity)
            elif kind == "weapon":
                drop = loot.get("weapon_drop")
                if isinstance(drop, dict):
                    weapon_id = weapon_service.create_weapon_conn(
                        conn,
                        robber_id,
                        str(drop["weapon_def_id"]),
                        str(drop["quality"]),
                        int(drop["max_level"]),
                        equipped=False,
                    )
                    loot["weapon_id"] = weapon_id

    @staticmethod
    def _loot_record_texts(loots: list[dict], robber_id: str) -> list[dict]:
        """写回探险结果里的被抢记录。"""

        return [
            {
                "robber_id": robber_id,
                "kind": loot.get("kind", ""),
                "item_id": loot.get("item_id", ""),
                "name": loot.get("name", ""),
                "quantity": int(loot.get("quantity", 1)),
                "created_at": ts(),
            }
            for loot in loots
        ]

    @staticmethod
    def _format_robbery_loots(loots: list[dict]) -> str:
        """把抢到的战利品格式化。"""

        if not loots:
            return "无"
        texts = []
        for loot in loots:
            if loot.get("kind") == "weapon":
                weapon_id = f"{weapon_id_label(loot['weapon_id'])} " if loot.get("weapon_id") else ""
                drop = loot.get("weapon_drop") if isinstance(loot.get("weapon_drop"), dict) else {}
                quality = f"[{drop.get('quality')}]" if drop.get("quality") else ""
                max_level = f" 上限{drop.get('max_level')}" if drop.get("max_level") else ""
                texts.append(f"武器 {weapon_id}{loot.get('name', '武器')}{quality}{max_level}")
            else:
                texts.append(f"{loot.get('name', loot.get('item_id', '战利品'))} x{int(loot.get('quantity', 1))}")
        return "、".join(texts)

    def _robbery_loot_value_conn(self, conn, loots: list[dict]) -> int:
        """估算抢劫战利品价值，用于宗门影响力。"""

        total = 0
        for loot in loots:
            quantity = max(1, int(loot.get("quantity", 1)))
            kind = str(loot.get("kind", ""))
            if kind == "backpack":
                row = conn.execute(
                    "SELECT base_price FROM item_defs WHERE item_id = ?",
                    (str(loot.get("item_id", "")),),
                ).fetchone()
                total += (int(row["base_price"]) if row else 0) * quantity
            elif kind == "ring":
                row = conn.execute(
                    "SELECT quality, category FROM ring_item_defs WHERE ring_item_id = ?",
                    (str(loot.get("item_id", "")),),
                ).fetchone()
                total += self._ring_loot_base_value(row) * quantity
            elif kind == "weapon":
                drop = loot.get("weapon_drop") if isinstance(loot.get("weapon_drop"), dict) else {}
                total += self._weapon_drop_value_conn(conn, drop)
        return max(0, total)

    @staticmethod
    def _ring_loot_base_value(row) -> int:
        """按纳戒物品品级估值。"""

        if not row:
            return 0
        base = {
            "凡品": 800,
            "良品": 1800,
            "珍品": 4200,
            "稀品": 9000,
        }.get(str(row["quality"]), 1000)
        if str(row["category"]) == "技能书":
            return base * 2
        if str(row["category"]) == "宝石":
            return base
        return base

    def _weapon_drop_value_conn(self, conn, drop: dict) -> int:
        """按武器掉落模板估值。"""

        if not isinstance(drop, dict) or not drop:
            return 0
        weapon_def = conn.execute(
            "SELECT base_attack FROM weapon_defs WHERE weapon_def_id = ?",
            (str(drop.get("weapon_def_id", "")),),
        ).fetchone()
        base_attack = int(weapon_def["base_attack"]) if weapon_def else 10
        max_level = max(1, int(drop.get("max_level", 1) or 1))
        quality_factor = {
            "凡品": 1.0,
            "良品": 1.4,
            "珍品": 2.0,
            "稀品": 3.0,
        }.get(str(drop.get("quality", "")), 1.0)
        return int((base_attack * 80 + max_level * 120) * quality_factor)

    @staticmethod
    def _write_robbery_game_log_conn(
        conn,
        client_id: str,
        opponent_id: str,
        success: bool,
        loot_text: str,
        *,
        target_view: bool = False,
    ) -> None:
        """给抢劫双方写入简要日志。"""

        action = "遭遇抢劫" if target_view else "抢劫"
        detail = f"opponent={opponent_id}, success={int(success)}, loot={loot_text}"
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (client_id, action, detail, ts()),
        )

    def _robbery_settlement_text(self, settled: dict) -> str:
        """生成抢劫结算说明。"""

        if not settled["success"]:
            text = (
                f"抢劫失败，战后血气 {settled['hp_left']}，精神 {settled['mp_left']}。"
                "失败不会增加仇恨，也不会触发复仇。"
            )
            if int(settled.get("sect_influence", 0)) > 0:
                text += f" 宗门影响力 +{int(settled['sect_influence'])}。"
            return text
        lines = [f"抢劫成功，获得：{settled['loot_text']}。"]
        if int(settled.get("sect_influence", 0)) > 0:
            lines.append(
                f"宗门影响力 +{int(settled['sect_influence'])}"
                f"（战利品估值 {money(int(settled.get('loot_value', 0)))}）。"
            )
        if int(settled.get("hate_used", 0)) > 0:
            extra_count = min(int(settled["hate_before"]), self.ROBBERY_REVENGE_EXTRA_LIMIT)
            lines.append(
                f"复仇触发：原仇恨 {settled['hate_before']}，报复指数 {min(100, int(settled['hate_before']) * 20)}，"
                f"本次额外尝试 {extra_count} 件战利品。复仇成功后，这条仇恨已清空。"
            )
        lines.append(
            f"{self.format_player_name(str(settled['target_id']))} 对你的仇恨 +1，报复指数 +20；"
            "仇恨越高，对方下次反抢你成功时可额外拿更多战利品。"
        )
        lines.append(f"战后血气 {settled['hp_left']}，精神 {settled['mp_left']}。")
        return " ".join(lines)

    def _create_request(self, client_id: str, target_id: str, mode: str, stake: int) -> str:
        """创建对战请求。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        if not target_id:
            command = "决斗 源石数量 对方名称" if mode == "duel" else "切磋 对方名称"
            return T.hint("没有找到对方。", f"发送：{command}，也可以直接@对方。")
        target, error = self.require_player(target_id)
        if error:
            return T.hint("对方还没有创建用户。", "请对方先发送：创建用户 名称")
        if target_id == client_id:
            return T.hint("不能挑战自己。", "请输入其他玩家名称，或直接@对方。")
        if player["status"] != "空闲" or target["status"] != "空闲":
            return T.hint("双方都需要处于空闲状态。", "双方可先发送：修仙信息 查看状态，处理探险或休息后再挑战。")
        with self.db.transaction() as conn:
            self._expire_requests_conn(conn, client_id, target_id)
            exists = conn.execute(
                """
                SELECT duel_id FROM duel_requests
                WHERE status = '等待'
                  AND (from_client_id = ? OR to_client_id = ? OR from_client_id = ? OR to_client_id = ?)
                LIMIT 1
                """,
                (client_id, client_id, target_id, target_id),
            ).fetchone()
            if exists:
                return T.hint("你或对方已有未处理的对战请求。", "先接受/拒绝当前请求，或等待请求超时后再发起。")
            if mode == "duel" and not self.spend_stones_conn(conn, client_id, stake):
                return T.hint(f"源石不足，决斗需要冻结 {money(stake)}。", "发送：源库 查看存量，或先取出源石、签到、探险、出售物品。")
            conn.execute(
                """
                INSERT INTO duel_requests
                (mode, from_client_id, to_client_id, stake, status, expires_at, created_at)
                VALUES (?, ?, ?, ?, '等待', datetime('now', 'localtime', '+10 minutes'), ?)
                """,
                (mode, client_id, target_id, stake, ts()),
            )
        mode_text = "切磋" if mode == "spar" else f"决斗 {money(stake)} 源石"
        accept_cmd = "接受切磋" if mode == "spar" else "接受决斗"
        reject_cmd = "拒绝切磋" if mode == "spar" else "拒绝决斗"
        from_name = str(player["display_name"])
        panel = T.panel()
        panel.section("对战请求")
        panel.line(f"已向 {self.format_player_name(target_id)} 发起{mode_text}，等待对方处理。")
        panel.line(f"对方 10 分钟内发送：{accept_cmd} {from_name}")
        panel.line(f"如果不接受，发送：{reject_cmd} {from_name}")
        return panel.render()

    def _accept(self, client_id: str, message: str, mode: str) -> str | dict:
        """接受对战请求并结算。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()

        from_id = self.player_id_from_last_arg(message)
        if not from_id:
            command = "接受切磋" if mode == "spar" else "接受决斗"
            return T.hint("没有找到发起人。", f"发送：{command} 发起人名称，也可以直接@发起人。")

        request = self._waiting_request(client_id, from_id, mode)
        if not request:
            return T.hint("没有找到待接受的请求。", "确认对方名称是否正确，或让对方重新发起切磋/决斗。")
        state_error = self._accept_state_error(client_id, from_id)
        if state_error:
            return state_error

        result = combat_service.duel(from_id, client_id, write_log=False)
        accepted = self._settle_accept_request(client_id, from_id, mode, request["duel_id"], result)
        if isinstance(accepted, str):
            return accepted

        request, fee = accepted
        settlement = self._settlement_text(mode, int(request["stake"]), fee)
        return self._duel_log_block(
            title="切磋结束" if mode == "spar" else "决斗结束",
            result=result,
            settlement=settlement,
            viewer_id=client_id,
            log_kind="duel",
            record_id=int(result.get("duel_record_id", 0)),
        )

    def _waiting_request(self, client_id: str, from_id: str, mode: str) -> dict | None:
        """读取当前仍等待处理的对战请求。"""

        with self.db.transaction() as conn:
            self._expire_requests_conn(conn, client_id, from_id)
            request_row = conn.execute(
                """
                SELECT * FROM duel_requests
                WHERE mode = ? AND from_client_id = ? AND to_client_id = ? AND status = '等待'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (mode, from_id, client_id),
            ).fetchone()
        return dict(request_row) if request_row else None

    def _accept_state_error(self, client_id: str, from_id: str) -> str:
        """接受前复查双方本体状态，避免发起后转入探险或休息仍被结算。"""

        challenger = self.player(from_id)
        accepter = self.player(client_id)
        if not challenger or not accepter:
            return T.hint("对战双方状态异常。", "请让对方重新发起切磋/决斗。")
        if challenger["status"] != "空闲" or accepter["status"] != "空闲":
            return T.hint(
                "双方都需要处于空闲状态才能接受对战。",
                "先处理探险、休息或其他战斗；原请求未过期前仍会保留，可稍后再接受，或发送拒绝切磋/拒绝决斗处理。<修仙信息>",
            )
        return ""

    def _settle_accept_request(
        self,
        client_id: str,
        from_id: str,
        mode: str,
        duel_id: int,
        result: dict,
    ) -> tuple[dict, int] | str:
        """接受请求并写入对战结果；失败时直接返回提示文本。"""

        with self.db.transaction() as conn:
            self._expire_requests_conn(conn, client_id, from_id)
            request_row = conn.execute(
                """
                SELECT * FROM duel_requests
                WHERE duel_id = ? AND status = '等待'
                """,
                (duel_id,),
            ).fetchone()
            if not request_row:
                return T.hint("没有找到待接受的请求。", "可能已超时或被处理，请让对方重新发起。")
            request = dict(request_row)
            state_error = self._accept_state_error(client_id, from_id)
            if state_error:
                return state_error

            if mode == "duel" and not self._spend_accept_stake_conn(conn, client_id, from_id, request):
                return T.hint("你的源石不足，决斗已取消，发起人的冻结源石已退回。", "补足源石后让对方重新发起决斗。")

            cursor = conn.execute(
                "UPDATE duel_requests SET status = '已接受' WHERE duel_id = ? AND status = '等待'",
                (request["duel_id"],),
            )
            if cursor.rowcount <= 0:
                return T.hint("没有找到待接受的请求。", "可能已超时或被处理，请让对方重新发起。")

            fee = self._pay_duel_winner_conn(conn, mode, request, result)
            record_id = self._write_duel_records_conn(conn, client_id, from_id, mode, request, result, fee)
            result["duel_record_id"] = record_id
            self._write_duel_weapon_record_conn(conn, result)
        return request, fee

    def _spend_accept_stake_conn(self, conn, client_id: str, from_id: str, request: dict) -> bool:
        """决斗接受方冻结押注；失败时退回发起方押注并取消请求。"""

        if self.spend_stones_conn(conn, client_id, request["stake"]):
            return True
        conn.execute(
            "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
            (request["stake"], from_id),
        )
        conn.execute(
            "UPDATE duel_requests SET status = '已拒绝' WHERE duel_id = ? AND status = '等待'",
            (request["duel_id"],),
        )
        return False

    def _pay_duel_winner_conn(self, conn, mode: str, request: dict, result: dict) -> int:
        """押注决斗结束后，把奖池扣手续费后发给胜者。"""

        if mode != "duel" or not result["winner_id"]:
            return 0
        pool = request["stake"] * 2
        fee = int(pool * 0.03)
        conn.execute(
            "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
            (pool - fee, result["winner_id"]),
        )
        return fee

    def _write_duel_records_conn(
        self,
        conn,
        client_id: str,
        from_id: str,
        mode: str,
        request: dict,
        result: dict,
        fee: int,
    ) -> int:
        """保存对战记录、战斗摘要和双方日志。"""

        action = "切磋结束" if mode == "spar" else "决斗结束"
        cursor = conn.execute(
            """
            INSERT INTO duel_records
            (duel_id, mode, from_client_id, to_client_id, winner_id, loser_id, stake, fee, summary, result, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request["duel_id"],
                "切磋" if mode == "spar" else "决斗",
                from_id,
                client_id,
                result["winner_id"],
                result["loser_id"],
                request["stake"],
                fee,
                result["summary"],
                dump_json(result),
                ts(),
            ),
        )
        conn.execute(
            "INSERT INTO combat_logs (client_id, target, summary, created_at) VALUES (?, ?, ?, ?)",
            (from_id, client_id, result["summary"], ts()),
        )
        self._write_duel_game_log_conn(conn, from_id, action, request, client_id, result, fee)
        self._write_duel_game_log_conn(conn, client_id, action, request, from_id, result, fee)
        return int(cursor.lastrowid)

    @staticmethod
    def _write_duel_game_log_conn(
        conn,
        client_id: str,
        action: str,
        request: dict,
        opponent_id: str,
        result: dict,
        fee: int,
    ) -> None:
        """给一方写入对战日志。"""

        detail = (
            f"duel_id={request['duel_id']}, opponent={opponent_id}, "
            f"winner={result['winner_id'] or ''}, stake={request['stake']}, fee={fee}, "
            f"weapon_exp={DuelService._duel_side_weapon_exp(client_id, result)}"
        )
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (client_id, action, detail, ts()),
        )

    def _write_duel_weapon_record_conn(self, conn, result: dict) -> None:
        """胜者武器累积一场对战胜绩，双方武器获得对战经验。"""

        self._write_duel_weapon_exp_conn(conn, result)
        if result.get("winner_id") == result.get("left_id"):
            self.record_weapon_combat_conn(
                conn,
                result["left_id"],
                int(result.get("left_weapon_id", 0)),
                duel_win=True,
                damage=int(result.get("left_highest_damage", 0)),
            )
        elif result.get("winner_id") == result.get("right_id"):
            self.record_weapon_combat_conn(
                conn,
                result["right_id"],
                int(result.get("right_weapon_id", 0)),
                duel_win=True,
                damage=int(result.get("right_highest_damage", 0)),
            )

    def _write_duel_weapon_exp_conn(self, conn, result: dict) -> None:
        """玩家对战结束后，双方真实持有的武器累计经验。"""

        self.add_weapon_exp_conn(
            conn,
            str(result.get("left_id", "")),
            int(result.get("left_weapon_id", 0)),
            int(result.get("left_weapon_exp", 0)),
        )
        self.add_weapon_exp_conn(
            conn,
            str(result.get("right_id", "")),
            int(result.get("right_weapon_id", 0)),
            int(result.get("right_weapon_exp", 0)),
        )

    @staticmethod
    def _settlement_text(mode: str, stake: int, fee: int) -> str:
        """生成押注决斗结算说明。"""

        if mode != "duel":
            return ""
        return f"决斗结算：胜者获得 {money(stake * 2 - fee)}，手续费 {money(fee)}。"

    def _duel_log_block(
        self,
        *,
        title: str,
        result: dict,
        settlement: str = "",
        viewer_id: str = "",
        log_kind: str = "",
        record_id: int = 0,
    ) -> str | dict:
        """按玩家设置返回对战短摘要，并附带战斗日志链接。"""

        viewer = self.player(viewer_id or str(result.get("left_id", "")))
        return combat_log_text.duel_brief(
            title=title,
            result=result,
            settlement=settlement,
            format_player_name=self.format_player_name,
            log_kind=log_kind,
            record_id=record_id,
            detail=combat_log_text.wants_detail(viewer),
        )

    def _duel_weapon_exp_text(self, result: dict) -> str:
        """展示对战双方本次武器经验。"""

        left_id = str(result.get("left_id", ""))
        right_id = str(result.get("right_id", ""))
        left_exp = int(result.get("left_weapon_exp", 0)) if int(result.get("left_weapon_id", 0)) > 0 else 0
        right_exp = int(result.get("right_weapon_exp", 0)) if int(result.get("right_weapon_id", 0)) > 0 else 0
        return f"{self.format_player_name(left_id)} +{left_exp}｜{self.format_player_name(right_id)} +{right_exp}"

    @staticmethod
    def _duel_side_weapon_exp(client_id: str, result: dict) -> int:
        """返回指定玩家在本次对战中的武器经验。"""

        if client_id == str(result.get("left_id", "")) and int(result.get("left_weapon_id", 0)) > 0:
            return int(result.get("left_weapon_exp", 0))
        if client_id == str(result.get("right_id", "")) and int(result.get("right_weapon_id", 0)) > 0:
            return int(result.get("right_weapon_exp", 0))
        return 0

    def _parse_duel_message(self, message: str) -> tuple[str, int]:
        """解析决斗参数，返回对方 client_id 和押注金额。"""

        parts = split_words(message)
        if len(parts) < 2:
            return "", 0

        fallback_stake = 0
        for index, part in enumerate(parts):
            stake = to_int(part)
            if stake <= 0:
                continue

            target_parts = parts[:index] + parts[index + 1 :]
            if not target_parts:
                continue

            if fallback_stake <= 0:
                fallback_stake = stake

            target_id = self.player_id_by_ref(target_parts[-1])
            if target_id:
                return target_id, stake
        return "", fallback_stake

    def _duel_round_lines(self, action: dict) -> list[str]:
        """整理一次行动条出手。"""

        lines = [f"第 {int(action.get('round', 0))} 次行动"]
        for side in ("left", "right"):
            attack = action.get(side)
            if not isinstance(attack, dict):
                continue
            lines.append("  " + self._duel_attack_text(attack))
        return lines

    def _duel_attack_text(self, attack: dict) -> str:
        """整理一次玩家出手。"""

        actor = self.format_player_name(str(attack.get("actor_id", "")))
        target = self.format_player_name(str(attack.get("target_id", "")))
        if attack.get("skill_used"):
            move = f"技能「{attack.get('skill_name', '')}」"
            cost = f"，消耗精神 {int(attack.get('mp_cost', 0))}"
        else:
            move = "普通攻击"
            cost = ""
        if attack.get("dodged"):
            effect = CombatCore.action_effect_text(attack)
            effect_text = f"，{effect}" if effect else ""
            return (
                f"{actor} 出手：{move} 被 {target} 闪过{effect_text}{cost}；"
                f"{target} 血气 {attack.get('target_hp_left', 0)}，精神 {attack.get('target_mp_left', 0)}"
            )
        combo = int(attack.get("combo_damage", 0))
        combo_text = f"，连击追加 {combo}" if combo > 0 else ""
        steal = int(attack.get("life_steal", 0))
        steal_text = f"，吸血 +{steal}" if steal > 0 else ""
        effect = CombatCore.action_effect_text(attack)
        effect_text = f"，{effect}" if effect else ""
        return (
            f"{actor} 出手：{move}，对 {target} 造成 {int(attack.get('damage', 0))} 伤害"
            f"{combo_text}{steal_text}{effect_text}{cost}；"
            f"{target} 血气 {attack.get('target_hp_left', 0)}，精神 {attack.get('target_mp_left', 0)}"
        )

    def _reject(self, client_id: str, message: str, mode: str) -> str:
        """拒绝对战请求。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        from_id = self.player_id_from_last_arg(message)
        if not from_id:
            command = "拒绝切磋" if mode == "spar" else "拒绝决斗"
            return T.hint("没有找到发起人。", f"发送：{command} 发起人名称，也可以直接@发起人。")
        with self.db.transaction() as conn:
            self._expire_requests_conn(conn, client_id, from_id)
            request = conn.execute(
                """
                SELECT * FROM duel_requests
                WHERE mode = ? AND from_client_id = ? AND to_client_id = ? AND status = '等待'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (mode, from_id, client_id),
            ).fetchone()
            if not request:
                return T.hint("没有找到待拒绝的请求。", "确认对方名称是否正确，或忽略已超时的请求。")
            cursor = conn.execute(
                "UPDATE duel_requests SET status = '已拒绝' WHERE duel_id = ? AND status = '等待'",
                (request["duel_id"],),
            )
            if cursor.rowcount <= 0:
                return T.hint("没有找到待拒绝的请求。", "可能已超时或被处理，无需重复拒绝。")
            if mode == "duel":
                conn.execute(
                    "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                    (request["stake"], from_id),
                )
        return "已拒绝。"

    def _expire_requests_conn(self, conn, *client_ids: str) -> None:
        """把已超时的等待请求标记为超时，并退回决斗冻结源石。"""

        ids = [client_id for client_id in dict.fromkeys(client_ids) if client_id]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""
                SELECT * FROM duel_requests
                WHERE status = '等待'
                  AND expires_at <= datetime('now', 'localtime')
                  AND (
                    from_client_id IN ({placeholders})
                    OR to_client_id IN ({placeholders})
                  )
                """,
                (*ids, *ids),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM duel_requests
                WHERE status = '等待'
                  AND expires_at <= datetime('now', 'localtime')
                """
            ).fetchall()

        for row in rows:
            cursor = conn.execute(
                "UPDATE duel_requests SET status = '已超时' WHERE duel_id = ? AND status = '等待'",
                (row["duel_id"],),
            )
            if cursor.rowcount <= 0:
                continue
            if row["mode"] == "duel" and row["stake"] > 0:
                conn.execute(
                    "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                    (row["stake"], row["from_client_id"]),
                )
            conn.execute(
                """
                INSERT INTO game_logs (client_id, action, detail, created_at)
                VALUES (?, '对战超时', ?, datetime('now', 'localtime'))
                """,
                (row["from_client_id"], f"duel_id={row['duel_id']}, stake={row['stake']}"),
            )


service = DuelService(db)

__all__ = ["DuelService", "service"]
