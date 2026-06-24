"""坐骑组件服务。

坐骑拥有品阶和星级两条成长线：升星消耗升星物品，满星后可使用进阶物品尝试进阶；
进阶成功后品阶提升，星级归零。十阶满星进阶成功后进入显化分支。
"""

from __future__ import annotations

import random as _random
from datetime import timedelta
from math import ceil

from ..common import CoreService, dt, now, ts
from ..format_text import T
from ..sql import db


# 物品 ID → 中文名称映射
_ITEM_NAMES: dict[str, str] = {
    "shou_xue": "兽血",
    "shou_hun_dan": "兽魂丹",
    "huan_shou_xue": "幻兽血",
    "huan_shou_hun_dan": "幻兽魂丹",
    "ji_huan_shou_xue": "极幻兽血",
}

# 显化方向 ID → 显示名映射
_MANIFEST_DIRECTIONS: dict[str, str] = {
    "manifest_east": "东方·青龙",
    "manifest_west": "西方·白虎",
    "manifest_south": "南方·朱雀",
    "manifest_north": "北方·玄武",
}

# 显化方向按钮命令
_MANIFEST_BUTTONS: tuple[str, ...] = (
    "显化·东方青龙",
    "显化·西方白虎",
    "显化·南方朱雀",
    "显化·北方玄武",
)


