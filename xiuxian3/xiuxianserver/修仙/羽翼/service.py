"""羽翼组件服务。

羽翼是身法向长期成长线：
- 前 10 点身法由“提升身法”转化获得
- 1 阶由身法达到 10 后自动激活
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

WING_STAGE_DEFS: tuple[dict[str, Any], ...] = (
    {"stage": 0, "name": "未启羽痕", "realm": "凡尘", "desc": "身法未成，尚未凝出羽意。", "next_require": 10},
    {"stage": 1, "name": "浮光初翎", "realm": "灵羽境", "desc": "羽意初生，身轻如燕。", "next_require": 20},
    {"stage": 2, "name": "乘风青羽", "realm": "灵羽境", "desc": "借风而起，羽痕渐明。", "next_require": 30},
    {"stage": 3, "name": "流云幻翼", "realm": "灵羽境", "desc": "云影随身，挪转如幻。", "next_require": 40},
    {"stage": 4, "name": "星辉玉骨", "realm": "仙羽境", "desc": "星华入骨，羽势渐成。", "next_require": 50},
    {"stage": 5, "name": "琉璃净羽", "realm": "仙羽境", "desc": "琉璃映心，羽质无垢。", "next_require": 60},
    {"stage": 6, "name": "九霄紫电", "realm": "仙羽境", "desc": "承雷淬翼，紫电环身。", "next_require": 70},
    {"stage": 7, "name": "涅槃金焱", "realm": "圣羽境", "desc": "焚而后生，羽火不灭。", "next_require": 80},
    {"stage": 8, "name": "太虚月华", "realm": "圣羽境", "desc": "月华洗羽，虚实相映。", "next_require": 90},
    {"stage": 9, "name": "混沌鸿蒙翅", "realm": "神羽境", "desc": "鸿蒙未判，羽势归元。", "next_require": 100},
    {"stage": 10, "name": "创世星渊翼", "realm": "神羽境", "desc": "星渊开阖，羽道大成。", "next_require": 0},
)

WING_BREAKTHROUGH_EVENTS: dict[int, dict[str, Any]] = {
    1: {
        "event_code": "wing_1_2",
        "system_type": "wing",
        "from_stage": 1,
        "to_stage": 2,
        "title": "青羽乘风",
        "require_attr": 20,
        "exclusive_location": "天枢城",
        "prompt_text": "风脉轻鸣，你察觉初翎正借城中天枢气机试图再生一层青羽。",
        "give_up_text": "你压下这次感悟，羽意回落，需静养一时。",
        "try_success_text": "你顺风而起，初翎舒展为青羽，羽势更稳。",
        "try_fail_text": "你心神未定，风势散去，羽痕只是轻轻震颤。",
        "give_up_penalty": {"blood_pct": 0.03, "shenfa_minus": 0, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.06, "shenfa_minus": 1, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    2: {
        "event_code": "wing_2_3",
        "system_type": "wing",
        "from_stage": 2,
        "to_stage": 3,
        "title": "流云化翼",
        "require_attr": 30,
        "exclusive_location": "青岚坊",
        "prompt_text": "青岚风卷云舒，你感到羽意将与流云相合，化出幻翼。",
        "give_up_text": "你收敛气息，任流云散去，短时不宜再度感悟。",
        "try_success_text": "你步入云岚中心，青羽化作流云幻翼，身法再进。",
        "try_fail_text": "云意未曾驯服，幻翼半聚半散，徒留反噬。",
        "give_up_penalty": {"blood_pct": 0.04, "shenfa_minus": 0, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.08, "shenfa_minus": 1, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    3: {
        "event_code": "wing_3_4",
        "system_type": "wing",
        "from_stage": 3,
        "to_stage": 4,
        "title": "星骨映辉",
        "require_attr": 40,
        "exclusive_location": "赤霞港",
        "prompt_text": "赤霞潮光映体，云翼之中浮出点点星辉，正待凝成玉骨。",
        "give_up_text": "你避开这次淬骨之机，星辉暂歇，需待一时再试。",
        "try_success_text": "星辉贯体，羽骨如玉，你成功踏入仙羽之境。",
        "try_fail_text": "星辉入体未稳，羽骨震荡，气血受创。",
        "give_up_penalty": {"blood_pct": 0.05, "shenfa_minus": 1, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.12, "shenfa_minus": 2, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    4: {
        "event_code": "wing_4_5",
        "system_type": "wing",
        "from_stage": 4,
        "to_stage": 5,
        "title": "琉璃净心",
        "require_attr": 50,
        "exclusive_location": "玄铁岭",
        "prompt_text": "玄铁岭地火沉重，正适合洗去杂质，让星辉玉骨蜕成琉璃净羽。",
        "give_up_text": "你暂避地火焠炼，羽势受挫，需静养片刻。",
        "try_success_text": "你挺过地火与寒铁交错的焠炼，羽质澄澈如琉璃。",
        "try_fail_text": "焠炼中途失衡，羽意蒙尘，气血与身法皆有折损。",
        "give_up_penalty": {"blood_pct": 0.06, "shenfa_minus": 1, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.15, "shenfa_minus": 3, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    5: {
        "event_code": "wing_5_6",
        "system_type": "wing",
        "from_stage": 5,
        "to_stage": 6,
        "title": "九霄引雷",
        "require_attr": 60,
        "exclusive_location": "万药谷",
        "prompt_text": "谷中雷草齐鸣，琉璃净羽引来九霄雷意。此关需承三段天雷，但底层只作一次总判定。",
        "give_up_text": "你主动散去引雷之势，羽翼震麻，一时不宜再接雷光。",
        "try_success_text": "第一道炼骨，第二道淬羽，第三道定形，你终成九霄紫电。",
        "try_fail_text": "前两道雷势尚可承受，第三道轰然落下，紫电未成反受其噬。",
        "give_up_penalty": {"blood_pct": 0.08, "shenfa_minus": 1, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.20, "shenfa_minus": 5, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "three_thunder_single_roll",
    },
    6: {
        "event_code": "wing_6_7",
        "system_type": "wing",
        "from_stage": 6,
        "to_stage": 7,
        "title": "金焱涅槃",
        "require_attr": 70,
        "exclusive_location": "云梦泽",
        "prompt_text": "云梦泽雾火交织，紫电之羽已到涅槃边缘，需以金焱重燃。",
        "give_up_text": "你止住涅槃火种，羽势回缩，短时无法再入此境。",
        "try_success_text": "你在雾火之中焚去旧羽，重铸金焱，正式迈入圣羽境。",
        "try_fail_text": "涅槃火势反卷，你未能完成重生，羽阶因此跌落。",
        "give_up_penalty": {"blood_pct": 0.10, "shenfa_minus": 2, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.25, "shenfa_minus": 6, "drop_stage_to": 5, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    7: {
        "event_code": "wing_7_8",
        "system_type": "wing",
        "from_stage": 7,
        "to_stage": 8,
        "title": "月华照羽",
        "require_attr": 80,
        "exclusive_location": "流沙海市",
        "prompt_text": "流沙海市月影飘忽，金焱之后需借月华洗尽火燥，化生太虚月华。",
        "give_up_text": "你错过月华最盛的一刻，羽光黯淡，需待冷却再寻良机。",
        "try_success_text": "月华落羽，火意归藏，你的羽翼终于显出太虚清辉。",
        "try_fail_text": "月华与金焱互斥，你被反冲震退，羽阶再度松动。",
        "give_up_penalty": {"blood_pct": 0.12, "shenfa_minus": 2, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.30, "shenfa_minus": 8, "drop_stage_to": 6, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    8: {
        "event_code": "wing_8_9",
        "system_type": "wing",
        "from_stage": 8,
        "to_stage": 9,
        "title": "鸿蒙回响",
        "require_attr": 90,
        "exclusive_location": "寒霜关",
        "prompt_text": "寒霜关天地肃杀，唯有在极静之中，月华才能返照鸿蒙。",
        "give_up_text": "你收回感悟，不再强求鸿蒙回响，羽势略有回退。",
        "try_success_text": "你在极寒静寂中听见初始回响，羽翼化入混沌鸿蒙。",
        "try_fail_text": "鸿蒙未启，反震先临，你的羽意被打回更低层次。",
        "give_up_penalty": {"blood_pct": 0.14, "shenfa_minus": 3, "keep_blood_min": 1},
        "fail_penalty": {"blood_pct": 0.35, "shenfa_minus": 10, "drop_stage_to": 7, "keep_blood_min": 1},
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
    9: {
        "event_code": "wing_9_10",
        "system_type": "wing",
        "from_stage": 9,
        "to_stage": 10,
        "title": "星渊创世",
        "require_attr": 100,
        "exclusive_location": "雷泽城",
        "prompt_text": "雷泽城上空裂出星渊之隙，若能承其开阖，羽道便可圆满至创世星渊翼。",
        "give_up_text": "你在最后一步前按下心念，鸿蒙余波反噬，需待一时方能再望星渊。",
        "try_success_text": "你撑开星渊一线，万象归翼，终成十阶大羽。",
        "try_fail_text": "星渊闭合，鸿蒙碎散，你虽保住性命，却被重重打回。",
        "give_up_penalty": {"blood_pct": 0.18, "shenfa_minus": 4, "keep_blood_min": 1},
        "fail_penalty": {
            "blood_pct": 1.0,
            "keep_blood_min": 1,
            "shenfa_minus": 0,
            "random_shenfa_minus": [1, 10],
            "drop_stage_to": 8,
        },
        "cooldown_seconds": 3600,
        "special_mode": "normal",
    },
}


class WingService(CoreService):
    """羽翼查询、身法成长和突破前置服务。"""

    improve_cost = 1000
    improve_label = "身法"
    explore_gain_chance = 0.2
    explore_trigger_chance = 0.2

    def __init__(self) -> None:
        super().__init__(db)

    def my_wing(self, client_id: str) -> str:
        """查看当前羽翼信息。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        growth = self.db.growth_path(client_id)
        stage = int(growth["wing_stage"])
        shenfa = int(growth["shenfa_value"])
        accum = int(growth["shenfa_blood_accum"])
        stage_def = self._stage_def(stage)
        next_event = self._event_def(stage)
        cooldown_text = self._cooldown_text(str(growth["wing_cooldown_until"] or ""))

        panel = T.panel()
        panel.section("羽翼")
        if stage <= 0:
            panel.line("当前阶级：0阶（未激活）")
            panel.line(f"当前身法：**{shenfa}**")
            panel.line("激活方式：身法达到 10 后自动凝成 1 阶羽翼。")
            panel.line(f"当前累计池：{accum}/{self.improve_cost}")
        else:
            panel.line(f"当前阶级：**{stage}阶 · {stage_def['name']}**")
            panel.line(f"当前境界：{stage_def['realm']}")
            panel.line(f"当前身法：**{shenfa}**")
            panel.line(f"羽意说明：{stage_def['desc']}")
            if stage >= 10:
                panel.line("突破状态：已满 10 阶，不再触发羽翼突破事件。")
            elif next_event:
                panel.line(f"下一阶要求：**{next_event['require_attr']}** 身法")
                panel.line(f"专属地点：{next_event['exclusive_location']}")
                panel.line("通用地点：碧潮岛、星陨墟、太虚秘境")
                panel.line(f"当前成功率：{self._success_rate(shenfa, int(next_event['require_attr'])):.2%}")
                panel.line("触发说明：满足地点与门槛后，探险开始前有 20% 概率进入突破事件。")
        panel.line(f"冷却状态：{cooldown_text}")
        return panel.render() + T.buttons("提升身法", "羽翼帮助", "探险列表")

    def help_info(self, client_id: str) -> str:
        """展示羽翼帮助。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        panel = T.panel()
        panel.section("羽翼帮助")
        panel.line("前 10 点身法只能通过 提升身法 获得。")
        panel.line("每获得 1 点身法，累计恰好消耗 1000 血气；单次只会补足当前累计池所缺差值。")
        panel.line("若血气不足本次所缺差值，则保留 1 点血气，其余计入累计池。羽翼文案中的“生命值”统一按现有血气值结算。")
        panel.line("身法达到 10 后自动激活 1 阶羽翼 浮光初翎。")
        panel.line("1→10 阶突破必须在指定地点或通用地点的探险开始前触发。")
        panel.line("成功率公式：成功率 = min(1, (当前身法 / 下一阶要求身法)^2)。")
        panel.line("放弃突破：进入 1 小时冷却。尝试失败：不追加统一冷却，完全按事件配置执行。")
        panel.hr()
        panel.section("十阶羽翼")
        for row in WING_STAGE_DEFS[1:]:
            text = f"{row['stage']}阶 · {row['name']}"
            if int(row["stage"]) < 10:
                text += f"｜下一阶要求 {row['next_require']}"
            panel.line(text)
        return panel.render() + T.buttons("羽翼", "提升身法", "探险列表")

    def improve_shenfa(self, client_id: str) -> str:
        """通过血气累计提升前 10 点身法。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        with self.db.transaction() as conn:
            growth = self.db.ensure_player_growth_path_conn(conn, client_id)
            stage = int(growth["wing_stage"])
            shenfa = int(growth["shenfa_value"])
            accum = int(growth["shenfa_blood_accum"])
            hp_row = conn.execute("SELECT hp FROM players WHERE client_id = ?", (client_id,)).fetchone()
            hp = int(hp_row["hp"]) if hp_row else 0
            if stage >= 1 or shenfa >= 10:
                return T.hint(
                    "你的前 10 点身法阶段已经完成，不能再用固定转化方式提升。",
                    "继续通过探险后的被动成长积累身法，或发送：羽翼 查看当前突破状态。<羽翼><探险列表>",
                )
            if hp <= 1:
                return T.hint(
                    "血气过低，至少要保留 1 点血气，当前无法继续提升身法。",
                    "先发送：休息 恢复血气，再继续修行。<休息>",
                )

            need = max(1, self.improve_cost - accum)
            consume = min(need, hp - 1)
            next_accum = accum + consume
            gain = next_accum // self.improve_cost
            left_accum = next_accum % self.improve_cost
            next_shenfa = shenfa + gain
            next_hp = hp - consume

            conn.execute("UPDATE players SET hp = ? WHERE client_id = ?", (next_hp, client_id))
            self.db.set_growth_accum_conn(conn, client_id, shenfa_blood_accum=left_accum)
            if gain:
                self.db.add_shenfa_conn(conn, client_id, gain)
            unlock_text = self._check_auto_unlock_conn(conn, client_id)
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '提升身法', ?, ?)",
                (client_id, f"consume_hp={consume}, gain={gain}, accum={left_accum}", ts()),
            )

        panel = T.panel()
        panel.section("提升身法")
        panel.line(f"本次消耗血气：**{consume}**")
        panel.line(f"当前血气：**{next_hp}**")
        if gain:
            panel.line(f"身法提升：**{shenfa} → {next_shenfa}**")
        else:
            panel.line(f"身法未提升，累计池：**{left_accum}/{self.improve_cost}**")
        if unlock_text:
            panel.hr()
            panel.line(unlock_text)
        return panel.render() + T.buttons("羽翼", "提升身法", "休息")

    def check_auto_unlock(self, client_id: str) -> str:
        """检查是否满足 0 阶到 1 阶自动激活。"""

        _, error = self.require_player(client_id)
        if error:
            return error

        with self.db.transaction() as conn:
            text = self._check_auto_unlock_conn(conn, client_id)
        return text or "当前未满足羽翼自动激活条件。"

    def build_explore_breakthrough_context(
        self,
        client_id: str,
        location_name: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """构造探险前羽翼突破上下文，供探险模块调用。"""

        def _build(active_conn: sqlite3.Connection) -> dict[str, Any]:
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            auto_unlock_text = self._check_auto_unlock_conn(active_conn, client_id)
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            cooldown_until = self._normalize_cooldown_conn(active_conn, client_id, str(growth["wing_cooldown_until"] or ""))
            stage = int(growth["wing_stage"])
            shenfa = int(growth["shenfa_value"])
            stage_def = self._stage_def(stage)
            event = self._event_def(stage)

            eligible = False
            reason = ""
            if stage >= 10:
                reason = "羽翼已满 10 阶"
            elif stage <= 0:
                reason = "羽翼尚未激活"
            elif not event:
                reason = "当前没有可触发的羽翼突破事件"
            elif cooldown_until:
                reason = "羽翼突破冷却中"
            elif shenfa < int(event["require_attr"]):
                reason = "身法未达到下一阶要求"
            elif not self._event_location_match(event, location_name):
                reason = "当前地点不匹配羽翼突破事件"
            else:
                eligible = True

            return {
                "system_type": "wing",
                "client_id": client_id,
                "location_name": location_name,
                "stage": stage,
                "stage_name": stage_def["name"],
                "realm": stage_def["realm"],
                "shenfa_value": shenfa,
                "cooldown_until": cooldown_until,
                "cooldown_text": self._cooldown_text(cooldown_until),
                "auto_unlock_text": auto_unlock_text or "",
                "event": dict(event) if event else None,
                "eligible": eligible,
                "reason": reason,
                "trigger_chance": self.explore_trigger_chance,
                "success_rate": self._success_rate(shenfa, int(event["require_attr"])) if event else 0.0,
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
        """按 20% 概率尝试触发羽翼突破事件。"""

        context = dict(context_snapshot) if isinstance(context_snapshot, dict) else self.build_explore_breakthrough_context(client_id, location_name, conn=conn)
        if not context["eligible"]:
            return {
                "system_type": "wing",
                "triggered": False,
                "continued": True,
                "message": "",
                "context": context,
            }

        roll = _random.random() if trigger_roll is None else float(trigger_roll)
        if roll >= self.explore_trigger_chance:
            return {
                "system_type": "wing",
                "triggered": False,
                "continued": True,
                "message": "",
                "context": context,
            }

        event = context["event"] or {}
        panel = T.panel()
        panel.section(f"羽翼感悟 · {event.get('title', '')}")
        panel.line(str(event.get("prompt_text", "")))
        panel.line(f"当前身法：**{context['shenfa_value']}**｜要求：**{event.get('require_attr', 0)}**")
        panel.line(f"当前成功率：**{context['success_rate']:.2%}**（已锁定到本轮感悟结算）")
        panel.line("说明：本事件文案中的“生命值”统一按现有血气值结算。")
        panel.line("请直接选择 放弃感悟 或 尝试感悟，正式完成本次感悟结算。")
        return {
            "system_type": "wing",
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
        """根据选择结算羽翼突破事件。"""

        normalized_choice = str(choice or "").strip().lower()
        if normalized_choice not in {"give_up", "try"}:
            return {
                "system_type": "wing",
                "triggered": False,
                "continued": True,
                "message": "羽翼突破选择无效。",
            }

        def _apply(active_conn: sqlite3.Connection) -> dict[str, Any]:
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            cooldown_until = self._normalize_cooldown_conn(active_conn, client_id, str(growth["wing_cooldown_until"] or ""))
            growth = self.db.ensure_player_growth_path_conn(active_conn, client_id)
            stage = int(growth["wing_stage"])
            shenfa = int(growth["shenfa_value"])
            stage_def = self._stage_def(stage)
            snapshot = dict(pending_snapshot) if isinstance(pending_snapshot, dict) else {}
            snapshot_event = snapshot.get("event") if isinstance(snapshot.get("event"), dict) else None
            event = dict(snapshot_event) if snapshot_event else self._event_def(stage)
            expected_stage = int(snapshot.get("from_stage") or stage)
            expected_code = str(snapshot.get("event_code") or (event or {}).get("event_code") or "")
            locked_attr_value = int(snapshot.get("attr_value") or shenfa)
            locked_rate = float(snapshot.get("success_rate") or self._success_rate(shenfa, int((event or {}).get("require_attr", 0))))
            context = {
                "system_type": "wing",
                "client_id": client_id,
                "location_name": location_name,
                "stage": stage,
                "stage_name": stage_def["name"],
                "realm": stage_def["realm"],
                "shenfa_value": shenfa,
                "locked_shenfa_value": locked_attr_value,
                "cooldown_until": cooldown_until,
                "cooldown_text": self._cooldown_text(cooldown_until),
                "event": dict(event) if event else None,
                "success_rate": locked_rate,
            }
            if stage >= 10 or stage <= 0 or not event:
                return {
                    "system_type": "wing",
                    "triggered": False,
                    "continued": True,
                    "message": "当前羽翼感悟已失效，无法继续结算。",
                    "context": context,
                }
            if stage != expected_stage or str(event.get("event_code") or "") != expected_code:
                return {
                    "system_type": "wing",
                    "triggered": False,
                    "continued": True,
                    "message": "当前羽翼阶段已变化，本次感悟无法按原快照继续结算。",
                    "context": context,
                }
            if cooldown_until or not self._event_location_match(event, location_name):
                return {
                    "system_type": "wing",
                    "triggered": False,
                    "continued": True,
                    "message": "当前羽翼感悟条件已变化，无法继续结算。",
                    "context": context,
                }

            from_stage = stage
            to_stage = int(event["to_stage"])

            if normalized_choice == "give_up":
                applied = self._apply_penalty_conn(active_conn, client_id, event.get("give_up_penalty", {}))
                cooldown_until = ts(now() + timedelta(seconds=int(event.get("cooldown_seconds", 0))))
                self.db.set_wing_cooldown_conn(active_conn, client_id, cooldown_until)
                active_conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '羽翼放弃感悟', ?, ?)",
                    (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}", ts()),
                )
                panel = T.panel()
                panel.section("羽翼感悟结果")
                panel.line(str(event.get("give_up_text", "")))
                panel.lines(self._penalty_lines(applied))
                panel.line(f"羽翼冷却：{self._cooldown_text(cooldown_until)}")
                return {
                    "system_type": "wing",
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
                self.db.set_wing_stage_conn(active_conn, client_id, to_stage)
                self.db.set_wing_cooldown_conn(active_conn, client_id, "")
                active_conn.execute(
                    "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '羽翼突破成功', ?, ?)",
                    (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}, rate={rate:.6f}", ts()),
                )
                panel = T.panel()
                panel.section("羽翼突破成功")
                if str(event.get("special_mode", "")) == "three_thunder_single_roll":
                    panel.line("第一道天雷落下，羽骨尽鸣。")
                    panel.line("第二道天雷加重，紫光渐成。")
                    panel.line(str(event.get("try_success_text", "")))
                else:
                    panel.line(str(event.get("try_success_text", "")))
                panel.line(f"羽翼阶级：**{from_stage}阶 → {to_stage}阶**")
                panel.line(f"锁定身法：**{locked_attr_value}**｜锁定成功率：**{rate:.2%}**")
                panel.line(f"当前名称：**{self._stage_def(to_stage)['name']}**")
                return {
                    "system_type": "wing",
                    "triggered": True,
                    "continued": True,
                    "success": True,
                    "choice": "try",
                    "message": panel.render() + T.buttons("探险状态", "结束探险"),
                    "context": context,
                }

            applied = self._apply_penalty_conn(active_conn, client_id, event.get("fail_penalty", {}))
            active_conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '羽翼突破失败', ?, ?)",
                (client_id, f"event={event['event_code']}, from_stage={from_stage}, to_stage={to_stage}, rate={rate:.6f}", ts()),
            )
            panel = T.panel()
            panel.section("羽翼突破失败")
            if str(event.get("special_mode", "")) == "three_thunder_single_roll":
                panel.line("第一道天雷落下，你勉强稳住羽骨。")
                panel.line("第二道天雷更重，羽势开始摇晃。")
                panel.line(str(event.get("try_fail_text", "")))
            else:
                panel.line(str(event.get("try_fail_text", "")))
            panel.line(f"锁定身法：**{locked_attr_value}**｜锁定成功率：**{rate:.2%}**")
            panel.lines(self._penalty_lines(applied))
            return {
                "system_type": "wing",
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

    def maybe_gain_explore_shenfa(
        self,
        client_id: str,
        *,
        roll: float | None = None,
    ) -> dict[str, Any]:
        """探险完成后按 20% 概率增加 1 点身法。"""

        with self.db.transaction() as conn:
            return self.maybe_gain_explore_shenfa_conn(conn, client_id, roll=roll)

    def maybe_gain_explore_shenfa_conn(
        self,
        conn: sqlite3.Connection,
        client_id: str,
        *,
        roll: float | None = None,
    ) -> dict[str, Any]:
        """事务内按 20% 概率增加 1 点身法。"""

        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        current = int(growth["shenfa_value"])
        stage = int(growth["wing_stage"])
        if current < 10 and stage <= 0:
            return {"gained": False, "amount": 0, "message": ""}
        actual_roll = _random.random() if roll is None else float(roll)
        if actual_roll >= self.explore_gain_chance:
            return {"gained": False, "amount": 0, "message": ""}
        value = self.db.add_shenfa_conn(conn, client_id, 1)
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '探险身法成长', ?, ?)",
            (client_id, f"shenfa={value}", ts()),
        )
        return {
            "gained": True,
            "amount": 1,
            "message": f"此次探险另有所悟，身法提升 1 点，当前身法 {value}。",
        }

    def _check_auto_unlock_conn(self, conn: sqlite3.Connection, client_id: str) -> str | None:
        """事务内检查羽翼 0 阶到 1 阶自动激活。"""

        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        stage = int(growth["wing_stage"])
        shenfa = int(growth["shenfa_value"])
        if stage >= 1 or shenfa < 10:
            return None
        self.db.set_wing_stage_conn(conn, client_id, 1)
        self.db.set_wing_cooldown_conn(conn, client_id, "")
        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '羽翼自动激活', ?, ?)",
            (client_id, f"stage=1, shenfa={shenfa}", ts()),
        )
        return "身法达到 10，自动突破为 1 阶羽翼 **浮光初翎**。"

    def _normalize_cooldown_conn(self, conn: sqlite3.Connection, client_id: str, cooldown_until: str) -> str:
        """事务内清理过期冷却并返回最新值。"""

        value = str(cooldown_until or "")
        if not value:
            return ""
        target = dt(value)
        if not target or target <= now():
            self.db.set_wing_cooldown_conn(conn, client_id, "")
            return ""
        return value

    @staticmethod
    def _stage_def(stage: int) -> dict[str, Any]:
        for row in WING_STAGE_DEFS:
            if int(row["stage"]) == int(stage):
                return dict(row)
        return dict(WING_STAGE_DEFS[0])

    @staticmethod
    def _event_def(stage: int) -> dict[str, Any] | None:
        event = WING_BREAKTHROUGH_EVENTS.get(int(stage))
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

    def _apply_penalty_conn(self, conn: sqlite3.Connection, client_id: str, penalty: dict[str, Any]) -> dict[str, Any]:
        """事务内应用失败或放弃惩罚。"""

        player = conn.execute("SELECT hp, mp FROM players WHERE client_id = ?", (client_id,)).fetchone()
        growth = self.db.ensure_player_growth_path_conn(conn, client_id)
        hp_before = int(player["hp"]) if player else 0
        mp_before = int(player["mp"]) if player else 0
        shenfa_before = int(growth["shenfa_value"])
        stage_before = int(growth["wing_stage"])

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

        shenfa_lost = max(0, int(penalty.get("shenfa_minus", 0)))
        random_minus = penalty.get("random_shenfa_minus")
        if isinstance(random_minus, (list, tuple)) and len(random_minus) == 2:
            shenfa_lost += max(0, _random.randint(int(random_minus[0]), int(random_minus[1])))
        shenfa_after = max(0, shenfa_before - shenfa_lost)

        stage_after = stage_before
        if penalty.get("drop_stage_to") is not None:
            stage_after = min(stage_after, max(0, int(penalty["drop_stage_to"])))

        conn.execute("UPDATE players SET hp = ?, mp = ? WHERE client_id = ?", (hp_after, mp_after, client_id))
        self.db.add_shenfa_conn(conn, client_id, shenfa_after - shenfa_before)
        if stage_after != stage_before:
            self.db.set_wing_stage_conn(conn, client_id, stage_after)

        return {
            "hp_lost": hp_lost,
            "mp_lost": mp_lost,
            "shenfa_lost": shenfa_before - shenfa_after,
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
        if int(applied.get("shenfa_lost", 0)) > 0:
            lines.append(f"身法损失：{int(applied['shenfa_lost'])}")
        if int(applied.get("stage_after", 0)) < int(applied.get("stage_before", 0)):
            lines.append(f"羽翼跌阶：{int(applied['stage_before'])}阶 → {int(applied['stage_after'])}阶")
        if not lines:
            lines.append("本次未产生额外属性损耗。")
        return lines


service = WingService()

__all__ = [
    "COMMON_BREAKTHROUGH_LOCATIONS",
    "WING_STAGE_DEFS",
    "WING_BREAKTHROUGH_EVENTS",
    "WingService",
    "service",
]
