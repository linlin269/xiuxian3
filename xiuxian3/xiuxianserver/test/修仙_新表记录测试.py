"""修仙长期记录表测试。

运行方式：

    python test/修仙_新表记录测试.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from 修仙.common import ts
from 修仙.constants import SCHEMA_VERSION
from 修仙.rules import weapon_exp_for_level, weapon_exp_progress
from 修仙.sql import XiuxianDB
from 修仙.玩家.service import PlayerService
from 修仙.武器.service import WeaponService
from 修仙.源库.service import SourceVaultService


def main() -> None:
    """验证新表不只是建表，也会沉淀真实数据。"""

    with TemporaryDirectory() as temp_dir:
        _assert_unknown_schema_does_not_drop(Path(temp_dir) / "xiuxian_unknown_schema_test.db")

        db = XiuxianDB(Path(temp_dir) / "xiuxian_records_test.db")
        player = PlayerService(db)
        weapon = WeaponService(db)
        vault = SourceVaultService(db)

        assert "创建成功" in player.create("record_player", "青衫客")
        assert "签到成功" in player.sign("record_player")
        assert "新手礼包领取成功" in player.newbie_gift("record_player")
        assert "已存入源石" in vault.deposit("record_player", 1000)
        weapon.ensure_starter_weapon("record_player")
        weapon_id = db.fetch_one("SELECT weapon_id FROM player_weapons WHERE holder_id = ?", ("record_player",))
        assert weapon_id is not None
        weapon_id_int = int(weapon_id["weapon_id"])
        assert "升级成功" in weapon.upgrade("record_player", str(weapon_id_int))
        with db.transaction() as conn:
            weapon.record_weapon_combat_conn(
                conn,
                "record_player",
                weapon_id_int,
                monster_kill=True,
                damage=88,
                weapon_exp=321,
            )
        weapon_row = db.fetch_one("SELECT level, exp FROM player_weapons WHERE weapon_id = ?", (weapon_id_int,))
        assert weapon_row is not None
        assert int(weapon_row["level"]) == 2
        assert int(weapon_row["exp"]) == weapon_exp_for_level(1) + 321
        current_exp, next_exp = weapon_exp_progress(
            int(weapon_row["exp"]),
            int(weapon_row["level"]),
            40,
        )
        assert f"经验:{current_exp}/{next_exp}" in weapon.list_weapons("record_player")

        with db.transaction() as conn:
            conn.execute(
                "UPDATE players SET level = 9, exp = 12345, source_stones = source_stones + 500000 WHERE client_id = ?",
                ("record_player",),
            )
            conn.execute(
                "UPDATE source_vaults SET balance = 120000 WHERE client_id = ?",
                ("record_player",),
            )
            for index in range(5):
                conn.execute(
                    """
                    INSERT INTO exploration_records
                    (client_id, location_name, status, started_at, ready_at, finished_at, result, claimed)
                    VALUES (?, ?, '已领取', ?, ?, ?, '{}', 1)
                    """,
                    ("record_player", "青岚坊", ts(), ts(), ts()),
                )
            for index in range(20):
                conn.execute(
                    """
                    INSERT INTO trade_records
                    (client_id, action, item_id, quantity, total_price, fee, location_name, business_day, created_at)
                    VALUES (?, 'sell', ?, 1, 10000, 100, '天枢城', '2099-01-01', ?)
                    """,
                    ("record_player", f"test_trade_{index}", ts()),
                )

        profile_text = player.profile("record_player")
        assert "经验" in profile_text
        assert "源石" in profile_text
        assert "武器" in profile_text
        assert "修仙日记" in player.diary("record_player")

        journal_count = db.fetch_one(
            "SELECT COUNT(*) AS count FROM player_journals WHERE client_id = ?",
            ("record_player",),
        )
        assert int(journal_count["count"]) >= 8
        level_journal = db.fetch_one(
            """
            SELECT text FROM player_journals
            WHERE client_id = ? AND milestone_key = 'level'
            """,
            ("record_player",),
        )
        assert level_journal and "累计经验 12345" in level_journal["text"]

        title_count = db.fetch_one(
            "SELECT COUNT(*) AS count FROM player_titles WHERE client_id = ?",
            ("record_player",),
        )
        active_title = db.fetch_one(
            "SELECT title FROM player_titles WHERE client_id = ? AND active = 1",
            ("record_player",),
        )
        assert int(title_count["count"]) >= 5
        assert active_title is not None

        log_actions = {
            row["action"]
            for row in db.fetch_all(
                "SELECT action FROM game_logs WHERE client_id = ?",
                ("record_player",),
            )
        }
        assert {"创建用户", "签到", "新手礼包", "存入源石", "升级武器"}.issubset(log_actions)
        db.close()

    print("修仙长期记录表测试通过")


def _assert_unknown_schema_does_not_drop(db_path: Path) -> None:
    """旧版本只能中止，不能把玩家表清空。"""

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_meta (key, value) VALUES ('version', '1999010101');
        CREATE TABLE players (
            client_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL
        );
        INSERT INTO players (client_id, display_name) VALUES ('kept_player', '不能删');
        """
    )
    conn.commit()
    conn.close()

    db = XiuxianDB(db_path)
    try:
        try:
            db.init()
        except RuntimeError as exc:
            assert "版本不匹配" in str(exc)
        else:
            raise AssertionError("旧 schema 不应初始化成功")
    finally:
        db.close()

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM players WHERE client_id = 'kept_player'").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


if __name__ == "__main__":
    main()
