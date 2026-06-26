"""光环组件服务。

光环是心境向长期成长线：
- 前 10 点心境由“提升心境”转化获得
- 1 阶由心境达到 10 后自动激活
- 1→10 阶的突破事件与探险地点绑定，供探险前联动调用
"""

from __future__ import annotations

import random as _random
import sqlite3
from datetime import timedelta
from typing import Any

from ..common import CoreService, dt, now, ts
from ..format_text import T
from ..sql import db


COMMON_BREAKTHROUGH_LOCATIONS = {"碧潮岛", "星陨墟", "太虚秘境"}

HALO_STAGE_DEFS: tuple[dict[str, Any], ...] = (
    {"stage": 0, "name": "未映道光", "realm": "凡尘", "desc": "心境未明，大道未曾映身。", "next_require": 10},
    {"stage": 1, "name": "萤火织梦", "realm": "微光境", "desc": "微火照心，梦意初生。", "next_require": 20},
    {"stage": 2, "name": "晨露凝光", "realm": "微光境", "desc": "晨露映念，灵光渐稳。", "next_require": 30},
    {"stage": 3, "name": "月影伴行", "realm": "微光境", "desc": "月影随心，神思澄净。", "next_require": 40},
    {"stage": 4, "name": "青莲剑歌", "realm": "玄光境", "desc": "剑意照神，莲心自明。", "next_require": 50},
    {"stage": 5, "name": "太极阴阳", "realm": "玄光境", "desc": "阴阳流转，心镜两分又合。", "next_require": 60},
    {"stage": 6, "name": "八卦镇灵", "realm": "玄光境", "desc": "八卦定势，心念可镇诸灵。", "next_require": 70},
    {"stage": 7, "name": "大日如来", "realm": "道光境", "desc": "心光如日，照彻内外。", "next_require": 80},
    {"stage": 8, "name": "星河轮转", "realm": "道光境", "desc": "念起星河，周天自转。", "next_require": 90},
    {"stage": 9, "name": "万法归宗", "realm": "混沌光境", "desc": "万法收束，一念归宗。", "next_require": 100},
    {"stage": 10, "name": "太初混元圈", "realm": "混沌光境", "desc": "太初既定，万象圆融。", "next_require": 0},
)

