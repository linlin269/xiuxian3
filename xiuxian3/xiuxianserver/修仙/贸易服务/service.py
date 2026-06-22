"""商场跑商组件服务。"""

from __future__ import annotations

from ..format_text import T

from datetime import timedelta
from math import hypot

from ..common import (
    business_day,
    computed_weapon_attack,
    dt,
    load_json,
    money,
    now,
    parse_name_level,
    parse_weapon_ref,
    quality_factor,
    split_words,
    to_int,
    ts,
    weapon_id_label,
    weapon_label_name,
)
from ..constants import (
    TRADE_ACTIVE_WINDOW_DAYS,
    TRADE_BUY_FEE_RATE,
    TRADE_DAILY_REWARD_CAP_BASE,
    TRADE_DAILY_REWARD_CAP_LEVEL_BONUS,
    TRADE_DAILY_REWARD_RATE,
    TRADE_MAX_PROFIT_RATE,
    TRADE_PURE_ECONOMY_PRICE_FACTOR,
    TRADE_RESALE_LOCK_HOURS,
    TRADE_SELL_FEE_RATE,
    WORLD_COORD_MAX,
    WORLD_COORD_MIN,
)
from ..rules import (
    book_recycle_price_rate,
    book_recycle_single_cap,
    gem_recycle_price_rate,
    gem_recycle_single_cap,
    special_sell_price_rate,
    special_sell_soft_line,
    trade_daily_reward_thresholds,
    trade_global_soft_line,
    trade_player_soft_line,
    trade_profit_rate,
    weapon_recycle_price_rate,
    weapon_recycle_single_cap,
)
from ..sql import TRADE_LOCATION_DEMANDS, db, trade_group_for_type
from ..weapon_core import WeaponCore
from ..wormhole_service import WormholeService
from ..world_materials import WORLD_RECYCLE_CATEGORIES, WorldMaterialService


