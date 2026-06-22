"""异火模块冒烟测试。

运行方式：

    python test/修仙_异火冒烟测试.py

测试使用临时 SQLite，不写入真实 xiuxian.db。
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from 修仙.sql import XiuxianDB, FLAME_DEFS
from 修仙.common import CoreService, ts
from 修仙.异火.service import FlameService, EXPLORE_FLAME_RANKS, BOSS_WORMHOLE_FLAME_RANKS
from 修仙.二手市场.service import SecondHandService
from 修仙.玩家.service import PlayerService


def main() -> None:
    """异火模块基础验证。"""

    with TemporaryDirectory() as temp_dir:
        db = XiuxianDB(Path(temp_dir) / "xiuxian_flame_test.db")
        try:
            _check_flame_defs_seeded(db)
            _check_tables_exist(db)
            _check_flame_list(db)
            _check_flame_equip_unequip(db)
            _check_flame_fusion_missing(db)
            _check_flame_fusion_success(db)
            _check_flame_duplicate_grant(db)
            _check_di_yan_locks_rank2_23(db)
            _check_flame_second_hand(db)
            _check_flame_multiplier(db)
        finally:
            db.close()

    print("异火模块冒烟测试通过")


def _check_flame_defs_seeded(db: XiuxianDB) -> None:
    """验证 23 种异火定义已落库。"""

    rows = db.fetch_all("SELECT * FROM flame_defs ORDER BY rank")
    assert len(rows) == 23, f"异火定义数量不对：{len(rows)}"
    assert rows[0]["name"] == "帝炎"
    assert rows[0]["rank"] == 1
    assert float(rows[0]["attack_multiplier"]) == 2.0
    assert rows[22]["name"] == "玄黄炎"
    assert rows[22]["rank"] == 23
    assert float(rows[22]["attack_multiplier"]) == 1.1


def _check_tables_exist(db: XiuxianDB) -> None:
    """验证三张异火表已创建。"""

    for table in ("flame_defs", "player_flames", "flame_fusion_records"):
        row = db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        assert row is not None, f"表 {table} 不存在"


def _check_flame_list(db: XiuxianDB) -> None:
    """验证异火列表和详情。"""

    flame = FlameService(db)
    player = PlayerService(db)
    player.create_player("flame_user", "异火测")
    text = flame.list_all("flame_user")
    assert "帝炎" in text
    assert "玄黄炎" in text
    assert "x2.000" in text

    detail = flame.detail("flame_user", "玄黄炎")
    assert "玄黄炎" in detail
    assert "x1.100" in detail
    assert "探险结算" in detail

    my = flame.my_flames("flame_user")
    assert "尚未拥有" in my


def _check_flame_equip_unequip(db: XiuxianDB) -> None:
    """验证装备和卸下。"""

    flame = FlameService(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, ?, 0, '测试', ?, ?)",
            ("equip_user", "xuanhuang_yan", ts(), ts()),
        )
    PlayerService(db).create_player("equip_user", "装备测")

    equip_text = flame.equip("equip_user", "玄黄炎")
    assert "已装备" in equip_text
    assert flame.equipped_multiplier("equip_user") == 1.1

    unequip_text = flame.unequip("equip_user")
    assert "已卸下" in unequip_text
    assert flame.equipped_multiplier("equip_user") == 1.0


def _check_flame_fusion_missing(db: XiuxianDB) -> None:
    """验证合成失败：缺少异火。"""

    flame = FlameService(db)
    PlayerService(db).create_player("fuse_user", "合成测")
    result = flame.fuse("fuse_user")
    assert "帝炎合成失败" in result
    assert "缺少异火" in result


def _check_flame_fusion_success(db: XiuxianDB) -> None:
    """验证合成成功。"""

    flame = FlameService(db)
    PlayerService(db).create_player("fuse_ok_user", "合成成功测")
    with db.transaction() as conn:
        for flame_def in FLAME_DEFS:
            if flame_def[1] >= 2:
                conn.execute(
                    "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, ?, 0, '测试', ?, ?)",
                    ("fuse_ok_user", flame_def[0], ts(), ts()),
                )
    result = flame.fuse("fuse_ok_user")
    assert "帝炎合成成功" in result
    assert flame.equipped_multiplier("fuse_ok_user") == 2.0
    rows = db.fetch_all("SELECT * FROM player_flames WHERE client_id = 'fuse_ok_user'")
    assert len(rows) == 1
    assert rows[0]["flame_id"] == "di_yan"


def _check_flame_duplicate_grant(db: XiuxianDB) -> None:
    """验证重复掉落转补偿。"""

    flame = FlameService(db)
    PlayerService(db).create_player("dup_user", "重复测")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, ?, 0, '测试', ?, ?)",
            ("dup_user", "xuanhuang_yan", ts(), ts()),
        )
    with db.transaction() as conn:
        result = flame.try_grant_flame(conn, "dup_user", "explore", {23})
    # 有概率获得其他异火（21或22），也可能重复得到23号
    if not result["granted"]:
        assert result["compensation"] is not None


def _check_di_yan_locks_rank2_23(db: XiuxianDB) -> None:
    """验证帝炎合成后 rank 2~23 不再发放。"""

    flame = FlameService(db)
    PlayerService(db).create_player("lock_user", "锁测")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, 'di_yan', 1, '合成', ?, ?)",
            ("lock_user", ts(), ts()),
        )
    with db.transaction() as conn:
        result = flame.try_grant_flame(conn, "lock_user", "boss_wormhole", BOSS_WORMHOLE_FLAME_RANKS)
    assert not result["granted"]
    assert result["compensation"] is not None
    assert result["compensation"]["reason"] == "已有帝炎"


def _check_flame_second_hand(db: XiuxianDB) -> None:
    """验证二手市场异火上架和下架。"""

    market = SecondHandService(db)
    player = PlayerService(db)
    player.create_player("seller_flame", "卖火测")
    player.create_player("buyer_flame", "买火测")
    player.add_stones("buyer_flame", 100_000)

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, ?, 0, '测试', ?, ?)",
            ("seller_flame", "xuanhuang_yan", ts(), ts()),
        )

    # 先上架玄黄炎
    sell_result = market.sell("seller_flame", "玄黄炎 1 50000")
    assert "上架成功" in sell_result

    # 已装备异火不能上架（先下架玄黄炎，然后测试装备中的异火）
    market.cancel("seller_flame")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, ?, 1, '测试', ?, ?)",
            ("seller_flame", "yinyang_shuangyan", ts(), ts()),
        )
    sell_equipped = market.sell("seller_flame", "阴阳双炎 1 30000")
    assert "正在装备中" in sell_equipped
    # 卸下后重新上架玄黄炎
    with db.transaction() as conn:
        conn.execute("UPDATE player_flames SET equipped = 0 WHERE client_id = 'seller_flame' AND flame_id = 'yinyang_shuangyan'")

    sell_result2 = market.sell("seller_flame", "玄黄炎 1 50000")
    assert "上架成功" in sell_result2
    buy_result = market.buy("buyer_flame", "二手市场购买 卖火测")
    assert "购买成功" in buy_result
    buyer_has = db.fetch_one("SELECT 1 FROM player_flames WHERE client_id = 'buyer_flame' AND flame_id = 'xuanhuang_yan'")
    assert buyer_has is not None

    # 买家已有帝炎时不能再买
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, 'di_yan', 0, '合成', ?, ?)",
            ("seller_flame", ts(), ts()),
        )
    market.sell("seller_flame", "帝炎 1 99999")
    player.create_player("di_yan_buyer", "帝炎买家测")
    player.add_stones("di_yan_buyer", 100_000)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, 'di_yan', 0, '合成', ?, ?)",
            ("di_yan_buyer", ts(), ts()),
        )
    buy_fail = market.buy("di_yan_buyer", "二手市场购买 卖火测")
    assert "已拥有帝炎" in buy_fail


def _check_flame_multiplier(db: XiuxianDB) -> None:
    """验证攻击倍率计算。"""

    flame = FlameService(db)
    core = CoreService(db)
    PlayerService(db).create_player("mult_user", "倍率测")

    # 默认 1.0
    assert flame.equipped_multiplier("mult_user") == 1.0
    assert core.equipped_flame_multiplier("mult_user") == 1.0

    # 装备玄黄炎后 1.1
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_flames (client_id, flame_id, equipped, source, obtained_at, updated_at) VALUES (?, 'xuanhuang_yan', 1, '测试', ?, ?)",
            ("mult_user", ts(), ts()),
        )
    assert flame.equipped_multiplier("mult_user") == 1.1
    assert core.equipped_flame_name("mult_user") == "玄黄炎"

    # final_attack 基于 base_attack + weapon_attack * flame_multiplier
    final = core.final_attack("mult_user")
    assert final >= 1


if __name__ == "__main__":
    main()