class MountService(CoreService):
    """坐骑查询、升星、进阶、祝福值管理和显化选择。"""

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #

    def _item_name(self, item_id: str) -> str:
        """物品 ID 转中文名。"""
        return _ITEM_NAMES.get(item_id, item_id)

    def _get_item_count(self, client_id: str, item_id: str) -> int:
        """查询纳戒物品数量。"""
        row = self.db.fetch_one(
            "SELECT quantity FROM ring_items WHERE client_id=? AND ring_item_id=?",
            (client_id, item_id),
        )
        return row["quantity"] if row else 0

    def _consume_items(self, client_id: str, item_id: str, count: int) -> None:
        """消耗纳戒物品。"""
        self.db.execute(
            "UPDATE ring_items SET quantity=quantity-? WHERE client_id=? AND ring_item_id=?",
            (count, client_id, item_id),
        )
        self.db.execute(
            "DELETE FROM ring_items WHERE client_id=? AND ring_item_id=? AND quantity<=0",
            (client_id, item_id),
        )

    @staticmethod
    def _advance_probability(blessing: int, total: int) -> float:
        """计算进阶概率 P = (blessing/total)^3。"""
        if total <= 0:
            return 0.0
        return (blessing / total) ** 3

    def _check_blessing_expired(self, mount: dict) -> bool:
        """检查并清除过期的祝福值，返回是否已过期。"""
        if not mount["blessing_expires_at"]:
            return False
        expires = dt(mount["blessing_expires_at"])
        if expires and now() > expires:
            self.db.execute(
                "UPDATE player_mounts SET blessing_value=0, blessing_expires_at='', updated_at=? WHERE client_id=?",
                (ts(), mount["client_id"]),
            )
            return True
        return False

    def _ensure_mount_created(self, client_id: str) -> dict | None:
        """确保坐骑已创建（等级≥10 自动创建），返回坐骑记录或 None。"""
        player = self.player(client_id)
        if not player:
            return None
        level = player.get("level", 0)
        if level < 10:
            return None
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )
        if not mount:
            current_ts = ts()
            self.db.execute(
                """
                INSERT INTO player_mounts
                (client_id, mount_id, stars, blessing_value, blessing_expires_at, manifest_chosen, created_at, updated_at)
                VALUES (?, 'qj_1', 0, 0, '', 0, ?, ?)
                """,
                (client_id, current_ts, current_ts),
            )
            mount = self.db.fetch_one(
                "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
            )
        return mount

    def _get_mount_def(self, mount_id: str) -> dict | None:
        """读取坐骑定义。"""
        return self.db.fetch_one(
            "SELECT * FROM mount_defs WHERE mount_id=?", (mount_id,)
        )

    def _tier_label(self, mount_def: dict) -> str:
        """生成品阶标签。"""
        mt = mount_def["mount_type"]
        if mt == "extreme":
            return "极显化"
        if mt == "manifest":
            return "显化"
        tier = mount_def["tier"]
        return f"{tier}阶"

    def _check_manifest_prompt(self, client_id: str, mount: dict) -> str | None:
        """显化选择拦截：如果需要选择方向则返回选择界面文本，否则返回 None。"""
        if mount["manifest_chosen"] != 0:
            return None
        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return None
        # 仅 tier=10 且 normal 且满星才拦截
        if mount_def["tier"] != 10 or mount_def["mount_type"] != "normal":
            return None
        if mount["stars"] < mount_def["max_stars"]:
            return None
        # 弹出显化方向选择
        return self._render_manifest_choice(mount_def)

    def _render_manifest_choice(self, mount_def: dict) -> str:
        """渲染显化方向选择界面。"""
        # 读取四条显化坐骑定义
        manifest_defs = self.db.fetch_all(
            "SELECT * FROM mount_defs WHERE mount_type='manifest' ORDER BY tier"
        )
        panel = T.panel()
        panel.section("选择显化方向")
        panel.line(f"{mount_def['name']}满星进阶成功，请选择你的显化方向：")
        panel.blank()
        for md in manifest_defs:
            direction = md["manifest_direction"]
            name = md["name"]
            aphorism = md["manifest_aphorism"]
            panel.line(f"**{direction}** · {name} · {aphorism}")
        return panel.render() + T.buttons(*_MANIFEST_BUTTONS)

    def _format_blessing_remaining(self, mount: dict) -> str:
        """格式化祝福值剩余时间文字。"""
        if not mount["blessing_expires_at"]:
            return ""
        expires = dt(mount["blessing_expires_at"])
        if not expires:
            return ""
        remaining = expires - now()
        if remaining.total_seconds() <= 0:
            return "已过期"
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        return f"剩余 {hours} 时 {minutes} 分"

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #

    def my_mount(self, client_id: str) -> str:
        """查看当前坐骑信息。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        # 等级检查
        level = player.get("level", 0)
        if level < 10:
            return T.hint("等级达到 10 级后开启坐骑模块。", "继续提升等级吧。")

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        # 显化选择拦截
        manifest_text = self._check_manifest_prompt(client_id, mount)
        if manifest_text:
            return manifest_text

        # 检查祝福值过期
        self._check_blessing_expired(mount)
        # 重新读取最新数据
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常，请联系管理员。")

        mt = mount_def["mount_type"]
        is_extreme = mt == "extreme"
        tier_label = self._tier_label(mount_def)

        panel = T.panel()
        panel.section("坐骑")

        # 品阶信息
        if is_extreme:
            direction = mount_def["manifest_direction"]
            panel.line(f"品阶：{tier_label}")
            panel.line(f"方向：{direction}")
            panel.line(f"星级：{mount['stars']}/{mount_def['max_stars']}")
            panel.line(f"坐骑：**{mount_def['name']}**")
            panel.line(f"极境：{mount_def['lore']}")
        elif mt == "manifest":
            direction = mount_def["manifest_direction"]
            panel.line(f"品阶：{tier_label}")
            panel.line(f"方向：{direction}")
            panel.line(f"星级：{mount['stars']}/{mount_def['max_stars']}")
            panel.line(f"坐骑：**{mount_def['name']}**")
            panel.line(f"大道显化：{mount_def['lore']}")
        else:
            panel.line(f"品阶：{tier_label}")
            panel.line(f"星级：{mount['stars']}/{mount_def['max_stars']}")
            panel.line(f"坐骑：**{mount_def['name']}**")
            panel.line(f"意境：{mount_def['lore']}")

        panel.blank()

        if is_extreme:
            panel.line("极显化坐骑已无法进阶")
        else:
            # 进阶祝福值
            blessing = mount["blessing_value"]
            total = mount_def["advance_blessing_total"]
            panel.line(f"进阶祝福值：{blessing}/{total}")
            remaining_text = self._format_blessing_remaining(mount)
            if remaining_text:
                panel.line(f"祝福有效期：{remaining_text}")

            # 下次使用进阶物品的概率
            advance_item = mount_def["advance_item_id"]
            if advance_item:
                item_name = self._item_name(advance_item)
                # 使用后祝福值至少增加 min
                bmin = mount_def["advance_blessing_min"]
                # 展示当前概率
                prob = self._advance_probability(blessing, total)
                panel.line(f"使用 {item_name} 后进阶概率：{prob:.2%}")

        # 按钮
        if is_extreme:
            return panel.render() + T.buttons("一键升星", "坐骑帮助")
        return panel.render() + T.buttons("一键升星", "坐骑进阶", "一键进阶", "坐骑帮助")

    # ------------------------------------------------------------------ #
    #  升星
    # ------------------------------------------------------------------ #

    def star_up(self, client_id: str) -> str:
        """使用 1 个升星物品提升 1 星。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        level = player.get("level", 0)
        if level < 10:
            return T.hint("等级达到 10 级后开启坐骑模块。")

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        manifest_text = self._check_manifest_prompt(client_id, mount)
        if manifest_text:
            return manifest_text

        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        max_stars = mount_def["max_stars"]
        if mount["stars"] >= max_stars:
            if mount_def["mount_type"] == "extreme":
                return T.hint(
                    f"当前坐骑 **{mount_def['name']}** 已满星（{max_stars}星），恭喜！极显化坐骑已达到巅峰。<坐骑>",
                )
            return T.hint(
                f"当前坐骑 **{mount_def['name']}** 已满星（{max_stars}星），无法继续升星。",
                "可以尝试进阶提升品阶。<坐骑进阶>",
            )

        star_item = mount_def["star_item_id"]
        item_count = self._get_item_count(client_id, star_item)
        if item_count <= 0:
            return T.hint(
                f"升星物品 **{self._item_name(star_item)}** 不足。",
                "暂无获取途径，敬请期待。<坐骑>",
            )

        # 扣 1 个升星物品
        self._consume_items(client_id, star_item, 1)
        new_stars = mount["stars"] + 1
        self.db.execute(
            "UPDATE player_mounts SET stars=?, updated_at=? WHERE client_id=?",
            (new_stars, ts(), client_id),
        )

        return (
            f"升星成功！\n"
            f"坐骑：{mount_def['name']} ⭐{mount['stars']}/{max_stars} → ⭐{new_stars}/{max_stars}\n"
            f"消耗 {self._item_name(star_item)} x1\n"
            f"<坐骑>"
        )

    def star_up_all(self, client_id: str) -> str:
        """一键消耗全部升星物品。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        level = player.get("level", 0)
        if level < 10:
            return T.hint("等级达到 10 级后开启坐骑模块。")

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        manifest_text = self._check_manifest_prompt(client_id, mount)
        if manifest_text:
            return manifest_text

        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        max_stars = mount_def["max_stars"]
        if mount["stars"] >= max_stars:
            return T.hint(
                f"当前坐骑 **{mount_def['name']}** 已满星（{max_stars}星），无法继续升星。",
                "可以尝试进阶提升品阶。<坐骑进阶>" if mount_def["mount_type"] != "extreme" else "<坐骑>",
            )

        star_item = mount_def["star_item_id"]
        item_count = self._get_item_count(client_id, star_item)
        if item_count <= 0:
            return T.hint(
                f"升星物品 **{self._item_name(star_item)}** 不足。",
                "暂无获取途径，敬请期待。<坐骑>",
            )

        # 可用数量 = min(物品数, 满星 - 当前星)
        need = max_stars - mount["stars"]
        use_count = min(item_count, need)

        self._consume_items(client_id, star_item, use_count)
        new_stars = mount["stars"] + use_count
        self.db.execute(
            "UPDATE player_mounts SET stars=?, updated_at=? WHERE client_id=?",
            (new_stars, ts(), client_id),
        )

        return (
            f"一键升星完成！\n"
            f"消耗 {self._item_name(star_item)} x{use_count}\n"
            f"坐骑：{mount_def['name']} ⭐{mount['stars']}/{max_stars} → ⭐{new_stars}/{max_stars}\n"
            f"<坐骑>"
        )

    # ------------------------------------------------------------------ #
    #  进阶（二次确认）
    # ------------------------------------------------------------------ #

    def advance(self, client_id: str, message: str) -> str:
        """批量使用进阶物品尝试进阶（二次确认）。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        level = player.get("level", 0)
        if level < 10:
            return T.hint("等级达到 10 级后开启坐骑模块。")

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        manifest_text = self._check_manifest_prompt(client_id, mount)
        if manifest_text:
            return manifest_text

        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        # 极显化不可进阶
        if mount_def["mount_type"] == "extreme":
            return T.hint("极显化坐骑已无法进阶。<坐骑>")

        # 必须满星
        if mount["stars"] < mount_def["max_stars"]:
            return T.hint(
                f"坐骑 **{mount_def['name']}** 需要满星（{mount_def['max_stars']}星）才能进阶。",
                f"当前星级：{mount['stars']}/{mount_def['max_stars']}。<坐骑升星>",
            )

        # 解析数量
        parts = message.strip().split()
        if not parts or not parts[0].isdigit():
            return T.hint("格式：坐骑进阶 数量", "例如：坐骑进阶 10。<坐骑进阶>")
        count = int(parts[0])
        if count <= 0:
            return T.hint("数量必须为正整数。<坐骑进阶>")

        advance_item = mount_def["advance_item_id"]
        item_count = self._get_item_count(client_id, advance_item)
        if item_count <= 0:
            return T.hint(
                f"进阶物品 **{self._item_name(advance_item)}** 不足。",
                "暂无获取途径，敬请期待。<坐骑>",
            )
        if count > item_count:
            count = item_count

        # 展示确认界面
        blessing = mount["blessing_value"]
        total = mount_def["advance_blessing_total"]
        bmin = mount_def["advance_blessing_min"]
        bmax = mount_def["advance_blessing_max"]

        current_prob = self._advance_probability(blessing, total)

        # 预计使用 count 个后的祝福值范围
        after_min_blessing = blessing + count * bmin
        after_max_blessing = min(blessing + count * bmax, total)
        prob_after_min = self._advance_probability(after_min_blessing, total)
        prob_after_max = self._advance_probability(after_max_blessing, total)

        panel = T.panel()
        panel.section("坐骑进阶确认")
        panel.line(f"当前坐骑：{self._tier_label(mount_def)} · {mount_def['name']} ⭐{mount['stars']}/{mount_def['max_stars']}")
        panel.line(f"进阶祝福值：{blessing}/{total}")
        panel.line(f"即将消耗：{self._item_name(advance_item)} x{count}")
        panel.line(f"当前概率：{current_prob:.2%}")
        panel.line(f"使用 {count} 个后预计祝福值：{after_min_blessing}-{after_max_blessing}")
        panel.line(f"预计概率：{prob_after_min:.2%} - {prob_after_max:.2%}")

        return panel.render() + T.buttons(f"确认进阶 {count}", "取消")

    def advance_all(self, client_id: str) -> str:
        """一键进阶（二次确认）。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        level = player.get("level", 0)
        if level < 10:
            return T.hint("等级达到 10 级后开启坐骑模块。")

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        manifest_text = self._check_manifest_prompt(client_id, mount)
        if manifest_text:
            return manifest_text

        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        if mount_def["mount_type"] == "extreme":
            return T.hint("极显化坐骑已无法进阶。<坐骑>")

        if mount["stars"] < mount_def["max_stars"]:
            return T.hint(
                f"坐骑 **{mount_def['name']}** 需要满星（{mount_def['max_stars']}星）才能进阶。",
                f"当前星级：{mount['stars']}/{mount_def['max_stars']}。<坐骑升星>",
            )

        advance_item = mount_def["advance_item_id"]
        item_count = self._get_item_count(client_id, advance_item)
        if item_count <= 0:
            return T.hint(
                f"进阶物品 **{self._item_name(advance_item)}** 不足。",
                "暂无获取途径，敬请期待。<坐骑>",
            )

        blessing = mount["blessing_value"]
        total = mount_def["advance_blessing_total"]
        bmin = mount_def["advance_blessing_min"]
        bmax = mount_def["advance_blessing_max"]

        # 运气最好 / 最差
        gap = total - blessing
        if gap <= 0:
            need_best = 0
            need_worst = 0
        else:
            need_best = ceil(gap / bmax) if bmax > 0 else 999999
            need_worst = ceil(gap / bmin) if bmin > 0 else 999999

        panel = T.panel()
        panel.section("一键进阶确认")
        panel.line(f"当前坐骑：{self._tier_label(mount_def)} · {mount_def['name']} ⭐{mount['stars']}/{mount_def['max_stars']}")
        panel.line(f"进阶祝福值：{blessing}/{total}")
        panel.line(f"进阶物品：{self._item_name(advance_item)}")
        panel.line(f"当前拥有：{item_count} 个")
        panel.blank()
        panel.line(f"运气最好：需要 {need_best} 个（每次+{bmax}）")
        panel.line(f"运气最差：需要 {need_worst} 个（每次+{bmin}）")

        if item_count >= need_worst:
            panel.line("当前拥有充足，可以进阶")
        elif item_count >= need_best:
            panel.line(f"当前拥有 {item_count} 个，运气好可能成功")
        else:
            panel.line(f"当前拥有 {item_count} 个，可能不足")

        return panel.render() + T.buttons("确认一键进阶", "取消")

    # ------------------------------------------------------------------ #
    #  确认进阶
    # ------------------------------------------------------------------ #

    def confirm_advance(self, client_id: str, message: str) -> str:
        """执行批量进阶。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        if mount_def["mount_type"] == "extreme":
            return T.hint("极显化坐骑已无法进阶。<坐骑>")

        if mount["stars"] < mount_def["max_stars"]:
            return T.hint("坐骑未满星，无法进阶。<坐骑升星>")

        # 检查祝福值是否过期（二次确认期间可能跨越24小时）
        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        # 解析数量
        parts = message.strip().split()
        if not parts or not parts[0].isdigit():
            return T.hint("格式：确认进阶 数量。<坐骑进阶>")
        count = int(parts[0])
        if count <= 0:
            return T.hint("数量必须为正整数。<坐骑进阶>")

        advance_item = mount_def["advance_item_id"]
        item_count = self._get_item_count(client_id, advance_item)
        if item_count <= 0:
            return T.hint(f"进阶物品 **{self._item_name(advance_item)}** 不足。<坐骑>")

        use_count = min(count, item_count)
        return self._do_advance(client_id, mount, mount_def, use_count)

    def confirm_advance_all(self, client_id: str) -> str:
        """执行一键进阶。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        if mount_def["mount_type"] == "extreme":
            return T.hint("极显化坐骑已无法进阶。<坐骑>")

        if mount["stars"] < mount_def["max_stars"]:
            return T.hint("坐骑未满星，无法进阶。<坐骑升星>")

        # 检查祝福值是否过期（二次确认期间可能跨越24小时）
        self._check_blessing_expired(mount)
        mount = self.db.fetch_one(
            "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
        )

        advance_item = mount_def["advance_item_id"]
        item_count = self._get_item_count(client_id, advance_item)
        if item_count <= 0:
            return T.hint(f"进阶物品 **{self._item_name(advance_item)}** 不足。<坐骑>")

        return self._do_advance(client_id, mount, mount_def, item_count)

    def _do_advance(
        self,
        client_id: str,
        mount: dict,
        mount_def: dict,
        use_count: int,
    ) -> str:
        """逐个消耗进阶物品并判定进阶，返回结果文本。"""
        advance_item = mount_def["advance_item_id"]
        item_name = self._item_name(advance_item)
        bmin = mount_def["advance_blessing_min"]
        bmax = mount_def["advance_blessing_max"]
        total = mount_def["advance_blessing_total"]

        old_blessing = mount["blessing_value"]
        current_blessing = old_blessing
        consumed = 0
        success = False

        for _ in range(use_count):
            self._consume_items(client_id, advance_item, 1)
            consumed += 1

            # 随机祝福值
            add_value = _random.randint(bmin, bmax)
            current_blessing = min(current_blessing + add_value, total)

            # 设置过期时间（首次使用）
            expires_at = mount["blessing_expires_at"]
            if not expires_at:
                expires_at = ts(now() + timedelta(hours=24))

            # 判定进阶
            prob = self._advance_probability(current_blessing, total)
            if _random.random() < prob:
                # 进阶成功
                next_id = mount_def["post_advance_mount_id"]
                if next_id:
                    # 普通进阶/显化进阶：更新为新坐骑
                    self.db.execute(
                        """
                        UPDATE player_mounts
                        SET mount_id=?, stars=0, blessing_value=0, blessing_expires_at='',
                            manifest_chosen=0, updated_at=?
                        WHERE client_id=?
                        """,
                        (next_id, ts(), client_id),
                    )
                else:
                    # 十阶满星进阶成功：保持当前坐骑，等待显化选择
                    self.db.execute(
                        """
                        UPDATE player_mounts
                        SET blessing_value=0, blessing_expires_at='',
                            manifest_chosen=0, updated_at=?
                        WHERE client_id=?
                        """,
                        (ts(), client_id),
                    )
                success = True
                break
            else:
                # 更新祝福值
                self.db.execute(
                    "UPDATE player_mounts SET blessing_value=?, blessing_expires_at=?, updated_at=? WHERE client_id=?",
                    (current_blessing, expires_at, ts(), client_id),
                )
                # 更新 mount dict 以保持状态一致
                mount["blessing_value"] = current_blessing
                mount["blessing_expires_at"] = expires_at

        if success:
            # 读取新坐骑信息
            new_mount = self.db.fetch_one(
                "SELECT * FROM player_mounts WHERE client_id=?", (client_id,)
            )
            new_def = self._get_mount_def(new_mount["mount_id"])
            new_name = new_def["name"] if new_def else "未知"
            new_tier = self._tier_label(new_def) if new_def else "?"
            old_name = mount_def["name"]
            old_tier = self._tier_label(mount_def)

            # 检查是否触发显化选择
            manifest_text = self._check_manifest_prompt(client_id, new_mount)
            if manifest_text:
                return (
                    f"消耗 {item_name} x{consumed}\n"
                    f"进阶成功！\n"
                    f"{old_tier} {old_name} → {new_tier} {new_name} ⭐0/{new_def['max_stars']}\n"
                    f"\n"
                    + manifest_text
                )

            return (
                f"消耗 {item_name} x{consumed}\n"
                f"进阶成功！\n"
                f"{old_tier} {old_name} → {new_tier} {new_name} ⭐0/{new_def['max_stars']}\n"
                f"{new_def['lore']}\n"
                f"<坐骑>"
            )
        else:
            return (
                f"消耗 {item_name} x{consumed}\n"
                f"进阶祝福值：{old_blessing} → {current_blessing}/{total}\n"
                f"本次未进阶成功\n"
                f"<坐骑>"
            )

    # ------------------------------------------------------------------ #
    #  显化选择
    # ------------------------------------------------------------------ #

    def choose_manifest(self, client_id: str, mount_id: str) -> str:
        """选择显化方向。"""
        player, error = self.require_player(client_id)
        if error:
            return error

        mount = self._ensure_mount_created(client_id)
        if not mount:
            return T.hint("坐骑数据异常，请联系管理员。")

        # 必须是 tier=10 且满星且未选择
        mount_def = self._get_mount_def(mount["mount_id"])
        if not mount_def:
            return T.hint("坐骑定义数据异常。")

        if mount_def["tier"] != 10 or mount_def["mount_type"] != "normal":
            return T.hint("当前坐骑不满足显化条件。<坐骑>")

        if mount["stars"] < mount_def["max_stars"]:
            return T.hint("需要满星进阶后才能选择显化方向。<坐骑>")

        if mount["manifest_chosen"] != 0:
            return T.hint("已选择显化方向，不可更改。<坐骑>")

        # 验证 mount_id 有效性
        target_def = self._get_mount_def(mount_id)
        if not target_def or target_def["mount_type"] != "manifest":
            return T.hint("无效的显化方向。<坐骑>")

        direction = target_def["manifest_direction"]

        # 更新
        self.db.execute(
            """
            UPDATE player_mounts
            SET mount_id=?, stars=0, blessing_value=0, blessing_expires_at='',
                manifest_chosen=1, updated_at=?
            WHERE client_id=?
            """,
            (mount_id, ts(), client_id),
        )

        return (
            f"已选择显化方向：**{direction}**\n"
            f"坐骑变为：{target_def['name']} ⭐0/{target_def['max_stars']}\n"
            f"大道显化：{target_def['lore']}"
            f"<坐骑>"
        )

    # ------------------------------------------------------------------ #
    #  帮助
    # ------------------------------------------------------------------ #

    @staticmethod
    def help_info(client_id: str) -> str:
        """展示全部坐骑信息。"""
        normal_defs = db.fetch_all(
            "SELECT * FROM mount_defs WHERE mount_type='normal' ORDER BY tier"
        )
        manifest_defs = db.fetch_all(
            "SELECT * FROM mount_defs WHERE mount_type='manifest' ORDER BY tier"
        )
        extreme_defs = db.fetch_all(
            "SELECT * FROM mount_defs WHERE mount_type='extreme' ORDER BY tier"
        )

        panel = T.panel()

        # 一阶至十阶
        panel.section("坐骑帮助 · 一阶至十阶")
        for d in normal_defs:
            item_name = _ITEM_NAMES.get(d["star_item_id"], d["star_item_id"])
            adv_name = _ITEM_NAMES.get(d["advance_item_id"], d["advance_item_id"])
            panel.line(
                f"**{d['tier']}阶 {d['name']}** · {d['lore']}"
            )
            panel.line(
                f"升星物品：{item_name}｜进阶物品：{adv_name}｜"
                f"祝福范围：{d['advance_blessing_min']}-{d['advance_blessing_max']}｜"
                f"祝福总值：{d['advance_blessing_total']}"
            )

        panel.blank()
        panel.line("进阶概率公式：P = (当前进阶祝福值 / 进阶祝福总值) 的 3 次方")
        panel.line("进阶祝福值有效期：24 小时（自首次使用进阶物品起算）")

        # 显化坐骑
        panel.hr()
        panel.section("坐骑帮助 · 显化坐骑")
        for d in manifest_defs:
            item_name = _ITEM_NAMES.get(d["star_item_id"], d["star_item_id"])
            adv_name = _ITEM_NAMES.get(d["advance_item_id"], d["advance_item_id"])
            panel.line(
                f"**{d['manifest_direction']} · {d['name']}** · {d['manifest_aphorism']}"
            )
            panel.line(
                f"升星物品：{item_name}｜进阶物品：{adv_name}｜"
                f"祝福范围：{d['advance_blessing_min']}-{d['advance_blessing_max']}｜"
                f"祝福总值：{d['advance_blessing_total']}"
            )

        # 极显化坐骑
        panel.hr()
        panel.section("坐骑帮助 · 极显化坐骑")
        for d in extreme_defs:
            item_name = _ITEM_NAMES.get(d["star_item_id"], d["star_item_id"])
            panel.line(
                f"**{d['manifest_direction']} · {d['name']}** · {d['lore']}"
            )
            panel.line(
                f"升星物品：{item_name}｜满星：{d['max_stars']}"
            )

        panel.blank()
        panel.line("极显化坐骑无法进阶，只能升星")

        return panel.render() + T.buttons("坐骑")


service = MountService(db)

__all__ = ["MountService", "service"]