HALO_BREAKTHROUGH_EVENTS: dict[int, dict[str, Any]] = {
    1: {
        "event_code": "halo_1_2",
        "system_type": "halo",
        "from_stage": 1,
        "to_stage": 2,
        "title": "晨露凝光",
        "require_attr": 20,
        "exclusive_location": "天枢城",
        "prompt_text": "清晨灵雾自天枢垂落，你心湖泛起微光，正可将萤火凝为晨露之辉。",
        "give_up_text": "你收回念头，让心火暂熄，需静修一时再续此悟。",
        "try_success_text": "晨露入心，微光更凝，你的光环终于稳成晨露凝光。",
        "try_fail_text": "心念刚起便被外界扰动，凝光未成，只剩一缕余辉。",
        "give_up_penalty": {"spirit_pct": 0.03, "xinjing_minus": 0, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.06, "xinjing_minus": 1, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    2: {
        "event_code": "halo_2_3",
        "system_type": "halo",
        "from_stage": 2,
        "to_stage": 3,
        "title": "月影伴行",
        "require_attr": 30,
        "exclusive_location": "青岚坊",
        "prompt_text": "青岚坊夜气如水，晨露光华正可映出一轮伴身月影。",
        "give_up_text": "你放下这次映月之机，心湖涟漪未平，短时不宜再试。",
        "try_success_text": "月影随行，念头更澄，你顺利将光环推入第三阶。",
        "try_fail_text": "月影方现便被杂念惊散，心境因此受挫。",
        "give_up_penalty": {"spirit_pct": 0.04, "xinjing_minus": 0, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.08, "xinjing_minus": 1, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    3: {
        "event_code": "halo_3_4",
        "system_type": "halo",
        "from_stage": 3,
        "to_stage": 4,
        "title": "青莲剑歌",
        "require_attr": 40,
        "exclusive_location": "赤霞港",
        "prompt_text": "赤霞映海，月影之中有剑意回响，似在催你开出青莲心歌。",
        "give_up_text": "你未再深入那缕剑歌，月影渐淡，只得稍后再悟。",
        "try_success_text": "剑歌穿心而不伤心，青莲由念而生，你正式踏入玄光境。",
        "try_fail_text": "剑意过急，心湖泛乱，光环未能化成青莲。",
        "give_up_penalty": {"spirit_pct": 0.05, "xinjing_minus": 1, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.12, "xinjing_minus": 2, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    4: {
        "event_code": "halo_4_5",
        "system_type": "halo",
        "from_stage": 4,
        "to_stage": 5,
        "title": "太极阴阳",
        "require_attr": 50,
        "exclusive_location": "玄铁岭",
        "prompt_text": "玄铁岭阴阳地脉交错，青莲剑歌已到合流之时，可尝试照见太极。",
        "give_up_text": "你放缓了心神推演，阴阳未合，需冷静一时再来。",
        "try_success_text": "阴阳二气在你心中流转为一，光环终于圆成太极阴阳。",
        "try_fail_text": "两仪未稳反生撕扯，心神与光环都受其累。",
        "give_up_penalty": {"spirit_pct": 0.06, "xinjing_minus": 1, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.15, "xinjing_minus": 3, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    5: {
        "event_code": "halo_5_6",
        "system_type": "halo",
        "from_stage": 5,
        "to_stage": 6,
        "title": "八卦镇灵",
        "require_attr": 60,
        "exclusive_location": "万药谷",
        "prompt_text": "万药谷草木灵息交错，正适合以太极演八卦，借八门之势镇住纷灵。",
        "give_up_text": "你散去卦象推演，心念暂乱，需休养一时方可再试。",
        "try_success_text": "八卦落位，诸念归序，你的光环进阶为八卦镇灵。",
        "try_fail_text": "卦位错乱，心神被反冲震退，悟境明显回落。",
        "give_up_penalty": {"spirit_pct": 0.08, "xinjing_minus": 1, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.18, "xinjing_minus": 5, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    6: {
        "event_code": "halo_6_7",
        "system_type": "halo",
        "from_stage": 6,
        "to_stage": 7,
        "title": "大日如来",
        "require_attr": 70,
        "exclusive_location": "云梦泽",
        "prompt_text": "云梦泽水雾迷蒙，唯有心中大日能照破雾障，此关正是由镇灵入道。",
        "give_up_text": "你没有点亮那轮心日，灵台回暗，需要一段时间重新积势。",
        "try_success_text": "一轮大日自心中升起，云梦尽开，你成功踏入道光境。",
        "try_fail_text": "日轮未稳先崩，光环反噬之下，你的境界因此跌落。",
        "give_up_penalty": {"spirit_pct": 0.10, "xinjing_minus": 2, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.24, "xinjing_minus": 6, "drop_stage_to": 5, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    7: {
        "event_code": "halo_7_8",
        "system_type": "halo",
        "from_stage": 7,
        "to_stage": 8,
        "title": "星河轮转",
        "require_attr": 80,
        "exclusive_location": "流沙海市",
        "prompt_text": "海市蜃景虚实相间，若能以心中大日统摄万象，便可化出星河轮转。",
        "give_up_text": "你主动散去星辰推演，灵台仍有余震，短时无法再聚星河。",
        "try_success_text": "大日收敛为核，群星绕念轮转，你的光环再进一步。",
        "try_fail_text": "星河失序，念头逆流，道光境界也随之动摇。",
        "give_up_penalty": {"spirit_pct": 0.12, "xinjing_minus": 2, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.28, "xinjing_minus": 8, "drop_stage_to": 6, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    8: {
        "event_code": "halo_8_9",
        "system_type": "halo",
        "from_stage": 8,
        "to_stage": 9,
        "title": "万法归宗",
        "require_attr": 90,
        "exclusive_location": "寒霜关",
        "prompt_text": "寒霜关静得近乎凝固，唯有在极静中，星河方能回收万法归于一宗。",
        "give_up_text": "你在最后归宗之前止步，心境折返，需稍后重整。",
        "try_success_text": "万法在你心海之中尽数归宗，混沌光境已然可见。",
        "try_fail_text": "诸法相争不肯归一，你的心境被撕回更低层次。",
        "give_up_penalty": {"spirit_pct": 0.14, "xinjing_minus": 3, "keep_spirit_min": 1},
        "fail_penalty": {"spirit_pct": 0.34, "xinjing_minus": 10, "drop_stage_to": 7, "keep_spirit_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    9: {
        "event_code": "halo_9_10",
        "system_type": "halo",
        "from_stage": 9,
        "to_stage": 10,
        "title": "太初混元",
        "require_attr": 100,
        "exclusive_location": "雷泽城",
        "prompt_text": "雷泽城天地元气翻涌，若能守住一念太初，便可将万法归宗圆成混元之环。",
        "give_up_text": "你在混元闭合前退了一步，心海翻腾，需要一时冷静。",
        "try_success_text": "太初一念落定，混元自成，你终成十阶光环大成。",
        "try_fail_text": "太初未立，万法重散，你虽保住灵台，却被打回前境。",
        "give_up_penalty": {"spirit_pct": 0.18, "xinjing_minus": 4, "keep_spirit_min": 1},
        "fail_penalty": {
            "spirit_pct": 1.0,
            "keep_spirit_min": 1,
            "xinjing_minus": 0,
            "random_xinjing_minus": [1, 10],
            "drop_stage_to": 8,
        },
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
}


class HaloService(CoreService):
    """光环查询、心境成长和突破前置服务。"""

    improve_cost = 500
    improve_label = "心境"
    explore_gain_chance = 0.2
    explore_trigger_chance = 0.2

    def __init__(self) -> None:
        super().__init__(db)

    def my_halo(self, client_id: str) -> str:
        """查看当前光环信息。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        growth = self.db.growth_path(client_id)
        stage = int(growth["halo_stage"])
        xinjing = int(growth["xinjing_value"])
        accum = int(growth["xinjing_spirit_accum"])
        stage_def = self._stage_def(stage)
        next_event = self._event_def(stage)
        cooldown_text = self._cooldown_text(str(growth["halo_cooldown_until"] or ""))

        panel = T.panel()
        panel.section("光环")
        if stage <= 0:
            panel.line("当前阶级：0阶（未激活）")
            panel.line(f"当前心境：**{xinjing}**")
            panel.line("激活方式：心境达到 10 后自动凝成 1 阶光环。")
            panel.line(f"当前累计池：{accum}/{self.improve_cost}")
        else:
            panel.line(f"当前阶级：**{stage}阶 · {stage_def['name']}**")
            panel.line(f"当前境界：{stage_def['realm']}")
            panel.line(f"当前心境：**{xinjing}**")
            panel.line(f"心意说明：{stage_def['desc']}")
            if stage >= 10:
                panel.line("突破状态：已满 10 阶，不再触发光环突破事件。")
            elif next_event:
                panel.line(f"下一阶要求：**{next_event['require_attr']}** 心境")
                panel.line(f"专属地点：{next_event['exclusive_location']}")
                panel.line("通用地点：碧潮岛、星陨墟、太虚秘境")
                panel.line(f"当前成功率：{self._success_rate(xinjing, int(next_event['require_attr'])):.2%}")
                panel.line("触发说明：满足地点与门槛后，探险开始前有 20% 概率进入突破事件。")
        panel.line(f"冷却状态：{cooldown_text}")
        return panel.render() + T.buttons("提升心境", "光环帮助", "探险列表")

    def help_info(self, client_id: str) -> str:
        """展示光环帮助。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        panel = T.panel()
        panel.section("光环帮助")
        panel.line("前 10 点心境只能通过 提升心境 获得。")
        panel.line("每获得 1 点心境，累计恰好消耗 500 精神；单次只会补足当前累计池所缺差值。")
        panel.line("若精神不足本次所缺差值，则保留 1 点精神，其余计入累计池。光环文案中的“生命值”统一按现有血气值解释，非额外字段。")
        panel.line("心境达到 10 后自动激活 1 阶光环 萤火织梦。")
        panel.line("1→10 阶突破必须在指定地点或通用地点的探险开始前触发。")
        panel.line("成功率公式：成功率 = min(1, (当前心境 / 下一阶要求心境)^2)。")
        panel.line("放弃突破：进入 1 小时冷却。尝试失败：不追加统一冷却，完全按事件配置执行。")
        panel.hr()
        panel.section("十阶光环")
        for row in HALO_STAGE_DEFS[1:]:
            text = f"{row['stage']}阶 · {row['name']}"
            if int(row["stage"]) < 10:
                text += f"｜下一阶要求 {row['next_require']}"
            panel.line(text)
        return panel.render() + T.buttons("光环", "提升心境", "探险列表")

    def improve_xinjing(self, client_id: str) -> str:
        """通过精神累计提升前 10 点心境。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        with self.db.transaction() as conn:
            growth = self.db.ensure_player_growth_path_conn(conn, client_id)
            stage = int(growth["halo_stage"])
            xinjing = int(growth["xinjing_value"])
            accum = int(growth["xinjing_spirit_accum"])
            mp_row = conn.execute("SELECT mp FROM players WHERE client_id = ?", (client_id,)).fetchone()
            mp = int(mp_row["mp"]) if mp_row else 0
            if stage >= 1 or xinjing >= 10:
                return T.hint(
                    "你的前 10 点心境阶段已经完成，不能再用固定转化方式提升。",
                    "继续通过探险后的被动成长积累心境，或发送：光环 查看当前突破状态。<光环><探险列表>",
                )
            if mp <= 1:
                return T.hint(
                    "精神过低，至少要保留 1 点精神，当前无法继续提升心境。",
                    "先发送：休息 恢复精神，再继续修行。<休息>",
                )

            need = max(1, self.improve_cost - accum)
            consume = min(need, mp - 1)
            next_accum = accum + consume
            gain = next_accum // self.improve_cost
            left_accum = next_accum % self.improve_cost
            next_xinjing = xinjing + gain
            next_mp = mp - consume

            conn.execute("UPDATE players SET mp = ? WHERE client_id = ?", (next_mp, client_id))
            self.db.set_growth_accum_conn(conn, client_id, xinjing_spirit_accum=left_accum)
            if gain:
                self.db.add_xinjing_conn(conn, client_id, gain)
            unlock_text = self._check_auto_unlock_conn(conn, client_id)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '提升心境', ?, ?)",
                (client_id, f"consume_mp={consume}, gain={gain}, accum={left_accum}", ts()),
            )

        panel = T.panel()
        panel.section("提升心境")
        panel.line(f"本次消耗精神：**{consume}**")
        panel.line(f"当前精神：**{next_mp}**")
        if gain:
            panel.line(f"心境提升：**{xinjing} → {next_xinjing}**")
        else:
            panel.line(f"心境未提升，累计池：**{left_accum}/{self.improve_cost}**")
        if unlock_text:
            panel.hr()
            panel.line(unlock_text)
        return panel.render() + T.buttons("光环", "提升心境", "休息")

    def check_auto_unlock(self, client_id: str) -> str:
        """检查是否满足 0 阶到 1 阶自动激活。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        with self.db.transaction() as conn:
            text = self._check_auto_unlock_conn(conn, client_id)
        return text or "当前未满足光环自动激活条件。"

    def build_explore_breakthrough_context(
        self,
        client_id: str,
        location_name: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """构造探险前光环突破上下文，供探险模块调用。"""

        def _build(active_conn: sqlite3.Connection) -> dict[str, Any]:
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            auto_unlock_text = self._check_auto_unlock_conn(active_conn, client_id)
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            cooldown_until = self._normalize_cooldown_conn(active_conn, client_id, str(growth["halo_cooldown_until"] or ""))
            stage = int(growth["halo_stage"])
            xinjing = int(growth["xinjing_value"])
            stage_def = self._stage_def(stage)
            event = self._event_def(stage)

            eligible = False
            reason = ""
            if stage >= 10:
                reason = "光环已满 10 阶"
            elif stage <= 0:
                reason = "光环尚未激活"
            elif not event:
                reason = "当前没有可触发的光环突破事件"
            elif cooldown_until:
                reason = "光环突破冷却中"
            elif xinjing < int(event["require_attr"]):
                reason = "心境未达到下一阶要求"
            elif not self._event_location_match(event, location_name):
                reason = "当前地点不匹配光环突破事件"
            else:
                eligible = True

            return {
                "system_type": "halo",
                "client_id": client_id,
                "location_name": location_name,
                "stage": stage,
                "stage_name": stage_def["name"],
                "realm": stage_def["realm"],
                "xinjing_value": xinjing,
                "cooldown_until": cooldown_until,
                "cooldown_text": self._cooldown_text(cooldown_until),
                "auto_unlock_text": auto_unlock_text or "",
                "event": dict(event) if event else None,
                "eligible": eligible,
                "reason": reason,
                "trigger_chance": self.explore_trigger_chance,
                "success_rate": self._success_rate(xinjing, int(event["require_attr"])) if event else 0.0,
                "is_common_location": location_name in COMMON_BREAKTHROUGH_LOCATIONS,
            }

        if conn is not None:
            return _build(conn)
        with self.db.transaction() as tx_conn:
            return _build(tx_conn)

    def try_trigger_breakthrough(
        self,
        client_id: str,
        location_name: str,
        *,
        trigger_roll: float | None = None,
        conn: sqlite3.Connection | None = None,
        context_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按 20% 概率尝试触发光环突破事件。"""

        context = dict(context_snapshot) if isinstance(context_snapshot, dict) else self.build_explore_breakthrough_context(client_id, location_name, conn=conn)
        if not context["eligible"]:
            return {
                "system_type": "halo",
                "triggered": False,
                "continued": True,
                "message": "",
                "context": context,
            }

        roll = _random.random() if trigger_roll is None else float(trigger_roll)
        if roll >= self.explore_trigger_chance:
            return {
                "system_type": "halo",
                "triggered": False,
                "continued": True,
                "message": "",
                "context": context,
            }

        event = context["event"] or {}
        panel = T.panel()
        panel.section(f"光环感悟 · {event.get('title', '')}")
        panel.line(str(event.get("prompt_text", "")))
        panel.line(f"当前心境：**{context['xinjing_value']}**｜要求：**{event.get('require_attr', 0)}**")
        panel.line(f"当前成功率：**{context['success_rate']:.2%}**（已锁定到本轮感悟结算）")
        panel.line("说明：本事件文案中的“生命值”统一按现有血气值解释。")
        panel.line("请直接选择 放弃感悟 或 尝试感悟，正式完成本次感悟结算。")
        return {
            "system_type": "halo",
            "triggered": True,
            "continued": False,
            "message": panel.render() + T.buttons("放弃感悟", "尝试感悟"),
            "context": context,
        }

    def apply_breakthrough_choice(
        self,
        client_id: str,
        location_name: str,
        choice: str,
        *,
        success_roll: float | None = None,
        conn: sqlite3.Connection | None = None,
        pending_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据选择结算光环突破事件。"""

        normalized_choice = str(choice or "").strip().lower()
        if normalized_choice not in {"give_up", "try"}:
            return {
                "system_type": "halo",
                "triggered": False,
                "continued": True,
                "message": "光环突破选择无效。",
            }

        def _apply(active_conn: sqlite3.Connection) -> dict[str, Any]:
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            cooldown_until = self._normalize_cooldown_conn(active_conn, client_id, str(growth["halo_cooldown_until"] or ""))
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            stage = int(growth["halo_stage"])
            xinjing = int(growth["xinjing_value"])
            stage_def = self._stage_def(stage)
            snapshot = dict(pending_snapshot) if isinstance(pending_snapshot, dict) else {}
            snapshot_event = snapshot.get("event") if isinstance(snapshot.get("event"), dict) else None
            event = dict(snapshot_event) if snapshot_event else self._event_def(stage)
            expected_stage = int(snapshot.get("from_stage") or stage)
            expected_code = str(snapshot.get("event_code") or (event or {}).get("event_code") or "")
            locked_attr_value = int(snapshot.get("attr_value") or xinjing)
            locked_rate = float(snapshot.get("success_rate") or self._success_rate(xinjing, int((event or {}).get("require_attr", 0))))
            context = {
                "system_type": "halo",
                "client_id": client_id,
                "location_name": location_name,
                "stage": stage,
                "stage_name": stage_def["name"],
                "realm": stage_def["realm"],
                "xinjing_value": xinjing,
                "locked_xinjing_value": locked_attr_value,
                "event": dict(event) if event else None,
                "cooldown_until": cooldown_until,
                "success_rate": locked_rate,
            }
            if stage >= 10 or stage <= 0 or not event:
                return {
                    "system_type": "halo",
                    "triggered": False,
                    "continued": True,
                    "message": "当前光环感悟已失效，无法继续结算。",
                    "context": context,
                }
            if stage != expected_stage or str(event.get("event_code") or "") != expected_code:
                return {
                    "system_type": "halo",
                    "triggered": False,
                    "continued": True,
                    "message": "当前光环阶段已变化，本次感悟无法按原快照继续结算。",
                    "context": context,
                }
            if cooldown_until or not self._event_location_match(event, location_name):
                return {
                    "system_type": "halo",
                    "triggered": False,
                    "continued": True,
                    "message": "当前光环感悟条件已变化，无法继续结算。",
                    "context": context,
                }

            from_stage = stage
            to_stage = int(event["to_stage"])

            if normalized_choice == "give_up":
                applied = self._apply_penalty_conn(active_conn, client_id, event.get("give_up_penalty", {}))
                cooldown_until = ts(now() + timedelta(seconds=int(event.get("cooldown_seconds", 0))))
                self.db.set_halo_cooldown_conn(active_conn, client_id, cooldown_until)
                active_conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '光环放弃感悟', ?, ?)",
                    (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}", ts()),
                )
                panel = T.panel()
                panel.section("光环感悟结果")
                panel.line(str(event.get("give_up_text", "")))
                panel.lines(self._penalty_lines(applied))
                panel.line(f"光环冷却：{self._cooldown_text(cooldown_until)}")
                return {
                    "system_type": "halo",
                    "triggered": True,
                    "continued": True,
                    "success": False,
                    "choice": "give_up",
                    "message": panel.render() + T.buttons("探险状态", "结束探险"),
                    "context": context,
                }

            rate = max(0.0, min(1.0, locked_rate))
            roll = _random.random() if success_roll is None else float(success_roll)
            if roll < rate:
                self.db.set_halo_stage_conn(active_conn, client_id, to_stage)
                self.db.set_halo_cooldown_conn(active_conn, client_id, "")
                active_conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '光环突破成功', ?, ?)",
                    (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}, rate={rate:.6f}", ts()),
                )
                panel = T.panel()
                panel.section("光环突破成功")
                panel.line(str(event.get("try_success_text", "")))
                panel.line(f"光环阶级：**{from_stage}阶 → {to_stage}阶**")
                panel.line(f"锁定心境：**{locked_attr_value}**｜锁定成功率：**{rate:.2%}**")
                panel.line(f"当前名称：**{self._stage_def(to_stage)['name']}**")
                return {
                    "system_type": "halo",
                    "triggered": True,
                    "continued": True,
                    "success": True,
                    "choice": "try",
                    "message": panel.render() + T.buttons("探险状态", "结束探险"),
                    "context": context,
                }

            applied = self._apply_penalty_conn(active_conn, client_id, event.get("fail_penalty", {}))
            active_conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '光环突破失败', ?, ?)",
                (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}, rate={rate:.6f}", ts()),
            )
            panel = T.panel()
            panel.section("光环突破失败")
            panel.line(str(event.get("try_fail_text", "")))
            panel.line(f"锁定心境：**{locked_attr_value}**｜锁定成功率：**{rate:.2%}**")
            panel.lines(self._penalty_lines(applied))
            return {
                "system_type": "halo",
                "triggered": True,
                "continued": True,
                "success": False,
                "choice": "try",
                "message": panel.render() + T.buttons("探险状态", "结束探险"),
                "context": context,
            }

        if conn is not None:
            return _apply(conn)
        with self.db.transaction() as tx_conn:
            return _apply(tx_conn)

    def maybe_gain_explore_xinjing(
        self,
        client_id: str,
        *,
        roll: float | None = None,
    ) -> dict[str, Any]:
        """探险完成后按 20% 概率增加 1 点心境。"""

        with self.db.transaction() as conn:
            return self.maybe_gain_explore_xinjing_conn(conn, client_id, roll=roll)

    def maybe_gain_explore_xinjing_conn(
        self,
        conn,
        client_id: str,
        *,
        roll: float | None = None,
    ) -> dict[str, Any]:
        """事务内按 20% 概率增加 1 点心境。"""

        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        current = int(growth["xinjing_value"])
        stage = int(growth["halo_stage"])
        if current < 10 and stage <= 0:
            return {"gained": False, "amount": 0, "message": ""}
        actual_roll = _random.random() if roll is None else float(roll)
        if actual_roll >= self.explore_gain_chance:
            return {"gained": False, "amount": 0, "message": ""}
        value = self.db.add_xinjing_conn(conn, client_id, 1)
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '探险心境成长', ?, ?)",
            (client_id, f"xinjing={value}", ts()),
        )
        return {
            "gained": True,
            "amount": 1,
            "message": f"此次探险心有所悟，心境提升 1 点，当前心境 {value}。",
        }

    def _check_auto_unlock_conn(self, conn, client_id: str) -> str | None:
        """事务内检查光环 0 阶到 1 阶自动激活。"""

        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        stage = int(growth["halo_stage"])
        xinjing = int(growth["xinjing_value"])
        if stage >= 1 or xinjing < 10:
            return None
        self.db.set_halo_stage_conn(conn, client_id, 1)
        self.db.set_halo_cooldown_conn(conn, client_id, "")
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '光环自动激活', ?, ?)",
            (client_id, f"stage=1, xinjing={xinjing}", ts()),
        )
        return "心境达到 10，自动突破为 1 阶光环 **萤火织梦**。"

    def _normalize_cooldown_conn(self, conn, client_id: str, cooldown_until: str) -> str:
        """事务内清理过期冷却并返回最新值。"""

        value = str(cooldown_until or "")
        if not value:
            return ""
        target = dt(value)
        if not target or target <= now():
            self.db.set_halo_cooldown_conn(conn, client_id, "")
            return ""
        return value

    @staticmethod
    def _stage_def(stage: int) -> dict[str, Any]:
        for row in HALO_STAGE_DEFS:
            if int(row["stage"]) == int(stage):
                return dict(row)
        return dict(HALO_STAGE_DEFS[0])

    @staticmethod
    def _event_def(stage: int) -> dict[str, Any] | None:
        event = HALO_BREAKTHROUGH_EVENTS.get(int(stage))
        return dict(event) if event else None

    @staticmethod
    def _success_rate(current_attr: int, require_attr: int) -> float:
        if require_attr <= 0:
            return 0.0
        ratio = max(0.0, float(current_attr) / float(require_attr))
        return min(1.0, ratio * ratio)

    @staticmethod
    def _event_location_match(event: dict[str, Any], location_name: str) -> bool:
        location = str(location_name or "")
        if location == str(event.get("exclusive_location", "")):
            return True
        return location in COMMON_BREAKTHROUGH_LOCATIONS

    @staticmethod
    def _cooldown_text(cooldown_until: str) -> str:
        if not cooldown_until:
            return "无"
        target = dt(cooldown_until)
        if not target:
            return "无"
        left = target - now()
        if left.total_seconds() <= 0:
            return "无"
        total_seconds = int(left.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if hours > 0:
            return f"剩余 {hours} 小时 {minutes} 分"
        return f"剩余 {minutes} 分"

    @staticmethod
    def _ratio_penalty(value: int, ratio: float, keep_min: int) -> tuple[int, int]:
        current = max(0, int(value))
        reserve = max(0, int(keep_min))
        if current <= reserve or ratio <= 0:
            return current, 0
        lost = int(current * float(ratio))
        if lost <= 0:
            lost = 1
        next_value = max(reserve, current - lost)
        return next_value, current - next_value

    def _apply_penalty_conn(self, conn, client_id: str, penalty: dict[str, Any]) -> dict[str, Any]:
        """事务内应用失败或放弃惩罚。"""

        player = conn.execute("SELECT hp, mp FROM players WHERE client_id = ?", (client_id,)).fetchone()
        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        hp_before = int(player["hp"]) if player else 0
        mp_before = int(player["mp"]) if player else 0
        xinjing_before = int(growth["xinjing_value"])
        stage_before = int(growth["halo_stage"])

        hp_after, hp_lost = self._ratio_penalty(
            hp_before,
            float(penalty.get("blood_pct", 0.0)),
            int(penalty.get("keep_blood_min", 0)),
        )
        mp_after, mp_lost = self._ratio_penalty(
            mp_before,
            float(penalty.get("spirit_pct", 0.0)),
            int(penalty.get("keep_spirit_min", 0)),
        )

        xinjing_lost = max(0, int(penalty.get("xinjing_minus", 0)))
        random_minus = penalty.get("random_xinjing_minus")
        if isinstance(random_minus, (list, tuple)) and len(random_minus) == 2:
            xinjing_lost += max(0, _random.randint(int(random_minus[0]), int(random_minus[1])))
        xinjing_after = max(0, xinjing_before - xinjing_lost)

        stage_after = stage_before
        if penalty.get("drop_stage_to") is not None:
            stage_after = min(stage_after, max(0, int(penalty["drop_stage_to"])))

        conn.execute("UPDATE players SET hp = ?, mp = ? WHERE client_id = ?", (hp_after, mp_after, client_id))
        self.db.add_xinjing_conn(conn, client_id, xinjing_after - xinjing_before)
        if stage_after != stage_before:
            self.db.set_halo_stage_conn(conn, client_id, stage_after)

        return {
            "hp_lost": hp_lost,
            "mp_lost": mp_lost,
            "xinjing_lost": xinjing_before - xinjing_after,
            "stage_before": stage_before,
            "stage_after": stage_after,
        }

    @staticmethod
    def _penalty_lines(applied: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        if int(applied.get("hp_lost", 0)) > 0:
            lines.append(f"血气损失（生命值口径）：{int(applied['hp_lost'])}")
        if int(applied.get("mp_lost", 0)) > 0:
            lines.append(f"精神损失：{int(applied['mp_lost'])}")
        if int(applied.get("xinjing_lost", 0)) > 0:
            lines.append(f"心境损失：{int(applied['xinjing_lost'])}")
        if int(applied.get("stage_after", 0)) < int(applied.get("stage_before", 0)):
            lines.append(f"光环跌阶：{int(applied['stage_before'])}阶 → {int(applied['stage_after'])}阶")
        if not lines:
            lines.append("本次未产生额外属性损耗。")
        return lines


service = HaloService()

__all__ = [
    "COMMON_BREAKTHROUGH_LOCATIONS",
    "HALO_STAGE_DEFS",
    "HALO_BREAKTHROUGH_EVENTS",
    "HaloService",
    "service",
]
