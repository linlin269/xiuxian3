"""修仙数据库备份包。

这个包没有 HTTP 路由，只负责在服务关闭时备份修仙数据库。
ROUTER_GROUPS 会导入中文子包，因此这里可以直接注册 OnEvent 回调。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from launch import C, OnEvent, logger

from ..sql import XiuxianDB, db


MAX_BACKUPS = 10
MIN_BACKUP_INTERVAL = timedelta(minutes=30)
HOT_RELOAD_WINDOW = timedelta(minutes=3)
STARTED_AT = datetime.now()


def backup_database(
    database: XiuxianDB,
    backup_dir: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """把当前 sqlite 库备份到本包目录，并只保留最近 10 份自动备份。"""

    db_path = Path(database.db_path)
    backup_dir = Path(backup_dir) if backup_dir is not None else Path(__file__).resolve().parent
    now = now or datetime.now()

    timestamp = now.strftime("%Y%m%d_%H%M")
    backup_path = _available_backup_path(backup_dir, db_path.stem, timestamp, db_path.suffix)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    with database.lock:
        database.init()
        assert database.conn is not None
        database.conn.commit()
        _assert_backup_worth_saving(database.conn)

        # sqlite3 的上下文管理器只提交事务，不会自动关闭连接。
        # Windows 下备份文件被占用时尤其明显，所以这里显式 close。
        backup_conn = sqlite3.connect(backup_path)
        try:
            database.conn.backup(backup_conn)
            backup_conn.commit()
        finally:
            backup_conn.close()

    _cleanup_old_backups(backup_dir, db_path.stem, db_path.suffix)
    return backup_path


@OnEvent.disconnect(priority=100)
async def backup_db() -> None:
    """服务关闭时备份修仙数据库。"""

    try:
        if _should_skip_disconnect_backup():
            logger.opt(colors=True).info(C.warn("跳过 修仙数据库 热重启备份"))
            return
        backup_path = backup_database(db)
        logger.opt(colors=True).info(
            C.join(
                C.warn("执行 修仙数据库 备份"),
                C.kv("path", backup_path),
            )
        )
    except Exception as exc:
        logger.opt(colors=True).error(
            C.join(
                C.fail("执行 修仙数据库 备份失败"),
                C.kv("error", exc),
            )
        )


def _assert_backup_worth_saving(conn: sqlite3.Connection) -> None:
    """空玩家库不进入自动备份，避免异常空库挤掉可恢复历史。"""

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='players'"
    ).fetchone()
    if not row:
        raise RuntimeError("players 表不存在，跳过数据库备份")
    player_count = int(conn.execute("SELECT COUNT(*) FROM players").fetchone()[0])
    if player_count <= 0:
        raise RuntimeError("players 表为空，跳过数据库备份")


def _should_skip_disconnect_backup(
    now: datetime | None = None,
    backup_dir: Path | None = None,
) -> bool:
    """热重启和短间隔关闭不自动备份。"""

    now = now or datetime.now()
    backup_dir = Path(backup_dir) if backup_dir is not None else Path(__file__).resolve().parent
    if now - STARTED_AT <= HOT_RELOAD_WINDOW:
        return True
    latest = _latest_auto_backup(backup_dir, Path(db.db_path).stem, Path(db.db_path).suffix)
    if latest and now - datetime.fromtimestamp(latest.stat().st_mtime) <= MIN_BACKUP_INTERVAL:
        return True
    return False


def _latest_auto_backup(backup_dir: Path, stem: str, suffix: str) -> Path | None:
    """找到最近一份自动备份；手工放入的恢复文件不参与限流。"""

    backups = _auto_backups(backup_dir, stem, suffix)
    return backups[0] if backups else None


def _available_backup_path(backup_dir: Path, stem: str, timestamp: str, suffix: str) -> Path:
    """生成不覆盖现有文件的分钟级备份路径。"""

    backup_path = backup_dir / f"{stem}_{timestamp}{suffix}"
    if not backup_path.exists():
        return backup_path
    for index in range(2, 100):
        candidate = backup_dir / f"{stem}_{timestamp}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"同一分钟备份文件过多，无法生成备份路径：{timestamp}")


def _cleanup_old_backups(backup_dir: Path, stem: str, suffix: str) -> None:
    """删除过旧自动备份；手工恢复文件不计入 10 个上限。"""

    for path in _auto_backups(backup_dir, stem, suffix)[MAX_BACKUPS:]:
        path.unlink(missing_ok=True)


def _auto_backups(backup_dir: Path, stem: str, suffix: str) -> list[Path]:
    """列出本组件生成的自动备份。"""

    auto_backup_re = re.compile(
        rf"^{re.escape(stem)}_\d{{8}}_\d{{4}}(?:_\d+)?{re.escape(suffix)}$"
    )
    return sorted(
        (
            path
            for path in backup_dir.glob(f"{stem}_*{suffix}")
            if auto_backup_re.match(path.name)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
