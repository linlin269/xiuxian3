"""修仙数据库备份组件测试。

运行方式：

    python test/修仙_数据库备份测试.py
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

backup_mod = importlib.import_module("修仙.数据库备份")
XiuxianDB = importlib.import_module("修仙.sql").XiuxianDB


def main() -> None:
    """验证备份命名、热重启跳过和清理范围。"""

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        db = XiuxianDB(temp_path / "xiuxian.db")
        _prepare_db(db)

        backup_path = backup_mod.backup_database(db, backup_dir=temp_path)
        assert backup_path.name.startswith("xiuxian_")
        assert backup_path.name.endswith(".db")
        assert len(backup_path.stem.split("_")[-1]) == 4
        second_backup_path = backup_mod.backup_database(db, backup_dir=temp_path)
        assert second_backup_path != backup_path
        assert backup_path.exists()
        assert second_backup_path.exists()
        assert second_backup_path.stem.startswith(f"{backup_path.stem}_")
        _assert_backup_contains_player(backup_path)
        _assert_backup_contains_player(second_backup_path)

        _assert_empty_db_skipped(temp_path)
        _assert_hot_reload_skip(temp_path)
        _assert_cleanup_keeps_manual_files(temp_path)
        db.close()

    print("修仙数据库备份测试通过")


def _prepare_db(db: XiuxianDB) -> None:
    """创建一条玩家数据，让备份具备保存价值。"""

    db.init()
    db.execute(
        """
        INSERT INTO players (client_id, display_name, created_at)
        VALUES ('backup_player', '备份玩家', '2099-01-01 00:00:00')
        """
    )


def _assert_empty_db_skipped(temp_path: Path) -> None:
    """空玩家库不应进入自动备份。"""

    empty_path = temp_path / "empty.db"
    db = XiuxianDB(empty_path)
    try:
        db.init()
        try:
            backup_mod.backup_database(db, backup_dir=temp_path)
        except RuntimeError as exc:
            assert "players 表为空" in str(exc)
        else:
            raise AssertionError("空玩家库不应被备份")
    finally:
        db.close()


def _assert_backup_contains_player(backup_path: Path) -> None:
    """备份文件必须是可打开的 sqlite，并保留玩家数据。"""

    db = XiuxianDB(backup_path)
    try:
        row = db.fetch_one(
            "SELECT display_name FROM players WHERE client_id = ?",
            ("backup_player",),
        )
        assert row and row["display_name"] == "备份玩家"
    finally:
        db.close()


def _assert_hot_reload_skip(temp_path: Path) -> None:
    """启动太短或离上次备份太近时跳过断连备份。"""

    now = datetime(2099, 1, 1, 12, 0, 0)
    old_started_at = backup_mod.STARTED_AT
    try:
        backup_mod.STARTED_AT = now - timedelta(seconds=30)
        assert backup_mod._should_skip_disconnect_backup(now, backup_dir=temp_path)

        backup_mod.STARTED_AT = now - timedelta(hours=1)
        recent = temp_path / "xiuxian_20990101_1150.db"
        recent.write_text("manual", encoding="utf-8")
        timestamp = (now - timedelta(minutes=5)).timestamp()
        os.utime(recent, (timestamp, timestamp))
        assert backup_mod._should_skip_disconnect_backup(now, backup_dir=temp_path)
    finally:
        backup_mod.STARTED_AT = old_started_at


def _assert_cleanup_keeps_manual_files(temp_path: Path) -> None:
    """自动备份清理不能误删手工恢复文件。"""

    manual = temp_path / "xiuxian_manual_restore_20990101_0000.db"
    manual.write_text("keep", encoding="utf-8")
    for index in range(backup_mod.MAX_BACKUPS + 3):
        path = temp_path / f"xiuxian_20990101_00{index:02d}.db"
        path.write_text(str(index), encoding="utf-8")
    backup_mod._cleanup_old_backups(temp_path, "xiuxian", ".db")
    auto_count = len(backup_mod._auto_backups(temp_path, "xiuxian", ".db"))
    assert auto_count == backup_mod.MAX_BACKUPS
    assert manual.exists()


if __name__ == "__main__":
    main()