class TradeService(WeaponCore):
    """商场交易、背包出售和资产回收分流。"""

    def __init__(self, database) -> None:
        super().__init__(database)
        self.wormhole = WormholeService(database)
        self.world_material = WorldMaterialService(database)

    def market_price(self, client_id: str, item_name: str) -> str:
        """查看全图市价。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        item = self.item_def_by_name(item_name.strip())
        if not item:
            return T.hint(
                f"没有找到商品：{item_name.strip()}。",
                "跑商商品只来自 11 个普通城池特产；发送：探险列表 查看城池特产，或发送：商场推荐 查看当前可做路线。<探险列表><商场推荐>",
            )
        if not item["tradeable"]:
            return T.hint(f"{item['name']} 不是跑商商品。", "跑商只能查询特产商品；其他物品可发送：背包 或 纳戒<背包><纳戒>")
        rows = self.db.fetch_all("SELECT name FROM trade_locations ORDER BY name")
        panel = T.panel()
        panel.section(f"{item['name']}市价")
        for row in rows:
            buy, sell = self.price(row["name"], item["item_id"])
            panel.line(f"{row['name']}｜买 **{money(buy)}**｜卖 **{money(sell)}**")
        return panel.render()

    def buy(self, client_id: str, message: str) -> str:
        """商场购买。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        item_name, quantity = self._parse_name_quantity(message)
        if quantity <= 0:
            return T.hint("购买格式不正确。", "发送：商场购买 商品名 数量，例如：商场购买 星官旧简 3")
        item = self.item_def_by_name(item_name)
        if not item or not item["tradeable"]:
            return T.hint(f"{item_name} 不是可购买的跑商商品。", "发送：商场推荐 查看当前位置能做的买卖。<商场推荐>")
        location = self._location(player["location_name"])
        if not location:
            return T.hint("当前位置不是商场城池，无法购买跑商商品。", "普通商场和探险地点重合；发送：探险列表 查看城池，再发送：导航 地点名。<探险列表>")
        home = self.db.fetch_one("SELECT home_location FROM trade_goods WHERE item_id = ?", (item["item_id"],))
        if not home or home["home_location"] != player["location_name"]:
            return T.hint(f"{player['location_name']} 不出售 {item['name']}。", "发送：商场行情 商品名 查看各地价格，再导航到产地购买。")
        buy_price, _sell_price = self.price(player["location_name"], item["item_id"], save=True)
        total = buy_price * quantity
        fee = int(total * self._trade_fee_rate(client_id, TRADE_BUY_FEE_RATE))
        with self.db.transaction() as conn:
            ok, reason = self.can_add_backpack_conn(conn, client_id, item["item_id"], quantity)
            if not ok:
                return reason
            if not self.spend_stones_conn(conn, client_id, total + fee):
                return T.hint(f"源石不足，需要 {money(total + fee)}。", "发送：源库 或 取出源石 数量，或先签到、探险、出售物品。<源库><签到><探险>")
            self.add_backpack_conn(conn, client_id, item["item_id"], quantity)
            conn.execute(
                """
                INSERT INTO trade_records
                (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                VALUES (?, 'buy', ?, ?, ?, ?, ?, ?, ?)
                """,
                (client_id, item["item_id"], quantity, total, fee, player["location_name"], business_day(), ts()),
            )
            self._add_heat_conn(conn, player["location_name"], item["item_id"], buy_count=quantity)
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_buy_locks
                (client_id, item_id, location_name, last_buy_at, last_buy_price)
                VALUES (?, ?, ?, ?, ?)
                """,
                (client_id, item["item_id"], player["location_name"], ts(), buy_price),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '商场购买', ?, ?)",
                (client_id, f"item={item['item_id']}, quantity={quantity}, total={total}, fee={fee}", ts()),
            )
        return f"购买成功：{item['name']} x{quantity}，花费 {money(total)}，手续费 {money(fee)}。" + self.wormhole.try_discover(
            client_id, "trade_buy", player["location_name"]
        )

    def sell(self, client_id: str, message: str) -> str:
        """商场出售。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        if not self._location(player["location_name"]):
            return T.hint("当前位置不是商场城池。", "可以直接发送：出售 物品名 数量，系统会自动分流。<自动出售>")
        item_name, quantity = self._parse_name_quantity(message)
        if quantity <= 0:
            return T.hint("出售格式不正确。", "发送：商场出售 商品名 数量，例如：商场出售 星官旧简 3")
        item = self.item_def_by_name(item_name)
        if not item or not item["tradeable"]:
            return T.hint(f"{item_name} 不是可出售的跑商商品。", "发送：背包 查看可出售的跑商货物。<背包>")
        return self._sell_item(client_id, player["location_name"], item, quantity) + "<跑商奖励>"

    def sell_any(self, client_id: str, message: str) -> str:
        """统一出售入口；背包和纳戒物品都从这里按类型分流。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        item_name, quantity = self._parse_name_quantity(message)
        if quantity <= 0:
            return T.hint("出售格式不正确。", "发送：出售 物品名 数量，例如：出售 古妖丹 3。<背包><纳戒>")

        backpack_item = self.item_def_by_name(item_name)
        if backpack_item:
            return self._sell_backpack_item(client_id, player, backpack_item, quantity)

        ring_name, gem_level = parse_name_level(item_name)
        ring_item = self.ring_item_def_by_name(ring_name)
        if ring_item:
            category = str(ring_item["category"])
            if category == "技能书":
                return self._sell_book_item(client_id, ring_item, quantity)
            if category == "宝石":
                return self._sell_gem_item(client_id, ring_item, gem_level, quantity)
            return T.hint(f"{ring_item['name']} 不能出售。", "纳戒里的专属道具一般用于养成或活动，不进入出售清单。<纳戒>")

        weapon_text = self._sell_weapon_item(client_id, item_name, quantity)
        if weapon_text:
            return weapon_text
        return T.hint(f"没有找到可出售物品：{item_name}。", "发送：背包 或 纳戒 查看准确名称。<背包><纳戒>")

    def sell_all(self, client_id: str, message: str) -> str:
        """批量出售纳戒资产。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        kind = "".join(split_words(message))
        if kind == "武器":
            location = self.recycle_location_by_type("weapon")
            if not location:
                return T.hint("武器回收地点不存在。", "请检查 recycle_locations 配置。")
            self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售全部武器")
            return self._recycle_weapons(client_id, location, all_spares=True)
        if kind == "宝石":
            location = self.recycle_location_by_type("gem")
            if not location:
                return T.hint("宝石回收地点不存在。", "请检查 recycle_locations 配置。")
            self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售全部宝石")
            return self._recycle_gems(client_id, location)
        if kind == "技能书":
            location = self.recycle_location_by_type("book")
            if not location:
                return T.hint("技能书回收地点不存在。", "请检查 recycle_locations 配置。")
            self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售全部技能书")
            return self._recycle_books(client_id, location)
        return T.hint("出售全部格式不正确。", "只能发送：出售全部 武器 / 出售全部 宝石 / 出售全部 技能书。<纳戒>")

    def treasure_map(self, client_id: str, message: str = "") -> str:
        """查看当前位置或指定城池藏宝图。"""

        return self.world_material.treasure_status(client_id, message)

    def treasure_bid(self, client_id: str, message: str) -> str:
        """给当前位置城池藏宝图出价。"""

        return self.world_material.treasure_bid(client_id, message)

    def treasure_claim(self, client_id: str) -> str:
        """领取已归属或脚下的藏宝图。"""

        return self.world_material.treasure_claim(client_id)

    def auto_sell(self, client_id: str) -> str:
        """清空背包里的可流通物品，并提示纳戒资产的出售方式。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        rows = self.backpack_rows(client_id)
        texts: list[str] = []
        sold_count = 0
        origin = {
            "name": str(player["location_name"]),
            "x": int(player["x"]),
            "y": int(player["y"]),
        }

        for row in rows:
            item = self.item_def(str(row["item_id"]))
            if not item or not int(item["tradeable"]):
                continue
            text = self._sell_trade_item_auto(client_id, item, int(row["quantity"]), discover=False)
            if not text:
                continue
            texts.append(text)
            sold_count += 1

        if self._has_world_materials(client_id):
            city = self._current_or_nearest_trade_location(client_id)
            if city:
                self._move_player_to_location(client_id, city["name"], int(city["x"]), int(city["y"]), "自动出售世界物资")
                texts.append(self.world_material.recycle(client_id, "全部"))
                sold_count += 1

        if self._has_special_loot(client_id):
            texts.append(self.special_auto_sell(client_id, origin))
            sold_count += 1
            special_auto_moved = True
        else:
            special_auto_moved = False

        for item, quantity in self._misc_backpack_rows(client_id):
            texts.append(self._sell_misc_backpack_item(client_id, item, quantity))
            sold_count += 1

        asset_lines = self._ring_asset_hint_lines(client_id)
        if sold_count <= 0 and not asset_lines:
            return T.hint("背包里没有可出售物品。", "继续探险、跑商或挑战后可发送：自动出售。<探险><商场推荐>")

        panel = T.panel()
        panel.section("自动出售")
        if texts:
            for text in texts:
                for line in str(text).splitlines():
                    value = line.strip()
                    if value:
                        panel.line(value)
        else:
            panel.line("背包里没有需要清理的可流通物品。")
        if asset_lines:
            panel.hr()
            panel.section("纳戒资产")
            panel.lines(asset_lines)
        current_player = self.player(client_id) or {}
        current_name = str(current_player.get("location_name") or origin["name"])
        current_x = int(current_player.get("x") or origin["x"])
        current_y = int(current_player.get("y") or origin["y"])
        moved = current_name != origin["name"] or current_x != origin["x"] or current_y != origin["y"]
        if moved and not special_auto_moved:
            panel.hr()
            panel.section("位置")
            panel.line(f"当前位置：{current_name} ({current_x},{current_y})")
            panel.line(f"出发地：{origin['name']} ({origin['x']},{origin['y']})")
        self.refresh_titles(client_id)
        buttons = []
        if moved and not special_auto_moved:
            buttons.append(f"导航 {origin['x']} {origin['y']}:回原处")
        return panel.render() + T.buttons(*buttons)

    def recommend(self, client_id: str) -> str:
        """按单位负重收益推荐当前能买的跑商路线。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        current = player["location_name"]
        if not self._location(current):
            return T.hint("当前位置不是商场城池。", "普通商场和探险地点重合；发送：探险列表 查看城池，再发送：导航 地点名。<探险列表>")
        options = self._trade_options(client_id, player)
        if not options:
            return T.hint("当前没有能购买且有利润的跑商路线。", "确认随身源石和背包空间足够，或换一个商场地点再试。")
        panel = T.panel()
        panel.section(f"{current}跑商推荐")
        for index, option in enumerate(options[:3], start=1):
            panel.line(
                f"{index}. 商场购买 {option['item_name']} {option['quantity']} -> "
                f"导航 {option['target']} -> 商场出售 {option['item_name']} {option['quantity']}\n"
                f"预计净赚 **{money(option['total_profit'])}**｜单件 **{money(option['unit_profit'])}**"
            )
        return (
            panel.render()
            + f"<商场购买 {options[0]['item_name']} {options[0]['quantity']}>"
            + f"<导航 {options[0]['target']}>"
            + f"<商场出售 {options[0]['item_name']} {options[0]['quantity']}>"
            + f"<自动出售>"
        )

    def records(self, client_id: str) -> str:
        """查看交易记录。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        rows = self.db.fetch_all(
            """
            SELECT r.*, COALESCE(i.name, e.name) AS name
            FROM trade_records r
            LEFT JOIN item_defs i ON i.item_id = r.item_id
            LEFT JOIN ring_item_defs e ON e.ring_item_id = r.item_id
            WHERE r.client_id = ?
            ORDER BY r.created_at DESC
            LIMIT 10
            """,
            (client_id,),
        )
        if not rows:
            return T.hint("暂无跑商记录。", "发送：商场推荐 查看路线，再发送：商场购买 商品名 数量<商场推荐>")
        panel = T.panel()
        panel.section("跑商记录")
        for row in rows:
            panel.line(
                f"{self._action_text(row['action'])}｜{(row['name'] or row['item_id'])} x{row['quantity']}｜"
                f"**{money(row['total_price'])}**｜{row['location_name']}"
            )
        return panel.render()

    def trade_curve(self, client_id: str) -> str:
        """查看普通跑商收益曲线。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        panel = T.panel()
        panel.section("跑商收益曲线")
        with self.db.transaction() as conn:
            market_state = self._trade_market_state_conn(conn, client_id)
        current_rate = trade_profit_rate(
            market_state["player_used"],
            market_state["global_used"],
            market_state["player_soft_line"],
            market_state["global_soft_line"],
        )
        panel.line(f"近{TRADE_ACTIVE_WINDOW_DAYS}天活跃：**{market_state['active_count']}** 人")
        panel.line(
            f"今日个人普通出售：**{market_state['player_used']}/{market_state['player_soft_line']}** 件收益线"
        )
        panel.line(
            f"今日全服普通出售：**{market_state['global_used']}/{market_state['global_soft_line']}** 件收益线"
        )
        panel.line(f"当前普通跑商利润倍率：**{int(current_rate * 100)}%**，只影响利润部分，不限制买卖")
        panel.line("当前跑商特产都是纯经济货物；价格基准更高，但仍计入普通跑商收益线")
        panel.line(f"同地点买入后 **{TRADE_RESALE_LOCK_HOURS}** 小时内不能原地出售")
        panel.line(f"跑商单件利润率最高约 **{int(TRADE_MAX_PROFIT_RATE * 100)}%**")
        panel.line(f"今日特殊收购价：{self._special_sell_rate_text(client_id, player['level'])}")
        return panel.render()

    def daily_reward(self, client_id: str) -> str:
        """领取每日普通跑商额外奖励。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        day = business_day()
        with self.db.transaction() as conn:
            claimed = conn.execute(
                """
                SELECT reward FROM trade_daily_rewards
                WHERE client_id = ? AND business_day = ?
                """,
                (client_id, day),
            ).fetchone()
            if claimed:
                return T.hint("今日跑商奖励已经领取。", "每日 04:00 后重置，明天继续跑商。")

            stat = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN action = 'sell' THEN quantity ELSE 0 END), 0) AS quantity,
                    COALESCE(SUM(
                        CASE
                            WHEN action = 'sell' THEN total_price - fee
                            WHEN action = 'buy' THEN -(total_price + fee)
                            ELSE 0
                        END
                    ), 0) AS net_profit
                FROM trade_records
                WHERE client_id = ?
                  AND business_day = ?
                  AND action IN ('buy', 'sell')
                """,
                (client_id, day),
            ).fetchone()
            quantity = int(stat["quantity"] if stat else 0)
            net_profit = int(stat["net_profit"] if stat else 0)
            if quantity <= 0:
                return T.hint("今天还没有普通跑商出售记录。", "发送：商场推荐，买入后导航到外地，再发送：商场出售 商品名 数量。<商场推荐>")
            if net_profit <= 0:
                return T.hint("今日普通跑商还没有形成净利润。", "先把货物卖到更高价地点，再领取跑商奖励。<商场推荐>")
            market_state = self._trade_market_state_conn(conn, client_id)
            min_quantity, min_net = trade_daily_reward_thresholds(market_state["player_soft_line"])
            if quantity < min_quantity and net_profit < min_net:
                return T.hint(
                    "今日普通跑商量还不够领取奖励。",
                    f"至少出售 {min_quantity} 件跑商商品，或普通跑商净利润达到 {money(min_net)}。",
                )

            cap = TRADE_DAILY_REWARD_CAP_BASE + int(player["level"]) * TRADE_DAILY_REWARD_CAP_LEVEL_BONUS
            reward = min(cap, max(1, int(net_profit * TRADE_DAILY_REWARD_RATE)))
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO trade_daily_rewards
                (client_id, business_day, sell_quantity, net_profit, reward, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_id, day, quantity, net_profit, reward, ts()),
            )
            if cursor.rowcount <= 0:
                return T.hint("今日跑商奖励已经领取。", "每日 04:00 后重置，明天继续跑商。")
            self._grant_source_stones_conn(conn, client_id, reward)
            self._write_game_log_conn(
                conn,
                client_id,
                "跑商奖励",
                f"day={day}, quantity={quantity}, net_profit={net_profit}, reward={reward}",
            )
        return f"跑商奖励领取成功：今日出售 {quantity} 件，普通跑商净利润 {money(net_profit)}，奖励源石 {money(reward)}。"

    def special_sell(self, client_id: str, message: str) -> str:
        """在特殊收购地点出售战利品。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        buyer = self._special_buyer(player["location_name"])
        if not buyer:
            return T.hint("当前位置不是特殊收购地点。", "直接发送：出售 物品名 数量，系统会自动前往对应收购点。<背包>")

        item_name, quantity = self._parse_name_quantity(message)
        if quantity <= 0:
            return T.hint("出售格式不正确。", "发送：出售 物品名 数量，例如：出售 古妖丹 2。<背包>")
        item = self.item_def_by_name(item_name)
        if not item:
            return T.hint(f"没有找到物品：{item_name}。", "发送：背包 确认物品名称。<背包>")
        allowed = set(str(buyer["item_ids"]).split(","))
        if item["item_id"] not in allowed:
            return T.hint(f"{buyer['buyer_name']} 不收 {item['name']}。", "发送：出售 物品名 数量，系统会自动选择对应收购点。<出售>")

        raw_total = int(item["base_price"] * float(buyer["price_factor"]) * quantity)
        war_prep_text = ""
        with self.db.transaction() as conn:
            used = self._special_sell_used_conn(conn, client_id)
            rate = special_sell_price_rate(player["level"], used + raw_total // 2)
            total = max(1, int(raw_total * rate))
            if not self.remove_backpack_conn(conn, client_id, item["item_id"], quantity):
                return T.hint(f"背包中 {item['name']} 数量不足。", "发送：背包 确认数量，或继续探险获取。<背包>")
            self._grant_source_stones_conn(conn, client_id, total)
            conn.execute(
                """
                INSERT INTO trade_records
                (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                VALUES (?, 'special_sell', ?, ?, ?, 0, ?, ?, ?)
                """,
                (client_id, item["item_id"], quantity, total, buyer["buyer_name"], business_day(), ts()),
            )
            self._write_game_log_conn(
                conn,
                client_id,
                "特殊出售",
                f"item={item['item_id']}, quantity={quantity}, total={total}, buyer={buyer['buyer_name']}",
            )
            war_prep = self.world_material.add_war_prep_conn(conn, buyer["buyer_name"], item, quantity, client_id)
            war_prep_text = str(war_prep.get("text") or "")
        return (
            f"战利品出售成功：{item['name']} x{quantity}，"
            f"原价 {money(raw_total)}，当前倍率 {int(rate * 100)}%，收入 {money(total)}。"
            + (f"\n{war_prep_text}。" if war_prep_text else "")
            + self.wormhole.try_discover(client_id, "special_sell", buyer["buyer_name"])
        )

    def special_auto_sell(self, client_id: str, origin: dict | None = None) -> str:
        """自动导航并出售背包里的所有特殊收购物。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None

        buyers = self._special_buyers_ordered()
        sell_plan = self._special_auto_sell_plan(client_id, buyers)
        if not sell_plan:
            return T.hint("背包里没有可出售的战利品。", "继续探险获取战利品，或发送：背包 查看库存。<背包>")

        total_gain = 0
        last_buyer: dict | None = None
        origin_name = str(origin.get("name")) if origin else str(player["location_name"])
        origin_x = int(origin.get("x")) if origin else to_int(player["x"])
        origin_y = int(origin.get("y")) if origin else to_int(player["y"])
        lines = ["战利品自动出售"]
        with self.db.transaction() as conn:
            used = self._special_sell_used_conn(conn, client_id)
            for buyer, items in sell_plan:
                sold_lines: list[str] = []
                war_prep_text = ""
                for item in items:
                    quantity = int(item["quantity"])
                    raw_total = int(item["base_price"] * float(buyer["price_factor"]) * quantity)
                    rate = special_sell_price_rate(player["level"], used + raw_total // 2)
                    total = max(1, int(raw_total * rate))
                    if not self.remove_backpack_conn(conn, client_id, item["item_id"], quantity):
                        sold_lines.append(f"{item['name']} x{quantity} 库存不足，已跳过")
                        continue

                    self._grant_source_stones_conn(conn, client_id, total)
                    conn.execute(
                        """
                        INSERT INTO trade_records
                        (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                        VALUES (?, 'special_auto_sell', ?, ?, ?, 0, ?, ?, ?)
                        """,
                        (client_id, item["item_id"], quantity, total, buyer["buyer_name"], business_day(), ts()),
                    )
                    used += total
                    total_gain += total
                    sold_lines.append(f"{item['name']} x{quantity}，原价 {money(raw_total)}，" f"倍率 {int(rate * 100)}%，收入 {money(total)}")
                    war_prep = self.world_material.add_war_prep_conn(conn, buyer["buyer_name"], item, quantity, client_id)
                    if war_prep.get("text"):
                        war_prep_text = str(war_prep["text"])

                if not sold_lines:
                    continue
                last_buyer = buyer
                lines.append(f"自动导航：{buyer['buyer_name']} ({buyer['x']},{buyer['y']})")
                lines.extend(sold_lines)
                if war_prep_text:
                    lines.append(war_prep_text)

            if not last_buyer:
                return T.hint("没有成功出售的特殊物品。", "发送：背包 确认可出售物品数量。<背包>")
            conn.execute(
                "UPDATE players SET location_name = ?, x = ?, y = ? WHERE client_id = ?",
                (last_buyer["buyer_name"], last_buyer["x"], last_buyer["y"], client_id),
            )
            self._write_game_log_conn(
                conn,
                client_id,
                "特殊自动出售",
                f"total={total_gain}, location={last_buyer['buyer_name']}",
            )

        lines.append(f"合计收入：{money(total_gain)}")
        lines.append(f"当前位置：{last_buyer['buyer_name']} ({last_buyer['x']},{last_buyer['y']})")
        lines.append(f"此处是特殊收购点；继续探险或跑商前可回到原位置：{origin_name} ({origin_x},{origin_y})。")
        notice = self.wormhole.try_discover(client_id, "special_auto_sell", last_buyer["buyer_name"])
        if notice:
            lines.append(notice.strip())
        panel = T.panel()
        panel.section("战利品自动出售")
        for line in lines[1:]:
            panel.line(line)
        return panel.render() + T.buttons(f"导航 {origin_x} {origin_y}:回原处", "探险列表", "自动出售")

    def navigate(self, client_id: str, message: str) -> str:
        """导航到地点或精确坐标。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        parts = split_words(message)
        if len(parts) == 1:
            location = self._navigation_location(parts[0])
            if not location:
                return T.hint("没有找到可导航地点。", "普通城池看探险列表；特殊收购和回收点由出售/自动出售自动前往。<探险列表><自动出售>")
            name = str(location["name"])
            x = int(location["x"])
            y = int(location["y"])
            wormhole_location = name
        elif len(parts) == 2:
            coordinate = self._parse_coordinates(parts)
            if not coordinate:
                return T.hint("导航格式不正确。", "发送：导航 地点名，或发送：导航 x y")
            x, y = coordinate
            if not self._coordinate_in_world(x, y):
                return T.hint(
                    f"坐标超出修仙界范围，左下角 ({WORLD_COORD_MIN},{WORLD_COORD_MIN})，右上角 ({WORLD_COORD_MAX},{WORLD_COORD_MAX})。",
                    "发送：导航 x y，其中 x 和 y 都需要在世界范围内。",
                )
            location = self._known_location_at(x, y)
            name = str(location["name"]) if location else self._wilderness_name(x, y)
            wormhole_location = name if location else ""
        else:
            return T.hint("导航格式不正确。", "发送：导航 地点名，或发送：导航 x y")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE players SET location_name = ?, x = ?, y = ? WHERE client_id = ?",
                (name, x, y, client_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '导航', ?, ?)",
                (client_id, f"location={name}, x={x}, y={y}", ts()),
            )
        notice = self.wormhole.try_discover(client_id, "navigate", wormhole_location) if wormhole_location else ""
        return f"已到达 {name} ({x},{y})。" + notice + "<探险><商场推荐><自动出售>"

    def price(self, location_name: str, item_id: str, save: bool = False) -> tuple[int, int]:
        """获取当天价格。

        跑商价格分三层：
        1. 物品自身基础价：写在 item_defs.base_price。
        2. 供需位置：产地便宜，离产地越远越贵。
        3. 地点需求：不同地点偏好不同类型货物。

        每日波动和买卖热度只做轻微扰动，不再决定货物的核心价值。
        """

        day = business_day()
        item = self.item_def(item_id)
        if not item:
            return 0, 0
        item_effect = load_json(item.get("effect"), {})
        trade_type = str(item_effect.get("trade_type") or "")
        trade_group = self._trade_group(item)
        home = self.db.fetch_one("SELECT home_location FROM trade_goods WHERE item_id = ?", (item_id,))
        home_name = home["home_location"] if home else location_name
        loc = self._location(location_name)
        home_loc = self._location(home_name)
        distance = hypot((loc["x"] - home_loc["x"]), (loc["y"] - home_loc["y"])) if loc and home_loc else 0
        daily_wave = 1.0
        supply_factor = self._supply_factor(distance, location_name == home_name)
        demand_factor = self._demand_factor(location_name, trade_type)
        heat = (
            self.db.fetch_one(
                """
                SELECT buy_count, sell_count FROM trade_heat
                WHERE location_name = ? AND item_id = ? AND business_day = ?
                """,
                (location_name, item_id, day),
            )
            or {"buy_count": 0, "sell_count": 0}
        )
        market_price = item["base_price"] * supply_factor * demand_factor * daily_wave * self._group_price_factor(trade_group)
        buy_price = max(1, int(market_price * min(1.5, 1.04 + heat["buy_count"] * 0.01)))
        sell_price = max(
            1,
            int(market_price * 0.82 * max(0.65, 1.0 - heat["sell_count"] * 0.008)),
        )
        if save:
            self.db.execute(
                """
                INSERT OR REPLACE INTO trade_prices
                (location_name, item_id, buy_price, sell_price, business_day)
                VALUES (?, ?, ?, ?, ?)
                """,
                (location_name, item_id, buy_price, sell_price, day),
            )
        return buy_price, sell_price

    @staticmethod
    def _supply_factor(distance: float, is_home: bool) -> float:
        """产地供给充足，所以便宜；离产地越远，运输价越高。"""

        if is_home:
            return 0.82
        return 0.96 + min(0.36, distance / 2600)

    @staticmethod
    def _demand_factor(location_name: str, trade_type: str) -> float:
        """读取地点对某类货物的需求倍率。"""

        if not trade_type:
            return 0.98
        return TRADE_LOCATION_DEMANDS.get(location_name, {}).get(trade_type, 0.98)

    def _trade_options(self, client_id: str, player: dict) -> list[dict]:
        """计算当前位置可执行的跑商路线。"""

        current = player["location_name"]
        buy_fee_rate = self._trade_fee_rate(client_id, TRADE_BUY_FEE_RATE)
        sell_fee_rate = self._trade_fee_rate(client_id, TRADE_SELL_FEE_RATE)
        source_stones = int(player["source_stones"])
        options: list[dict] = []
        with self.db.transaction() as conn:
            market_state = self._trade_market_state_conn(conn, client_id)

        for item in self._location_goods(current):
            buy_price, _ = self.price(current, item["item_id"])
            quantity = self._max_buy_quantity(client_id, item, buy_price, buy_fee_rate, source_stones)
            if quantity <= 0:
                continue
            for loc in self.db.fetch_all("SELECT name, x, y FROM trade_locations"):
                if loc["name"] == current:
                    continue
                _, sell_price = self.price(loc["name"], item["item_id"])
                sell_price = self._profit_capped_sell_price(buy_price, sell_price)
                profit_rate = self._trade_profit_rate_for_quantity(market_state, quantity)
                sell_price = self._profit_adjusted_sell_price(buy_price, sell_price, profit_rate)

                buy_total = buy_price * quantity
                buy_fee = int(buy_total * buy_fee_rate)
                sell_total = sell_price * quantity
                sell_fee = int(sell_total * sell_fee_rate)
                unit_profit = int(sell_price * (1 - sell_fee_rate)) - int(buy_price * (1 + buy_fee_rate))
                total_profit = sell_total - sell_fee - buy_total - buy_fee
                if total_profit <= 0:
                    continue

                options.append(
                    {
                        "item_id": item["item_id"],
                        "item_name": item["name"],
                        "quantity": quantity,
                        "target": loc["name"],
                        "target_x": loc["x"],
                        "target_y": loc["y"],
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "buy_total": buy_total,
                        "buy_fee": buy_fee,
                        "sell_total": sell_total,
                        "sell_fee": sell_fee,
                        "unit_profit": unit_profit,
                        "total_profit": total_profit,
                        "profit_per_weight": total_profit / max(1, int(item["weight"]) * quantity),
                    }
                )

        options.sort(key=lambda row: (row["total_profit"], row["profit_per_weight"]), reverse=True)
        return options

    def _max_buy_quantity(self, client_id: str, item: dict, buy_price: int, buy_fee_rate: float, source_stones: int) -> int:
        """计算当前源石和背包最多能买多少。"""

        high = min(int(item["stack_limit"]), source_stones // max(1, buy_price))
        low = 0
        with self.db.transaction() as conn:
            while low < high:
                mid = (low + high + 1) // 2
                total = buy_price * mid
                fee = int(total * buy_fee_rate)
                ok, _reason = self.can_add_backpack_conn(conn, client_id, item["item_id"], mid)
                if ok and total + fee <= source_stones:
                    low = mid
                else:
                    high = mid - 1
        return low

    def _sell_item(self, client_id: str, location_name: str, item: dict, quantity: int, discover: bool = True) -> str:
        """出售一类背包物品。"""

        _buy, sell_price = self.price(location_name, item["item_id"], save=True)
        locked_text = self._resale_lock_text(client_id, item["item_id"], location_name)
        if locked_text:
            return locked_text
        last_buy_price = self._last_trade_buy_price(client_id, item["item_id"])
        sell_price = self._profit_capped_sell_price(last_buy_price, sell_price)
        profit_rate = 1.0
        medicine_text = ""
        with self.db.transaction() as conn:
            market_state = self._trade_market_state_conn(conn, client_id)
            profit_rate = self._trade_profit_rate_for_quantity(market_state, quantity)
            sell_price = self._profit_adjusted_sell_price(last_buy_price, sell_price, profit_rate)
            total = sell_price * quantity
            fee = int(total * self._trade_fee_rate(client_id, TRADE_SELL_FEE_RATE))
            if not self.remove_backpack_conn(conn, client_id, item["item_id"], quantity):
                return T.hint(f"背包中 {item['name']} 数量不足。", "发送：背包 确认数量，或继续探险/购买获取。")
            self._grant_source_stones_conn(conn, client_id, total - fee)
            conn.execute(
                """
                INSERT INTO trade_records
                (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                VALUES (?, 'sell', ?, ?, ?, ?, ?, ?, ?)
                """,
                (client_id, item["item_id"], quantity, total, fee, location_name, business_day(), ts()),
            )
            self._add_heat_conn(conn, location_name, item["item_id"], sell_count=quantity)
            self._write_game_log_conn(
                conn,
                client_id,
                "商场出售",
                f"item={item['item_id']}, quantity={quantity}, total={total}, fee={fee}",
            )
            medicine_text = self.world_material.maybe_carry_medicine(conn, client_id, location_name)
        text = f"出售成功：{item['name']} x{quantity}，收入 {money(total - fee)}，手续费 {money(fee)}。"
        if last_buy_price > 0 and profit_rate < 0.995:
            text += f" 今日跑商利润倍率 {int(profit_rate * 100)}%。"
        if medicine_text:
            text += "\n商路顺药：" + medicine_text
        if discover:
            text += self.wormhole.try_discover(client_id, "trade_sell", location_name)
        return text

    def _sell_backpack_item(self, client_id: str, player: dict, item: dict, quantity: int) -> str:
        """按背包物品类型自动选择出售去路。"""

        category, _subtype = self.world_material.item_world_type(item)
        if int(item["tradeable"]):
            text = self._sell_trade_item_auto(client_id, item, quantity)
            return text or T.hint(f"{item['name']} 暂无可出售商场。", "换个城池或先查看商场推荐。<商场推荐>")
        if category in WORLD_RECYCLE_CATEGORIES:
            city = self._current_or_nearest_trade_location(client_id)
            if not city:
                return T.hint("当前没有可承接世界物资的城池。", "请检查 11 个普通城池配置。")
            self._move_player_to_location(client_id, city["name"], int(city["x"]), int(city["y"]), "出售世界物资")
            return self.world_material.recycle(client_id, f"{item['name']} {quantity}")
        if category == "战利品":
            buyer = self._buyer_for_item(str(item["item_id"]))
            if not buyer:
                return T.hint(f"{item['name']} 暂无特殊收购点。", "这类战利品暂时不能出售。")
            self._move_player_to_location(client_id, buyer["buyer_name"], int(buyer["x"]), int(buyer["y"]), "出售战利品")
            return self.special_sell(client_id, f"{item['name']} {quantity}")
        return self._sell_misc_backpack_item(client_id, item, quantity)

    def _sell_trade_item_auto(self, client_id: str, item: dict, quantity: int, discover: bool = True) -> str:
        """自动选择收益最高且不被原地转售锁卡住的普通商场出售点。"""

        target = self._best_trade_sell_location(client_id, item, quantity)
        if not target:
            return ""
        self._move_player_to_location(client_id, target["name"], int(target["x"]), int(target["y"]), "出售商场货物")
        return self._sell_item(client_id, target["name"], item, quantity, discover=discover) + "<跑商奖励>"

    def _best_trade_sell_location(self, client_id: str, item: dict, quantity: int) -> dict | None:
        """按净收入挑一个商场出售点。"""

        last_buy_price = self._last_trade_buy_price(client_id, str(item["item_id"]))
        sell_fee_rate = self._trade_fee_rate(client_id, TRADE_SELL_FEE_RATE)
        with self.db.transaction() as conn:
            market_state = self._trade_market_state_conn(conn, client_id)
        profit_rate = self._trade_profit_rate_for_quantity(market_state, quantity)
        best: dict | None = None
        best_value = -1
        for row in self.db.fetch_all("SELECT name, x, y FROM trade_locations"):
            if self._resale_lock_text(client_id, str(item["item_id"]), row["name"]):
                continue
            _buy, sell_price = self.price(row["name"], str(item["item_id"]), save=True)
            sell_price = self._profit_capped_sell_price(last_buy_price, sell_price)
            sell_price = self._profit_adjusted_sell_price(last_buy_price, sell_price, profit_rate)
            total = sell_price * quantity
            net = total - int(total * sell_fee_rate)
            if net > best_value:
                best_value = net
                best = dict(row)
        return best

    def _sell_misc_backpack_item(self, client_id: str, item: dict, quantity: int) -> str:
        """兜底甩掉背包杂物；背包是流通货仓，不做误清保护。"""

        amount = max(1, int(quantity))
        base_price = max(0, int(item.get("base_price") or 0))
        total = base_price * amount
        with self.db.transaction() as conn:
            if not self.remove_backpack_conn(conn, client_id, str(item["item_id"]), amount):
                return f"{item['name']} x{amount} 库存不足，已跳过。"
            if total > 0:
                self._grant_source_stones_conn(conn, client_id, total)
            conn.execute(
                """
                INSERT INTO trade_records
                (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                VALUES (?, 'misc_sell', ?, ?, ?, 0, ?, ?, ?)
                """,
                (client_id, item["item_id"], amount, total, "杂物甩卖", business_day(), ts()),
            )
            self._write_game_log_conn(conn, client_id, "杂物甩卖", f"item={item['item_id']}, quantity={amount}, total={total}")
        if total > 0:
            return f"甩卖杂物：{item['name']} x{amount}，收入 {money(total)}。"
        return f"甩掉杂物：{item['name']} x{amount}，未得源石。"

    def _sell_book_item(self, client_id: str, item: dict, quantity: int) -> str:
        """出售一本或多本纳戒技能书。"""

        location = self.recycle_location_by_type("book")
        if not location:
            return T.hint("技能书回收地点不存在。", "请检查 recycle_locations 配置。")
        self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售技能书")
        return self._recycle_books(client_id, location, item, quantity)

    def _sell_gem_item(self, client_id: str, item: dict, gem_level: int | None, quantity: int) -> str:
        """出售纳戒宝石。"""

        location = self.recycle_location_by_type("gem")
        if not location:
            return T.hint("宝石回收地点不存在。", "请检查 recycle_locations 配置。")
        self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售宝石")
        return self._recycle_gems(client_id, location, item, gem_level, quantity)

    def _sell_weapon_item(self, client_id: str, text: str, quantity: int) -> str:
        """按 ID 或名称出售武器；没有匹配时返回空字符串。"""

        self.ensure_starter_weapon(client_id)
        rows = self.weapons(client_id)
        weapon_id = parse_weapon_ref(text)
        if weapon_id > 0:
            ids = [weapon_id]
        else:
            matches = [row for row in rows if weapon_label_name(row) == text.strip()]
            if not matches:
                return ""
            ids = [int(row["weapon_id"]) for row in matches[: max(1, quantity)]]
        location = self.recycle_location_by_type("weapon")
        if not location:
            return T.hint("武器回收地点不存在。", "请检查 recycle_locations 配置。")
        self._move_player_to_location(client_id, location["name"], int(location["x"]), int(location["y"]), "出售武器")
        return self._recycle_weapons(client_id, location, ids)

    def _current_or_nearest_trade_location(self, client_id: str) -> dict | None:
        """优先使用当前城池，不在城池时选择最近普通商场城池。"""

        player = self.player(client_id)
        if not player:
            return None
        current = self._location(str(player["location_name"]))
        if current:
            return dict(current)
        rows = self.db.fetch_all("SELECT name, x, y FROM trade_locations")
        if not rows:
            return None
        x = int(player["x"])
        y = int(player["y"])
        return dict(min(rows, key=lambda row: hypot(int(row["x"]) - x, int(row["y"]) - y)))

    def _move_player_to_location(self, client_id: str, name: str, x: int, y: int, action: str) -> None:
        """经济行为需要自动换点时，统一更新玩家位置并记账。"""

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE players SET location_name = ?, x = ?, y = ? WHERE client_id = ?",
                (name, int(x), int(y), client_id),
            )
            self._write_game_log_conn(conn, client_id, action, f"location={name}, x={x}, y={y}")

    def recycle_location_by_type(self, recycle_type: str) -> dict | None:
        """读取某类纳戒资产的回收点。"""

        row = self.db.fetch_one(
            "SELECT * FROM recycle_locations WHERE recycle_type = ? ORDER BY rowid LIMIT 1",
            (recycle_type,),
        )
        return dict(row) if row else None

    def _buyer_for_item(self, item_id: str) -> dict | None:
        """读取收购指定战利品的特殊收购点。"""

        for buyer in self._special_buyers_ordered():
            if item_id in set(str(buyer["item_ids"]).split(",")):
                return dict(buyer)
        return None

    def _has_world_materials(self, client_id: str) -> bool:
        """背包里是否还有可入城池的世界物资。"""

        for row in self.backpack_rows(client_id):
            category, _subtype = self.world_material.item_world_type(row)
            if category in WORLD_RECYCLE_CATEGORIES:
                return True
        return False

    def _has_special_loot(self, client_id: str) -> bool:
        """背包里是否还有特殊收购战利品。"""

        for row in self.backpack_rows(client_id):
            category, _subtype = self.world_material.item_world_type(row)
            if category == "战利品" and self._buyer_for_item(str(row["item_id"])):
                return True
        return False

    def _misc_backpack_rows(self, client_id: str) -> list[tuple[dict, int]]:
        """自动出售最后兜底的背包杂物。"""

        result: list[tuple[dict, int]] = []
        for row in self.backpack_rows(client_id):
            item = self.item_def(str(row["item_id"]))
            if not item:
                continue
            category, _subtype = self.world_material.item_world_type(item)
            if int(item["tradeable"]) or category in WORLD_RECYCLE_CATEGORIES or category == "战利品":
                continue
            result.append((item, int(row["quantity"])))
        return result

    def _ring_asset_hint_lines(self, client_id: str) -> list[str]:
        """自动出售后提示纳戒资产的明确出售命令。"""

        lines: list[str] = []
        weapon_rows = self.weapons(client_id)
        spare_count = sum(1 for row in weapon_rows if not int(row["equipped"]))
        if spare_count > 0:
            lines.append(f"备用武器 {spare_count} 件：发送 出售全部 武器。")
        gem_quantity = sum(int(row["quantity"]) for row in self.gem_rows(client_id))
        if gem_quantity > 0:
            lines.append(f"宝石 {gem_quantity} 颗：发送 出售全部 宝石。")
        book_row = self.db.fetch_one(
            """
            SELECT COALESCE(SUM(r.quantity), 0) AS quantity
            FROM ring_items r
            JOIN ring_item_defs e ON e.ring_item_id = r.ring_item_id
            WHERE r.client_id = ? AND e.category = '技能书'
            """,
            (client_id,),
        )
        book_quantity = int(book_row["quantity"] if book_row else 0)
        if book_quantity > 0:
            lines.append(f"技能书 {book_quantity} 本：发送 出售全部 技能书。")
        return lines

    def _resale_lock_text(self, client_id: str, item_id: str, location_name: str) -> str:
        """同地点刚买入的货物不能立刻原地卖出。"""

        row = self.db.fetch_one(
            """
            SELECT last_buy_at FROM trade_buy_locks
            WHERE client_id = ? AND item_id = ? AND location_name = ?
            """,
            (client_id, item_id, location_name),
        )
        if not row:
            return ""
        last_buy_at = dt(row["last_buy_at"])
        if not last_buy_at:
            return ""
        passed = now() - last_buy_at
        lock_time = timedelta(hours=TRADE_RESALE_LOCK_HOURS)
        if passed >= lock_time:
            return ""
        left_minutes = max(1, int((lock_time - passed).total_seconds() // 60) + 1)
        return T.hint(f"这批货刚在本地买入，{left_minutes} 分钟后才能原地出售。", "先导航到其他商场出售，或等待冷却结束。")

    def _last_trade_buy_price(self, client_id: str, item_id: str) -> int:
        """读取最近一次普通跑商买入价。"""

        row = self.db.fetch_one(
            """
            SELECT last_buy_price FROM trade_buy_locks
            WHERE client_id = ? AND item_id = ?
            ORDER BY last_buy_at DESC
            LIMIT 1
            """,
            (client_id, item_id),
        )
        if not row or not row["last_buy_price"]:
            return 0
        return max(0, int(row["last_buy_price"]))

    @staticmethod
    def _profit_capped_sell_price(buy_price: int, sell_price: int) -> int:
        """按最近买入价限制最高利润，避免单次价格波动被刷爆。"""

        raw_price = max(1, int(sell_price))
        cost = max(0, int(buy_price))
        if cost <= 0:
            return raw_price
        max_sell = int(cost * (1 + TRADE_MAX_PROFIT_RATE))
        return min(raw_price, max(1, max_sell))

    @staticmethod
    def _profit_adjusted_sell_price(buy_price: int, sell_price: int, rate: float) -> int:
        """只对普通跑商利润部分应用柔性衰减。"""

        raw_price = max(1, int(sell_price))
        cost = max(0, int(buy_price))
        if cost <= 0 or raw_price <= cost:
            return raw_price
        profit = raw_price - cost
        safe_rate = max(0.0, min(1.0, float(rate)))
        adjusted = cost + int(profit * safe_rate)
        return max(1, min(raw_price, adjusted))

    @staticmethod
    def _trade_group(item: dict) -> str:
        """读取跑商大类；现有库未回填 trade_group 时按 trade_type 现场归类。"""

        effect = load_json(item.get("effect", "{}"), {})
        trade_group = str(effect.get("trade_group") or "")
        if trade_group:
            return trade_group
        return trade_group_for_type(str(effect.get("trade_type") or ""))

    @staticmethod
    def _group_price_factor(trade_group: str) -> float:
        """按跑商大类调整基础成交价。"""

        if trade_group == "纯经济":
            return TRADE_PURE_ECONOMY_PRICE_FACTOR
        return 1.0

    def _trade_fee_rate(self, client_id: str, base_rate: float) -> float:
        """按聚财类宝石小幅降低跑商手续费。"""

        trade_bonus = min(base_rate * 0.8, self.equipment_bonuses(client_id).get("trade_bonus", 0))
        return max(0.0, base_rate - trade_bonus)

    def _trade_market_state_conn(self, conn, client_id: str) -> dict[str, int]:
        """读取今日普通跑商出售热度和动态收益线。"""

        active_count = self._active_trade_player_count_conn(conn)
        global_soft_line = trade_global_soft_line(active_count)
        player_soft_line = trade_player_soft_line(active_count, global_soft_line)
        day = business_day()
        global_row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS quantity
            FROM trade_records
            JOIN item_defs i ON i.item_id = trade_records.item_id
            WHERE business_day = ? AND action = 'sell'
            """,
            (day,),
        ).fetchone()
        player_row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS quantity
            FROM trade_records
            JOIN item_defs i ON i.item_id = trade_records.item_id
            WHERE business_day = ? AND action = 'sell' AND client_id = ?
            """,
            (day, client_id),
        ).fetchone()
        global_used = int(global_row["quantity"] if global_row else 0)
        player_used = int(player_row["quantity"] if player_row else 0)
        return {
            "active_count": active_count,
            "global_soft_line": global_soft_line,
            "global_used": global_used,
            "player_soft_line": player_soft_line,
            "player_used": player_used,
        }

    @staticmethod
    def _trade_profit_rate_for_quantity(market_state: dict[str, int], quantity: int) -> float:
        """用本次出售数量的中点估算批量交易的边际利润倍率。"""

        middle_quantity = max(0, int(quantity)) // 2
        return trade_profit_rate(
            int(market_state["player_used"]) + middle_quantity,
            int(market_state["global_used"]) + middle_quantity,
            int(market_state["player_soft_line"]),
            int(market_state["global_soft_line"]),
        )

    def _active_trade_player_count_conn(self, conn) -> int:
        """读取近期活跃人数，用来动态调整跑商总池和个人份额。"""

        cutoff = ts(now() - timedelta(days=TRADE_ACTIVE_WINDOW_DAYS))
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM players p
            WHERE p.created_at >= ?
               OR EXISTS (SELECT 1 FROM game_logs g WHERE g.client_id = p.client_id AND g.created_at >= ?)
               OR EXISTS (SELECT 1 FROM trade_records t WHERE t.client_id = p.client_id AND t.created_at >= ?)
               OR EXISTS (SELECT 1 FROM exploration_records e WHERE e.client_id = p.client_id AND e.started_at >= ?)
               OR EXISTS (SELECT 1 FROM wormhole_participants wp WHERE wp.client_id = p.client_id AND wp.updated_at >= ?)
               OR EXISTS (SELECT 1 FROM seasonal_boss_participants sp WHERE sp.client_id = p.client_id AND sp.updated_at >= ?)
               OR EXISTS (SELECT 1 FROM duel_records d WHERE (d.from_client_id = p.client_id OR d.to_client_id = p.client_id) AND d.created_at >= ?)
               OR EXISTS (SELECT 1 FROM combat_logs c WHERE c.client_id = p.client_id AND c.created_at >= ?)
            """,
            (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff),
        ).fetchone()
        return max(1, int(row["count"] if row else 0))

    @staticmethod
    def _add_heat_conn(conn, location_name: str, item_id: str, buy_count: int = 0, sell_count: int = 0) -> None:
        """记录商场买卖热度。"""

        conn.execute(
            """
            INSERT INTO trade_heat (location_name, item_id, business_day, buy_count, sell_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(location_name, item_id, business_day)
            DO UPDATE SET
                buy_count = buy_count + excluded.buy_count,
                sell_count = sell_count + excluded.sell_count
            """,
            (location_name, item_id, business_day(), buy_count, sell_count),
        )

    def _parse_name_quantity(self, message: str) -> tuple[str, int]:
        """从命令参数里解析名称和数量。"""

        parts = split_words(message)
        if len(parts) < 2:
            return message.strip(), 0
        return " ".join(parts[:-1]), to_int(parts[-1])

    def _location(self, name: str) -> dict | None:
        """读取地点。"""

        return self.db.fetch_one("SELECT * FROM trade_locations WHERE name = ?", (name.strip(),))

    def _special_buyer(self, name: str) -> dict | None:
        """读取特殊收购地点。"""

        return self.db.fetch_one("SELECT * FROM special_buyers WHERE buyer_name = ?", (name.strip(),))

    def _navigation_location(self, name: str) -> dict | None:
        """读取任意可命名导航地点。"""

        row = self.db.fetch_one("SELECT name, x, y FROM world_locations WHERE name = ?", (name.strip(),))
        return {"name": row["name"], "x": int(row["x"]), "y": int(row["y"])} if row else None

    def _all_navigation_locations(self) -> list[dict]:
        """读取全部命名世界点位，供坐标导航使用。"""

        return [
            {"name": row["name"], "x": int(row["x"]), "y": int(row["y"])}
            for row in self.db.fetch_all("SELECT name, x, y FROM world_locations")
        ]

    def _known_location_at(self, x: int, y: int) -> dict | None:
        """读取精确坐标上的命名地点。"""

        row = self.db.fetch_one("SELECT name, x, y FROM world_locations WHERE x = ? AND y = ?", (x, y))
        return {"name": row["name"], "x": int(row["x"]), "y": int(row["y"])} if row else None

    def _nearest_location(self, x: int, y: int) -> dict | None:
        """按坐标找最近地点。"""

        rows = self._all_navigation_locations()
        if not rows:
            return None
        return min(rows, key=lambda row: hypot(row["x"] - x, row["y"] - y))

    @staticmethod
    def _parse_coordinates(parts: list[str]) -> tuple[int, int] | None:
        """严格解析坐标，避免文字参数被默认当成 0。"""

        try:
            return int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coordinate_in_world(x: int, y: int) -> bool:
        """判断坐标是否在当前修仙界边界内。"""

        return WORLD_COORD_MIN <= x <= WORLD_COORD_MAX and WORLD_COORD_MIN <= y <= WORLD_COORD_MAX

    def _wilderness_name(self, x: int, y: int) -> str:
        """给任意非命名坐标生成稳定的荒野地点名。"""

        nearest = self._nearest_location(x, y)
        if not nearest:
            return "荒野"
        direction = self._direction_from_anchor(x, y, nearest)
        return f"荒野·{nearest['name']}{direction}"

    @staticmethod
    def _direction_from_anchor(x: int, y: int, anchor: dict) -> str:
        """按坐标相对命名地点生成方位词。"""

        dx = x - int(anchor["x"])
        dy = y - int(anchor["y"])
        horizontal = "东" if dx > 0 else "西" if dx < 0 else ""
        vertical = "北" if dy > 0 else "南" if dy < 0 else ""
        return horizontal + vertical or "附近"

    def _recycle_weapons(
        self,
        client_id: str,
        location: dict,
        weapon_ids: list[int] | None = None,
        all_spares: bool = False,
    ) -> str:
        """商场统一出售入口使用的武器回收实现。"""

        player = self.player(client_id) or {}
        self.ensure_starter_weapon(client_id)
        with self.db.transaction() as conn:
            ids = self._recyclable_weapon_ids_conn(conn, client_id) if all_spares else list(weapon_ids or [])
            if not ids:
                return T.hint("当前没有可出售的备用武器。", "已装备武器和最后一把武器不能出售；发送：武器 查看列表。<武器>")

            weapons = self._weapon_rows_for_recycle_conn(conn, client_id, ids)
            missing_ids = [weapon_id for weapon_id in ids if weapon_id not in weapons]
            if missing_ids:
                return T.hint(f"没有找到武器：{self._format_weapon_ids(missing_ids)}。", "发送：武器 查看自己的武器 ID。<武器>")

            equipped_ids = [weapon_id for weapon_id in ids if int(weapons[weapon_id]["equipped"])]
            if equipped_ids:
                return T.hint(f"已装备武器不能出售：{self._format_weapon_ids(equipped_ids)}。", "先切换到其他武器，再出售备用武器。<武器>")

            count = conn.execute(
                "SELECT COUNT(*) AS total FROM player_weapons WHERE holder_id = ?",
                (client_id,),
            ).fetchone()
            total_count = int(count["total"] if count else 0)
            if total_count - len(ids) < 1:
                return T.hint("不能出售最后一把武器。", "至少保留一把自用武器，避免无法探险战斗。")

            today_income = self._today_weapon_recycle_income_conn(conn, client_id)
            records: list[dict[str, object]] = []
            total_value = 0
            for weapon_id in ids:
                record = self._recycle_weapon_conn(
                    conn,
                    client_id,
                    dict(weapons[weapon_id]),
                    location,
                    int(player.get("level", 1)),
                    today_income,
                )
                records.append(record)
                total_value += int(record["value"])
                today_income += int(record["value"])

            self._grant_source_stones_conn(conn, client_id, total_value)

        return self._format_weapon_recycle_result(records)

    @staticmethod
    def _grant_source_stones_conn(conn, client_id: str, amount: int) -> None:
        """在当前事务里给玩家增加源石。"""

        conn.execute(
            "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
            (int(amount), client_id),
        )

    @staticmethod
    def _write_game_log_conn(conn, client_id: str, action: str, detail: str) -> None:
        """在当前事务里写入游戏日志。"""

        conn.execute(
            "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (client_id, action, detail, ts()),
        )

    @staticmethod
    def _recyclable_weapon_ids_conn(conn, client_id: str) -> list[int]:
        """读取全部备用武器 ID。"""

        rows = conn.execute(
            """
            SELECT weapon_id
            FROM player_weapons
            WHERE holder_id = ? AND equipped = 0
            ORDER BY weapon_id
            """,
            (client_id,),
        ).fetchall()
        return [int(row["weapon_id"]) for row in rows]

    @staticmethod
    def _weapon_rows_for_recycle_conn(conn, client_id: str, weapon_ids: list[int]) -> dict[int, dict]:
        """读取本次出售涉及的武器行。"""

        rows: dict[int, dict] = {}
        for weapon_id in weapon_ids:
            row = conn.execute(
                """
                SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
                FROM player_weapons w
                JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
                WHERE w.holder_id = ? AND w.weapon_id = ?
                """,
                (client_id, weapon_id),
            ).fetchone()
            if row:
                rows[weapon_id] = dict(row)
        return rows

    def _recycle_weapon_conn(
        self,
        conn,
        client_id: str,
        weapon: dict,
        location: dict,
        player_level: int,
        today_income: int,
    ) -> dict[str, object]:
        """在事务中回收一把武器并写入回收流水。"""

        weapon_id = int(weapon["weapon_id"])
        quote = self._weapon_recycle_quote(weapon, float(location["price_factor"]), player_level, today_income)
        value = int(quote["value"])
        conn.execute("DELETE FROM weapon_enchant_names WHERE weapon_id = ?", (weapon_id,))
        conn.execute("DELETE FROM player_weapons WHERE holder_id = ? AND weapon_id = ?", (client_id, weapon_id))
        conn.execute(
            """
            INSERT INTO weapon_recycle_records (
                client_id, weapon_id, weapon_name, quality, level, max_level,
                raw_value, capped_value, price_rate, total_price,
                location_name, business_day, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                weapon_id,
                weapon_label_name(weapon),
                weapon["quality"],
                int(weapon["level"]),
                int(weapon["max_level"]),
                quote["raw_value"],
                quote["capped_value"],
                quote["rate"],
                value,
                location["name"],
                business_day(),
                ts(),
            ),
        )
        self._write_game_log_conn(
            conn,
            client_id,
            "武器回收",
            f"weapon_id={weapon_id}, stones={value}, rate={quote['rate']:.2f}",
        )
        return {
            "weapon_id": weapon_id,
            "name": weapon_label_name(weapon),
            "quality": weapon["quality"],
            "value": value,
            "rate": float(quote["rate"]),
        }

    @staticmethod
    def _weapon_recycle_quote(weapon: dict, price_factor: float, player_level: int, today_income: int) -> dict[str, float | int]:
        """计算武器回收报价。"""

        enchants = len(load_json(weapon.get("enchant_effects"), []))
        raw_value = int(
            (
                computed_weapon_attack(weapon) * 60
                + int(weapon["max_level"]) * 100
                + int(weapon["level"]) * 250
                + enchants * 5000
            )
            * quality_factor(weapon["quality"])
            * price_factor
        )
        single_cap = weapon_recycle_single_cap(player_level)
        capped_value = min(raw_value, single_cap)
        rate = weapon_recycle_price_rate(player_level, today_income + capped_value // 2)
        value = max(1, int(capped_value * rate))
        return {
            "raw_value": raw_value,
            "single_cap": single_cap,
            "capped_value": capped_value,
            "rate": rate,
            "value": value,
        }

    @staticmethod
    def _today_weapon_recycle_income_conn(conn, client_id: str) -> int:
        """在事务里读取玩家今日武器回收收入。"""

        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_price), 0) AS total
            FROM weapon_recycle_records
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, business_day()),
        ).fetchone()
        return int(row["total"]) if row else 0

    @staticmethod
    def _format_weapon_recycle_result(records: list[dict[str, object]]) -> str:
        """格式化单把或批量武器回收结果。"""

        if len(records) == 1:
            record = records[0]
            return TradeService._format_recycle_success(
                f"{weapon_id_label(record['weapon_id'])} {record['name']}[{record['quality']}]",
                int(record["value"]),
                float(record["rate"]),
            )

        total_value = sum(int(record["value"]) for record in records)
        return TradeService._format_batch_recycle_result(
            "武器批量回收",
            f"{len(records)} 把",
            total_value,
            [
                f"{weapon_id_label(record['weapon_id'])} {record['name']}[{record['quality']}]｜"
                f"收入 **{money(int(record['value']))}**｜倍率 {int(float(record['rate']) * 100)}%"
                for record in records
            ],
        )

    @staticmethod
    def _format_recycle_success(label: str, value: int, rate: float) -> str:
        """格式化单个纳戒资产回收结果。"""

        return f"回收成功：{label}，获得源石 {money(value)}，当前倍率 {int(rate * 100)}%。"

    @staticmethod
    def _format_batch_recycle_result(title: str, quantity_text: str, total_value: int, lines: list[str]) -> str:
        """格式化批量回收面板。"""

        panel = T.panel()
        panel.section(title)
        panel.line(f"回收 **{quantity_text}**，获得源石 **{money(total_value)}**。")
        for line in lines:
            panel.line(line)
        return panel.render()

    def _recycle_books(
        self,
        client_id: str,
        location: dict,
        item: dict | None = None,
        quantity: int | None = None,
    ) -> str:
        """回收纳戒里的未附魔技能书。"""

        player = self.player(client_id) or {}
        if item is not None:
            amount = max(1, int(quantity or 1))
            with self.db.transaction() as conn:
                row = conn.execute(
                    """
                    SELECT quantity FROM ring_items
                    WHERE client_id = ? AND ring_item_id = ?
                    """,
                    (client_id, item["ring_item_id"]),
                ).fetchone()
                owned = int(row["quantity"]) if row else 0
                if owned < amount:
                    return T.hint(f"纳戒里 {item['name']} 只有 {owned} 本。", "发送：纳戒 查看库存后再出售。<纳戒>")

                today_income = self._today_book_recycle_income_conn(conn, client_id)
                quote = self._book_recycle_quote(
                    item,
                    amount,
                    float(location["price_factor"]),
                    int(player.get("level", 1)),
                    today_income,
                )
                if not self.remove_ring_conn(conn, client_id, item["ring_item_id"], amount):
                    return T.hint("技能书库存已变化，出售失败。", "发送：纳戒 查看当前库存后再试。<纳戒>")
                self._grant_source_stones_conn(conn, client_id, int(quote["value"]))
                self._insert_book_recycle_record_conn(conn, client_id, item, amount, quote, location)
                self._write_game_log_conn(
                    conn,
                    client_id,
                    "技能书回收",
                    f"book={item['ring_item_id']}, quantity={amount}, stones={quote['value']}",
                )
            return self._format_recycle_success(f"{item['name']} x{amount}", int(quote["value"]), float(quote["rate"]))

        records: list[dict[str, object]] = []
        total_value = 0
        total_quantity = 0
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT r.ring_item_id, r.quantity, e.name, e.quality
                FROM ring_items r
                JOIN ring_item_defs e ON e.ring_item_id = r.ring_item_id
                WHERE r.client_id = ? AND r.quantity > 0 AND e.category = '技能书'
                ORDER BY e.quality, e.name
                """,
                (client_id,),
            ).fetchall()
            if not rows:
                return T.hint(f"{location['name']}可以回收纳戒里的未附魔技能书，但你当前没有技能书。", "继续探险、挑战虫洞或首领获取技能书。")

            today_income = self._today_book_recycle_income_conn(conn, client_id)
            for row in rows:
                amount = int(row["quantity"])
                quote = self._book_recycle_quote(
                    row,
                    amount,
                    float(location["price_factor"]),
                    int(player.get("level", 1)),
                    today_income,
                )
                if not self.remove_ring_conn(conn, client_id, row["ring_item_id"], amount):
                    continue
                self._insert_book_recycle_record_conn(conn, client_id, row, amount, quote, location)
                today_income += int(quote["value"])
                total_value += int(quote["value"])
                total_quantity += amount
                records.append(
                    {
                        "name": row["name"],
                        "quality": row["quality"],
                        "quantity": amount,
                        "value": int(quote["value"]),
                        "rate": float(quote["rate"]),
                    }
                )

            if not records:
                return T.hint("技能书库存已变化，出售失败。", "发送：纳戒 查看当前库存后再试。<纳戒>")
            self._grant_source_stones_conn(conn, client_id, total_value)
            self._write_game_log_conn(conn, client_id, "技能书批量回收", f"quantity={total_quantity}, stones={total_value}")

        return self._format_batch_recycle_result(
            "技能书批量回收",
            f"{total_quantity} 本",
            total_value,
            [
                f"{record['name']}[{record['quality']}] x{record['quantity']}｜"
                f"收入 **{money(record['value'])}**｜倍率 {int(float(record['rate']) * 100)}%"
                for record in records
            ],
        )

    @staticmethod
    def _insert_book_recycle_record_conn(conn, client_id: str, book: dict, quantity: int, quote: dict, location: dict) -> None:
        """写入技能书回收流水。"""

        conn.execute(
            """
            INSERT INTO book_recycle_records (
                client_id, book_id, book_name, quality, quantity,
                raw_value, capped_value, price_rate, total_price,
                location_name, business_day, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                book["ring_item_id"],
                book["name"],
                book["quality"],
                quantity,
                quote["raw_value"],
                quote["capped_value"],
                quote["rate"],
                quote["value"],
                location["name"],
                business_day(),
                ts(),
            ),
        )

    @staticmethod
    def _today_book_recycle_income_conn(conn, client_id: str) -> int:
        """在事务里读取玩家今日技能书回收收入。"""

        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_price), 0) AS total
            FROM book_recycle_records
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, business_day()),
        ).fetchone()
        return int(row["total"]) if row else 0

    @staticmethod
    def _book_recycle_quote(
        book: dict,
        quantity: int,
        price_factor: float,
        player_level: int,
        today_income: int,
    ) -> dict[str, float | int]:
        """计算技能书回收报价。"""

        amount = max(1, int(quantity))
        raw_unit = int(4000 * quality_factor(book["quality"]) * price_factor)
        single_cap = book_recycle_single_cap(player_level)
        capped_unit = min(raw_unit, single_cap)
        raw_value = raw_unit * amount
        capped_value = capped_unit * amount
        rate = book_recycle_price_rate(player_level, today_income + capped_value // 2)
        value = max(1, int(capped_value * rate))
        return {
            "raw_value": raw_value,
            "single_cap": single_cap,
            "capped_value": capped_value,
            "rate": rate,
            "value": value,
        }

    def _recycle_gems(
        self,
        client_id: str,
        location: dict,
        item: dict | None = None,
        gem_level: int | None = None,
        quantity: int | None = None,
    ) -> str:
        """回收纳戒里的未镶嵌宝石。"""

        player = self.player(client_id) or {}
        if item is not None:
            amount = max(1, int(quantity or 1))
            with self.db.transaction() as conn:
                resolved_level, level_error = self.resolve_gem_level_conn(
                    conn,
                    client_id,
                    item["ring_item_id"],
                    item["name"],
                    gem_level,
                    "出售 {name} {level}级 1",
                )
                if level_error:
                    return level_error
                assert resolved_level is not None

                row = conn.execute(
                    """
                    SELECT quantity FROM gem_items
                    WHERE client_id = ? AND gem_id = ? AND level = ?
                    """,
                    (client_id, item["ring_item_id"], resolved_level),
                ).fetchone()
                owned = int(row["quantity"]) if row else 0
                if owned < amount:
                    return T.hint(f"纳戒里 {item['name']} {resolved_level}级 只有 {owned} 个。", "发送：宝石 查看库存后再出售。<宝石>")

                today_income = self._today_gem_recycle_income_conn(conn, client_id)
                quote = self._gem_recycle_quote(
                    item,
                    resolved_level,
                    amount,
                    float(location["price_factor"]),
                    int(player.get("level", 1)),
                    today_income,
                )
                if not self.remove_gem_conn(conn, client_id, item["ring_item_id"], resolved_level, amount):
                    return T.hint("宝石库存已变化，出售失败。", "发送：宝石 查看当前库存后再试。<宝石>")
                self._grant_source_stones_conn(conn, client_id, int(quote["value"]))
                self._insert_gem_recycle_record_conn(
                    conn,
                    client_id,
                    {
                        "gem_id": item["ring_item_id"],
                        "name": item["name"],
                        "quality": item["quality"],
                    },
                    resolved_level,
                    amount,
                    quote,
                    location,
                )
                self._write_game_log_conn(
                    conn,
                    client_id,
                    "宝石回收",
                    f"gem={item['ring_item_id']}, level={resolved_level}, quantity={amount}, stones={quote['value']}",
                )
            return self._format_recycle_success(
                f"{item['name']} {resolved_level}级 x{amount}",
                int(quote["value"]),
                float(quote["rate"]),
            )

        records: list[dict[str, object]] = []
        total_value = 0
        total_quantity = 0
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT g.gem_id, g.level, g.quantity, e.name, e.quality
                FROM gem_items g
                JOIN ring_item_defs e ON e.ring_item_id = g.gem_id
                WHERE g.client_id = ? AND g.quantity > 0 AND e.category = '宝石'
                ORDER BY e.name, g.level
                """,
                (client_id,),
            ).fetchall()
            if not rows:
                return T.hint(f"{location['name']}可以回收纳戒里的未镶嵌宝石，但你当前没有宝石。", "继续探险或挑战首领、虫洞获取宝石。")

            today_income = self._today_gem_recycle_income_conn(conn, client_id)
            for row in rows:
                amount = int(row["quantity"])
                level = int(row["level"])
                quote = self._gem_recycle_quote(
                    row,
                    level,
                    amount,
                    float(location["price_factor"]),
                    int(player.get("level", 1)),
                    today_income,
                )
                if not self.remove_gem_conn(conn, client_id, row["gem_id"], level, amount):
                    continue
                self._insert_gem_recycle_record_conn(conn, client_id, row, level, amount, quote, location)
                today_income += int(quote["value"])
                total_value += int(quote["value"])
                total_quantity += amount
                records.append(
                    {
                        "name": row["name"],
                        "quality": row["quality"],
                        "level": level,
                        "quantity": amount,
                        "value": int(quote["value"]),
                        "rate": float(quote["rate"]),
                    }
                )

            if not records:
                return T.hint("宝石库存已变化，出售失败。", "发送：宝石 查看当前库存后再试。<宝石>")
            self._grant_source_stones_conn(conn, client_id, total_value)
            self._write_game_log_conn(conn, client_id, "宝石批量回收", f"quantity={total_quantity}, stones={total_value}, level=all")

        return self._format_batch_recycle_result(
            "宝石批量回收",
            f"{total_quantity} 颗",
            total_value,
            [
                f"{record['name']} {record['level']}级[{record['quality']}] x{record['quantity']}｜"
                f"收入 **{money(record['value'])}**｜倍率 {int(float(record['rate']) * 100)}%"
                for record in records
            ],
        )

    @staticmethod
    def _insert_gem_recycle_record_conn(
        conn,
        client_id: str,
        gem: dict,
        level: int,
        quantity: int,
        quote: dict,
        location: dict,
    ) -> None:
        """写入宝石回收流水。"""

        conn.execute(
            """
            INSERT INTO gem_recycle_records (
                client_id, gem_id, gem_name, quality, level, quantity,
                raw_value, capped_value, price_rate, total_price,
                location_name, business_day, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                str(gem["gem_id"]),
                gem["name"],
                gem["quality"],
                level,
                quantity,
                quote["raw_value"],
                quote["capped_value"],
                quote["rate"],
                quote["value"],
                location["name"],
                business_day(),
                ts(),
            ),
        )

    @staticmethod
    def _today_gem_recycle_income_conn(conn, client_id: str) -> int:
        """在事务里读取玩家今日宝石回收收入。"""

        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_price), 0) AS total
            FROM gem_recycle_records
            WHERE client_id = ? AND business_day = ?
            """,
            (client_id, business_day()),
        ).fetchone()
        return int(row["total"]) if row else 0

    @staticmethod
    def _gem_recycle_quote(
        gem: dict,
        level: int,
        quantity: int,
        price_factor: float,
        player_level: int,
        today_income: int,
    ) -> dict[str, float | int]:
        """计算宝石回收报价。"""

        level = max(1, int(level))
        amount = max(1, int(quantity))
        raw_unit = int((level * level * 2200 + level * 600) * quality_factor(gem["quality"]) * price_factor)
        single_cap = int(gem_recycle_single_cap(player_level) * (1 + (level - 1) * 0.25))
        capped_unit = min(raw_unit, single_cap)
        raw_value = raw_unit * amount
        capped_value = capped_unit * amount
        rate = gem_recycle_price_rate(player_level, today_income + capped_value // 2)
        value = max(1, int(capped_value * rate))
        return {
            "raw_value": raw_value,
            "single_cap": single_cap,
            "capped_value": capped_value,
            "rate": rate,
            "value": value,
        }

    @staticmethod
    def _format_weapon_ids(weapon_ids: list[int]) -> str:
        """把多个武器 ID 排成玩家可读文本。"""

        return "、".join(weapon_id_label(weapon_id) for weapon_id in weapon_ids)

    def _special_sell_rate_text(self, client_id: str, level: int) -> str:
        """读取今日特殊收购价格倍率展示。"""

        with self.db.transaction() as conn:
            used = self._special_sell_used_conn(conn, client_id)
        rate = special_sell_price_rate(level, used)
        soft_line = special_sell_soft_line(level)
        return f"{int(rate * 100)}%，今日已收入 {money(used)}，参考线 {money(soft_line)}"

    @staticmethod
    def _special_sell_used_conn(conn, client_id: str) -> int:
        """统计今日特殊收购已变现金额。"""

        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_price), 0) AS total
            FROM trade_records
            WHERE client_id = ?
              AND action IN ('special_sell', 'special_auto_sell')
              AND business_day = ?
            """,
            (client_id, business_day()),
        ).fetchone()
        return int(row["total"] if row else 0)

    def _location_goods(self, location_name: str) -> list[dict]:
        """读取地点可购买商品。"""

        return self.db.fetch_all(
            """
            SELECT i.*
            FROM trade_goods g
            JOIN item_defs i ON i.item_id = g.item_id
            WHERE g.home_location = ?
              AND i.tradeable = 1
            ORDER BY i.base_price, i.name
            """,
            (location_name,),
        )

    def _can_buy_one(self, client_id: str, player: dict, item: dict, buy_fee_rate: float) -> bool:
        """推荐前确认当前玩家至少能买 1 个该商品。"""

        if not item.get("tradeable"):
            return False
        buy_price, _sell_price = self.price(player["location_name"], item["item_id"])
        fee = int(buy_price * buy_fee_rate)
        if int(player["source_stones"]) < buy_price + fee:
            return False
        with self.db.transaction() as conn:
            ok, _reason = self.can_add_backpack_conn(conn, client_id, item["item_id"], 1)
        return ok

    def _special_buyers_ordered(self) -> list[dict]:
        """按初始化顺序读取特殊收购地点。"""

        return self.db.fetch_all("SELECT rowid, * FROM special_buyers ORDER BY rowid")

    def _special_auto_sell_plan(self, client_id: str, buyers: list[dict]) -> list[tuple[dict, list[dict]]]:
        """按收购地点整理可自动出售的背包物品。"""

        backpack = {row["item_id"]: row for row in self.backpack_rows(client_id) if int(row["quantity"]) > 0}
        plan: list[tuple[dict, list[dict]]] = []
        for buyer in buyers:
            items = []
            for item_id in str(buyer["item_ids"]).split(","):
                item = backpack.get(item_id)
                if item:
                    items.append(item)
            if items:
                plan.append((buyer, items))
        return plan

    @staticmethod
    def _action_text(action: str) -> str:
        """把交易动作转成玩家能看懂的文字。"""

        return {
            "buy": "购买",
            "sell": "出售",
            "medicine_carry": "商路顺药",
            "special_sell": "战利品出售",
            "special_auto_sell": "战利品自动出售",
            "misc_sell": "杂物甩卖",
        }.get(action, action)


service = TradeService(db)

__all__ = ["TradeService", "service"]
