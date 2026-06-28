"""后台接口核心逻辑。"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from ..common import CoreService, dt, now, row_value, ts, validate_name
from ..constants import MAX_LEVEL
from ..rules import level_from_exp, money, player_exp_for_level
from ..sql import db

BACKEND_SERVER_DIR = Path(__file__).resolve().parents[2]
XIUXIAN_DIR = Path(__file__).resolve().parent.parent
BOOTSTRAP_TOKEN_FILE = BACKEND_SERVER_DIR / "后台初始化一次性密钥.txt"
BOOTSTRAP_META_KEY = "bootstrap_token"
BOOTSTRAP_LOCK_KEY = "bootstrap_locked"
BOOTSTRAP_ADMIN_ID_KEY = "bootstrap_admin_id"
BOOTSTRAP_CREATED_AT_KEY = "bootstrap_created_at"
ADMIN_SESSION_COOKIE = "xiuxian_admin_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
LOGIN_LOCK_THRESHOLD = 5
LOGIN_LOCK_MINUTES = 30
OPERATION_TTL_SECONDS = 30 * 60
VALID_ACTIONS = {"grant_stones", "grant_item", "grant_exp"}
VALID_ITEM_SCOPES = {"backpack", "ring"}


class AdminOperationError(RuntimeError):
    """后台操作校验失败。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AdminLogDB:
    """后台审计日志库，独立于修仙主库。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None
        self.lock = RLock()

    def init(self) -> None:
        """初始化日志库。"""

        with self.lock:
            if self.conn is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA foreign_keys = ON")

            assert self.conn is not None
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_operation_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id INTEGER NOT NULL DEFAULT 0,
                    request_id TEXT NOT NULL,
                    preview_id INTEGER NOT NULL DEFAULT 0,
                    admin_id INTEGER NOT NULL,
                    admin_username TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_client_id TEXT NOT NULL DEFAULT '',
                    target_name_snapshot TEXT NOT NULL DEFAULT '',
                    item_scope TEXT NOT NULL DEFAULT '',
                    item_id_snapshot TEXT NOT NULL DEFAULT '',
                    item_name_snapshot TEXT NOT NULL DEFAULT '',
                    stones_amount INTEGER NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    operator_ip TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            self.conn.commit()

    def close(self) -> None:
        """关闭日志库。"""

        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """查询多条日志。"""

        with self.lock:
            self.init()
            assert self.conn is not None
            rows = self.conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def record_operation(
        self,
        *,
        operation_id: int,
        request_id: str,
        admin_id: int,
        admin_username: str,
        stage: str,
        action_type: str,
        target_client_id: str = "",
        target_name_snapshot: str = "",
        item_scope: str = "",
        item_id_snapshot: str = "",
        item_name_snapshot: str = "",
        stones_amount: int = 0,
        quantity: int = 0,
        status: str,
        error_message: str = "",
        detail: dict[str, Any] | None = None,
        operator_ip: str = "",
        user_agent: str = "",
        created_at: str | None = None,
    ) -> int:
        """写入一条审计日志。"""

        with self.lock:
            self.init()
            assert self.conn is not None
            cursor = self.conn.execute(
                """
                INSERT INTO admin_operation_logs (
                    operation_id, request_id, preview_id, admin_id, admin_username,
                    stage, action_type, target_client_id, target_name_snapshot,
                    item_scope, item_id_snapshot, item_name_snapshot,
                    stones_amount, quantity, status, error_message,
                    detail_json, operator_ip, user_agent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(operation_id),
                    request_id,
                    int(operation_id),
                    int(admin_id),
                    admin_username,
                    stage,
                    action_type,
                    target_client_id,
                    target_name_snapshot,
                    item_scope,
                    item_id_snapshot,
                    item_name_snapshot,
                    int(stones_amount),
                    int(quantity),
                    status,
                    error_message,
                    _json_text(detail or {}),
                    operator_ip,
                    user_agent,
                    created_at or ts(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def recent_logs(self, limit: int = 20, date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
        """读取最新日志，可选按日期范围筛选。"""

        safe_limit = max(1, min(int(limit), 200))
        if date_from or date_to:
            conditions: list[str] = []
            params: list[Any] = []
            if date_from:
                conditions.append("created_at >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("created_at <= ?")
                params.append(date_to + "T23:59:59")
            where = " AND ".join(conditions)
            params.append(safe_limit)
            return self.fetch_all(
                f"SELECT * FROM admin_operation_logs WHERE {where} ORDER BY log_id DESC LIMIT ?",
                tuple(params),
            )
        return self.fetch_all(
            "SELECT * FROM admin_operation_logs ORDER BY log_id DESC LIMIT ?",
            (safe_limit,),
        )


class AdminBackendService:
    """后台账号、会话、预览、执行与日志能力。"""

    def __init__(self) -> None:
        self.core = CoreService(db)
        self.log_db = AdminLogDB(XIUXIAN_DIR / "xiuxiangmrizhi.db")
        self._lock = RLock()

    def startup(self) -> None:
        """服务启动时准备后台所需数据。"""

        with self._lock:
            db.init()
            self.log_db.init()
            self.ensure_bootstrap_token()

    def shutdown(self) -> None:
        """服务关闭时释放日志库连接。"""

        self.log_db.close()

    def refresh_hall_of_heroes_npcs(self) -> str:
        """手动刷新英灵殿 NPC。"""

        from ..英灵殿.service import service as hall_of_heroes_service

        return hall_of_heroes_service.refresh_npcs()

    def ensure_bootstrap_token(self) -> str:
        """确保一次性初始化密钥在数据库和文本文件里同步存在。"""

        with self._lock:
            db.init()
            self.log_db.init()
            token = self._meta_value(BOOTSTRAP_META_KEY)
            file_token = self._read_bootstrap_token_file()

            if not self._is_valid_bootstrap_token(token):
                token = file_token if self._is_valid_bootstrap_token(file_token) else secrets.token_hex(8)
                self._meta_set(BOOTSTRAP_META_KEY, token)
                self._meta_set(BOOTSTRAP_CREATED_AT_KEY, ts())
            elif file_token != token:
                self._write_bootstrap_token_file(token)

            if not self._is_valid_bootstrap_token(file_token) or file_token != token:
                self._write_bootstrap_token_file(token)

            return token

    def bootstrap_status(self) -> dict[str, Any]:
        """读取后台初始化状态。"""

        token = self.ensure_bootstrap_token()
        admin_count = self._scalar(
            "SELECT COUNT(*) AS total FROM admin_users",
            default=0,
        )
        bootstrapped = bool(self._meta_value(BOOTSTRAP_LOCK_KEY)) or int(admin_count) > 0
        if bootstrapped and not self._meta_value(BOOTSTRAP_LOCK_KEY):
            self._meta_set(BOOTSTRAP_LOCK_KEY, "1")
        return {
            "ok": True,
            "bootstrapped": bootstrapped,
            "admin_count": int(admin_count),
            "token_ready": bool(token),
            "token_length": len(token),
            "token_file": BOOTSTRAP_TOKEN_FILE.name,
            "bootstrap_locked": bool(self._meta_value(BOOTSTRAP_LOCK_KEY)),
        }

    def bootstrap_admin(
        self,
        username: str,
        password: str,
        token: str,
        *,
        operator_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        """创建首位管理员。"""

        self.ensure_bootstrap_token()
        clean_username = self._validate_admin_username(username)
        clean_password = self._validate_admin_password(password)
        clean_token = _text(token)
        expected_token = self._meta_value(BOOTSTRAP_META_KEY)
        if not clean_token or not secrets.compare_digest(clean_token, expected_token):
            raise AdminOperationError("后台初始化一次性密钥不正确，请从 txt 文件里重新复制。")

        if self.bootstrap_status()["bootstrapped"]:
            raise AdminOperationError("后台已经初始化过管理员账号，不能重复初始化。")

        salt, password_hash = _hash_password(clean_password)
        created_at = ts()
        with db.transaction() as conn:
            existing = conn.execute("SELECT admin_id FROM admin_users LIMIT 1").fetchone()
            if existing:
                raise AdminOperationError("后台已经存在管理员账号，不能重复初始化。")

            cursor = conn.execute(
                """
                INSERT INTO admin_users (
                    username, password_hash, password_salt, role,
                    is_active, failed_login_count, locked_until,
                    last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'super_admin', 1, 0, NULL, NULL, ?, ?)
                """,
                (clean_username, password_hash, salt, created_at, created_at),
            )
            admin_id = int(cursor.lastrowid)
            self._meta_set_conn(conn, BOOTSTRAP_LOCK_KEY, "1")
            self._meta_set_conn(conn, BOOTSTRAP_ADMIN_ID_KEY, str(admin_id))
            self._meta_set_conn(conn, BOOTSTRAP_CREATED_AT_KEY, created_at)

        return {
            "ok": True,
            "message": "管理员初始化成功，请使用新账号登录。",
            "admin": self._public_admin(
                {
                    "admin_id": admin_id,
                    "username": clean_username,
                    "role": "super_admin",
                    "is_active": 1,
                    "failed_login_count": 0,
                    "locked_until": None,
                    "last_login_at": None,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            ),
        }

    def login_admin(
        self,
        username: str,
        password: str,
        *,
        operator_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        """管理员登录并创建会话。"""

        clean_username = _text(username)
        clean_password = str(password or "")
        if not clean_username:
            raise AdminOperationError("请输入管理员账号。")
        if not clean_password:
            raise AdminOperationError("请输入管理员密码。")

        row = db.fetch_one("SELECT * FROM admin_users WHERE username = ? LIMIT 1", (clean_username,))
        if not row:
            raise AdminOperationError("管理员账号或密码不正确。")
        if not int(row_value(row, "is_active", 0)):
            raise AdminOperationError("管理员账号已被停用。")

        locked_until = dt(row_value(row, "locked_until", ""))
        if locked_until and locked_until > now():
            minutes = max(1, int((locked_until - now()).total_seconds() // 60) + 1)
            raise AdminOperationError(f"管理员账号已锁定，请 {minutes} 分钟后再试。")

        if not _verify_password(clean_password, row_value(row, "password_salt", ""), row_value(row, "password_hash", "")):
            with db.transaction() as conn:
                self._record_login_failure_conn(conn, int(row_value(row, "admin_id", 0)))
            raise AdminOperationError("管理员账号或密码不正确。")

        session_token = secrets.token_urlsafe(32)
        session_hash = _hash_session_token(session_token)
        expires_at = ts(now() + timedelta(seconds=SESSION_TTL_SECONDS))
        current_at = ts()
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE admin_users
                SET failed_login_count = 0,
                    locked_until = NULL,
                    last_login_at = ?,
                    updated_at = ?
                WHERE admin_id = ?
                """,
                (current_at, current_at, int(row_value(row, "admin_id", 0))),
            )
            conn.execute(
                """
                INSERT INTO admin_sessions (
                    admin_id, session_token_hash, expires_at, last_seen_at,
                    revoked_at, ip, user_agent, created_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    int(row_value(row, "admin_id", 0)),
                    session_hash,
                    expires_at,
                    current_at,
                    operator_ip,
                    user_agent,
                    current_at,
                ),
            )

        admin = self._public_admin(row)
        admin["last_login_at"] = current_at
        return {
            "ok": True,
            "message": "登录成功。",
            "admin": admin,
            "session_token": session_token,
            "expires_at": expires_at,
        }

    def current_admin(self, session_token: str) -> dict[str, Any] | None:
        """根据会话令牌读取当前管理员。"""

        token = _text(session_token)
        if not token:
            return None

        session_hash = _hash_session_token(token)
        row = db.fetch_one(
            """
            SELECT s.session_id, s.expires_at, s.last_seen_at, s.revoked_at, s.created_at AS session_created_at,
                   u.admin_id, u.username, u.password_hash, u.password_salt, u.role, u.is_active,
                   u.failed_login_count, u.locked_until, u.last_login_at, u.created_at, u.updated_at
            FROM admin_sessions s
            JOIN admin_users u ON u.admin_id = s.admin_id
            WHERE s.session_token_hash = ?
            LIMIT 1
            """,
            (session_hash,),
        )
        if not row:
            return None
        if int(row_value(row, "is_active", 0)) <= 0:
            return None

        expires_at = dt(row_value(row, "expires_at", ""))
        if row_value(row, "revoked_at", "") or (expires_at and expires_at <= now()):
            if row_value(row, "revoked_at", "") == "":
                db.execute("UPDATE admin_sessions SET revoked_at = ? WHERE session_id = ?", (ts(), int(row_value(row, "session_id", 0))))
            return None

        db.execute(
            "UPDATE admin_sessions SET last_seen_at = ? WHERE session_id = ?",
            (ts(), int(row_value(row, "session_id", 0))),
        )
        admin = self._public_admin(row)
        admin.update(
            {
                "session_id": int(row_value(row, "session_id", 0)),
                "session_expires_at": row_value(row, "expires_at", ""),
                "session_last_seen_at": row_value(row, "last_seen_at", ""),
            }
        )
        return admin

    def logout(self, session_token: str) -> bool:
        """注销管理员会话。"""

        token = _text(session_token)
        if not token:
            return False
        session_hash = _hash_session_token(token)
        row = db.fetch_one("SELECT session_id FROM admin_sessions WHERE session_token_hash = ? LIMIT 1", (session_hash,))
        if not row:
            return False
        db.execute(
            "UPDATE admin_sessions SET revoked_at = ? WHERE session_id = ?",
            (ts(), int(row_value(row, "session_id", 0))),
        )
        return True

    def search_players(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """按玩家名或 client_id 搜索。"""

        value = _text(query)
        safe_limit = max(1, min(int(limit), 50))
        if not value:
            rows = db.fetch_all(
                """
                SELECT client_id, display_name, level, source_stones, status, location_name, x, y
                FROM players
                ORDER BY level DESC, display_name ASC
                LIMIT ?
                """,
                (safe_limit,),
            )
            return [self._player_search_row(row, 4) for row in rows]

        like = _like_term(value)
        rows = db.fetch_all(
            """
            SELECT client_id, display_name, level, source_stones, status, location_name, x, y,
                   CASE
                       WHEN client_id = ? THEN 0
                       WHEN display_name = ? THEN 1
                       WHEN display_name LIKE ? ESCAPE '\\' THEN 2
                       WHEN client_id LIKE ? ESCAPE '\\' THEN 3
                       ELSE 4
                   END AS match_rank
            FROM players
            WHERE client_id = ?
               OR display_name = ?
               OR display_name LIKE ? ESCAPE '\\'
               OR client_id LIKE ? ESCAPE '\\'
            ORDER BY match_rank, level DESC, display_name ASC
            LIMIT ?
            """,
            (value, value, f"{like}%", f"%{like}%", value, value, f"{like}%", f"%{like}%", safe_limit),
        )
        return [self._player_search_row(row, int(row_value(row, "match_rank", 4))) for row in rows]

    def get_player_detail(self, client_id: str) -> dict[str, Any]:
        """读取玩家详情。"""

        value = _text(client_id)
        if not value:
            raise AdminOperationError("请先选择玩家。")
        player = db.fetch_one("SELECT * FROM players WHERE client_id = ?", (value,))
        if not player:
            raise AdminOperationError(f"没有找到玩家：{value}。")
        vault = db.fetch_one("SELECT * FROM source_vaults WHERE client_id = ?", (value,)) or {}
        backpack = self.core.backpack_rows(value)
        ring = self.core.ring_rows(value)
        logs = db.fetch_all(
            "SELECT log_id, action, detail, created_at FROM game_logs WHERE client_id = ? ORDER BY log_id DESC LIMIT 10",
            (value,),
        )
        player_public = dict(player)
        player_public["next_level_text"] = self.core.next_level_text(player_public)
        player_public["display_name"] = str(player_public.get("display_name") or "")
        player_public["source_stones"] = int(player_public.get("source_stones") or 0)

        # 坐骑信息
        mount_info: dict[str, Any] = {}
        mount_row = db.fetch_one("SELECT * FROM player_mounts WHERE client_id = ?", (value,))
        if mount_row:
            mount_def = db.fetch_one("SELECT * FROM mount_defs WHERE mount_id = ?", (mount_row["mount_id"],))
            if mount_def:
                mt = mount_def["mount_type"]
                if mt == "extreme":
                    tier_text = "极显化"
                elif mt == "manifest":
                    tier_text = "显化"
                else:
                    tier_text = f"{mount_def['tier']}阶"
                direction = mount_def.get("manifest_direction") or ""
                mount_info = {
                    "mount_id": mount_row["mount_id"],
                    "name": mount_def["name"],
                    "tier_text": tier_text,
                    "direction": direction,
                    "stars": mount_row["stars"],
                    "max_stars": mount_def["max_stars"],
                    "mount_type": mt,
                    "lore": mount_def.get("lore") or "",
                    "blessing_value": mount_row["blessing_value"],
                    "blessing_expires_at": mount_row["blessing_expires_at"] or "",
                }

        return {
            "player": player_public,
            "source_vault": dict(vault) if vault else {},
            "backpack": backpack,
            "ring": ring,
            "mount": mount_info,
            "recent_logs": logs,
            "summary": {
                "backpack_count": len(backpack),
                "ring_count": len(ring),
                "backpack_weight": self.core.backpack_weight(value),
            },
        }

    def search_items(self, query: str, scope: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """按名称搜索背包和纳戒物品。"""

        value = _text(query)
        clean_scope = _text(scope).lower()
        if clean_scope not in VALID_ITEM_SCOPES:
            clean_scope = ""
        safe_limit = max(1, min(int(limit), 50))
        results: list[dict[str, Any]] = []
        if clean_scope in {"", "backpack"}:
            results.extend(self._search_backpack_items(value, safe_limit))
        if clean_scope in {"", "ring"}:
            results.extend(self._search_ring_items(value, safe_limit))
        results.sort(key=lambda row: (int(row.get("match_rank", 4)), str(row.get("scope") or ""), str(row.get("name") or "")))
        return results[:safe_limit]

    def resolve_player(self, ref: str) -> dict[str, Any]:
        """把玩家引用解析成唯一玩家。"""

        value = _text(ref)
        if not value:
            raise AdminOperationError("请先输入玩家名或玩家 ID。")
        exact = self.core.player_by_ref(value)
        if exact:
            return dict(exact)
        candidates = self.search_players(value, limit=10)
        if not candidates:
            raise AdminOperationError(f"没有找到玩家：{value}。")
        if len(candidates) > 1:
            names = "、".join(str(row.get("display_name") or row.get("client_id") or "") for row in candidates[:5])
            raise AdminOperationError(f"玩家「{value}」匹配到多个结果，请从搜索结果里选择：{names}。")
        return candidates[0]

    def resolve_item(self, ref: str, scope: str = "") -> dict[str, Any]:
        """把物品引用解析成唯一物品。"""

        value = _text(ref)
        clean_scope = _text(scope).lower()
        if not value:
            raise AdminOperationError("请先输入物品名或物品 ID。")
        if clean_scope not in VALID_ITEM_SCOPES:
            clean_scope = ""
        candidates = self.search_items(value, clean_scope, limit=10)
        if not candidates:
            label = "背包物品" if clean_scope == "backpack" else "纳戒物品" if clean_scope == "ring" else "物品"
            raise AdminOperationError(f"没有找到{label}：{value}。")
        if len(candidates) > 1:
            first_rank = int(candidates[0].get("match_rank", 4))
            same_rank = [row for row in candidates if int(row.get("match_rank", 4)) == first_rank]
            if len(same_rank) > 1:
                names = "、".join(str(row.get("name") or row.get("item_id") or "") for row in same_rank[:5])
                raise AdminOperationError(f"物品「{value}」匹配到多个结果，请从搜索结果里选择并明确来源：{names}。")
        return candidates[0]

    def preview_operation(
        self,
        admin: dict[str, Any],
        payload: dict[str, Any],
        *,
        operator_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        """生成发放预览。"""

        request_id = _text(payload.get("request_id")) or secrets.token_hex(16)
        action_type = _text(payload.get("action_type")).lower()
        target_ref = _text(payload.get("target_client_id")) or _text(payload.get("target_query"))
        item_ref = _text(payload.get("item_id")) or _text(payload.get("item_query"))
        item_scope = _text(payload.get("item_scope")).lower()
        stones_amount = max(0, self._payload_int(payload, "stones_amount"))
        quantity = max(0, self._payload_int(payload, "quantity"))
        exp_amount = max(0, self._payload_int(payload, "exp_amount"))
        if action_type == "grant_exp":
            quantity = exp_amount
        reason = _text(payload.get("reason")) or "后台发放"
        note = _text(payload.get("note"))
        created_at = ts()
        expires_at = ts(now() + timedelta(seconds=OPERATION_TTL_SECONDS))

        try:
            preview = self._build_preview(
                action_type=action_type,
                request_id=request_id,
                target_ref=target_ref,
                item_ref=item_ref,
                item_scope=item_scope,
                stones_amount=stones_amount,
                quantity=quantity,
                exp_amount=exp_amount,
                reason=reason,
                note=note,
            )
            with db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO admin_operations (
                        request_id, admin_id, action_type, target_client_id,
                        target_name_snapshot, item_scope, item_id_snapshot,
                        item_name_snapshot, stones_amount, quantity, reason, note,
                        before_json, after_json, warnings_json, rollback_json,
                        status, error_message, operator_ip, user_agent,
                        created_at, confirmed_at, executed_at, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        request_id,
                        int(row_value(admin, "admin_id", 0)),
                        action_type,
                        preview["target_client_id"],
                        preview["target_name_snapshot"],
                        preview["item_scope"],
                        preview["item_id_snapshot"],
                        preview["item_name_snapshot"],
                        int(stones_amount),
                        int(quantity),
                        reason,
                        note,
                        _json_text(preview["before"]),
                        _json_text(preview["after"]),
                        _json_text(preview.get("warnings", [])),
                        _json_text(preview.get("rollback", {})),
                        operator_ip,
                        user_agent,
                        created_at,
                        expires_at,
                        created_at,
                    ),
                )
                operation_id = int(cursor.lastrowid)
            return {
                "ok": True,
                "message": "预览已生成。",
                "operation": self._operation_row_to_public({
                    "operation_id": operation_id,
                    "request_id": request_id,
                    "admin_id": int(row_value(admin, "admin_id", 0)),
                    "admin_username": row_value(admin, "username", ""),
                    "action_type": action_type,
                    "target_client_id": preview["target_client_id"],
                    "target_name_snapshot": preview["target_name_snapshot"],
                    "item_scope": preview["item_scope"],
                    "item_id_snapshot": preview["item_id_snapshot"],
                    "item_name_snapshot": preview["item_name_snapshot"],
                    "stones_amount": int(stones_amount),
                    "quantity": int(quantity),
                    "reason": reason,
                    "note": note,
                    "before_json": _json_text(preview["before"]),
                    "after_json": _json_text(preview["after"]),
                    "warnings_json": _json_text(preview.get("warnings", [])),
                    "rollback_json": _json_text(preview.get("rollback", {})),
                    "status": "pending",
                    "error_message": "",
                    "operator_ip": operator_ip,
                    "user_agent": user_agent,
                    "created_at": created_at,
                    "confirmed_at": None,
                    "executed_at": None,
                    "expires_at": expires_at,
                    "updated_at": created_at,
                }),
            }
        except AdminOperationError as exc:
            failed_operation_id = self._store_failed_operation(
                admin=admin,
                request_id=request_id,
                action_type=action_type,
                target_ref=target_ref,
                item_ref=item_ref,
                item_scope=item_scope,
                stones_amount=stones_amount,
                quantity=quantity,
                reason=reason,
                note=note,
                error_message=exc.message,
                operator_ip=operator_ip,
                user_agent=user_agent,
                created_at=created_at,
                expires_at=expires_at,
            )
            self._write_audit_log(
                operation_id=failed_operation_id,
                request_id=request_id,
                admin=admin,
                stage="preview",
                action_type=action_type,
                target_client_id=target_ref,
                target_name_snapshot="",
                item_scope=item_scope,
                item_id_snapshot=item_ref,
                item_name_snapshot="",
                stones_amount=stones_amount,
                quantity=quantity,
                status="failed",
                error_message=exc.message,
                detail={
                    "reason": reason,
                    "note": note,
                    "target_ref": target_ref,
                    "item_ref": item_ref,
                    "action_type": action_type,
                },
                operator_ip=operator_ip,
                user_agent=user_agent,
                created_at=created_at,
            )
            return {
                "ok": False,
                "message": exc.message,
                "request_id": request_id,
                "operation_id": failed_operation_id,
            }

    def confirm_operation(
        self,
        admin: dict[str, Any],
        payload: dict[str, Any],
        *,
        operator_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        """确认执行发放。"""

        operation_id = self._payload_int(payload, "preview_id", "operation_id")
        request_id = _text(payload.get("request_id"))
        if operation_id <= 0:
            raise AdminOperationError("请先选择预览记录。")
        if not request_id:
            raise AdminOperationError("请先填写请求编号。")

        op_row = db.fetch_one(
            """
            SELECT o.*, u.username AS admin_username
            FROM admin_operations o
            LEFT JOIN admin_users u ON u.admin_id = o.admin_id
            WHERE o.operation_id = ?
            LIMIT 1
            """,
            (operation_id,),
        )
        if not op_row:
            raise AdminOperationError("没有找到对应的预览记录。")
        if row_value(op_row, "request_id", "") != request_id:
            raise AdminOperationError("请求编号与预览记录不一致。")
        if int(row_value(op_row, "admin_id", 0)) != int(row_value(admin, "admin_id", 0)):
            raise AdminOperationError("这条预览不是当前管理员创建的，不能确认。")
        if row_value(op_row, "status", "") != "pending":
            raise AdminOperationError(f"这条预览当前状态是 {row_value(op_row, 'status', '')}，不能重复确认。")

        expires_at = dt(row_value(op_row, "expires_at", ""))
        if expires_at and expires_at <= now():
            self._mark_operation_status(operation_id, "expired", "预览已过期，请重新生成。", operator_ip=operator_ip, user_agent=user_agent)
            self._write_audit_log(
                operation_id=operation_id,
                request_id=request_id,
                admin=admin,
                stage="confirm",
                action_type=row_value(op_row, "action_type", ""),
                target_client_id=row_value(op_row, "target_client_id", ""),
                target_name_snapshot=row_value(op_row, "target_name_snapshot", ""),
                item_scope=row_value(op_row, "item_scope", ""),
                item_id_snapshot=row_value(op_row, "item_id_snapshot", ""),
                item_name_snapshot=row_value(op_row, "item_name_snapshot", ""),
                stones_amount=int(row_value(op_row, "stones_amount", 0)),
                quantity=int(row_value(op_row, "quantity", 0)),
                status="failed",
                error_message="预览已过期，请重新生成。",
                detail={"operation_id": operation_id, "status": "expired"},
                operator_ip=operator_ip,
                user_agent=user_agent,
            )
            return {
                "ok": False,
                "message": "预览已过期，请重新生成。",
                "operation_id": operation_id,
            }

        try:
            with db.transaction() as conn:
                fresh = conn.execute(
                    "SELECT * FROM admin_operations WHERE operation_id = ? LIMIT 1",
                    (operation_id,),
                ).fetchone()
                if not fresh:
                    raise AdminOperationError("没有找到对应的预览记录。")
                if row_value(fresh, "request_id", "") != request_id:
                    raise AdminOperationError("请求编号与预览记录不一致。")
                if row_value(fresh, "status", "") != "pending":
                    raise AdminOperationError(f"这条预览当前状态是 {row_value(fresh, 'status', '')}，不能重复确认。")

                current_at = ts()
                conn.execute(
                    "UPDATE admin_operations SET status = 'running', confirmed_at = ?, updated_at = ? WHERE operation_id = ?",
                    (current_at, current_at, operation_id),
                )
                result = self._execute_operation_conn(conn, fresh)
                conn.execute(
                    """
                    UPDATE admin_operations
                    SET status = 'success',
                        after_json = ?,
                        error_message = '',
                        executed_at = ?,
                        updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (
                        _json_text(result["after"]),
                        current_at,
                        current_at,
                        operation_id,
                    ),
                )
            self._write_audit_log(
                operation_id=operation_id,
                request_id=request_id,
                admin=admin,
                stage="confirm",
                action_type=row_value(op_row, "action_type", ""),
                target_client_id=row_value(op_row, "target_client_id", ""),
                target_name_snapshot=row_value(op_row, "target_name_snapshot", ""),
                item_scope=row_value(op_row, "item_scope", ""),
                item_id_snapshot=row_value(op_row, "item_id_snapshot", ""),
                item_name_snapshot=row_value(op_row, "item_name_snapshot", ""),
                stones_amount=int(row_value(op_row, "stones_amount", 0)),
                quantity=int(row_value(op_row, "quantity", 0)),
                status="success",
                detail={
                    "before": result["before"],
                    "after": result["after"],
                    "reason": row_value(op_row, "reason", ""),
                    "note": row_value(op_row, "note", ""),
                },
                operator_ip=operator_ip,
                user_agent=user_agent,
                created_at=ts(),
            )
            return {
                "ok": True,
                "message": "发放成功。",
                "operation_id": operation_id,
                "operation": self.get_operation_detail(operation_id),
            }
        except AdminOperationError as exc:
            self._mark_operation_status(operation_id, "failed", exc.message, operator_ip=operator_ip, user_agent=user_agent)
            self._write_audit_log(
                operation_id=operation_id,
                request_id=request_id,
                admin=admin,
                stage="confirm",
                action_type=row_value(op_row, "action_type", ""),
                target_client_id=row_value(op_row, "target_client_id", ""),
                target_name_snapshot=row_value(op_row, "target_name_snapshot", ""),
                item_scope=row_value(op_row, "item_scope", ""),
                item_id_snapshot=row_value(op_row, "item_id_snapshot", ""),
                item_name_snapshot=row_value(op_row, "item_name_snapshot", ""),
                stones_amount=int(row_value(op_row, "stones_amount", 0)),
                quantity=int(row_value(op_row, "quantity", 0)),
                status="failed",
                error_message=exc.message,
                detail={
                    "operation_id": operation_id,
                    "reason": row_value(op_row, "reason", ""),
                    "note": row_value(op_row, "note", ""),
                },
                operator_ip=operator_ip,
                user_agent=user_agent,
                created_at=ts(),
            )
            return {
                "ok": False,
                "message": exc.message,
                "operation_id": operation_id,
            }

    def list_operations(self, limit: int = 20, date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
        """读取后台操作记录，可选按日期范围筛选。"""

        safe_limit = max(1, min(int(limit), 100))
        if date_from or date_to:
            conditions: list[str] = []
            params: list[Any] = []
            if date_from:
                conditions.append("o.created_at >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("o.created_at <= ?")
                params.append(date_to + "T23:59:59")
            where = " AND ".join(conditions)
            params.append(safe_limit)
            rows = db.fetch_all(
                f"""
                SELECT o.*, u.username AS admin_username
                FROM admin_operations o
                LEFT JOIN admin_users u ON u.admin_id = o.admin_id
                WHERE {where}
                ORDER BY o.operation_id DESC
                LIMIT ?
                """,
                tuple(params),
            )
        else:
            rows = db.fetch_all(
                """
                SELECT o.*, u.username AS admin_username
                FROM admin_operations o
                LEFT JOIN admin_users u ON u.admin_id = o.admin_id
                ORDER BY o.operation_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
        return [self._operation_row_to_public(row) for row in rows]

    def get_operation_detail(self, operation_id: int) -> dict[str, Any]:
        """读取单条后台操作详情。"""

        row = db.fetch_one(
            """
            SELECT o.*, u.username AS admin_username
            FROM admin_operations o
            LEFT JOIN admin_users u ON u.admin_id = o.admin_id
            WHERE o.operation_id = ?
            LIMIT 1
            """,
            (int(operation_id),),
        )
        if not row:
            raise AdminOperationError("没有找到这条后台操作记录。")
        return self._operation_row_to_public(row)

    def _build_preview(
        self,
        *,
        action_type: str,
        request_id: str,
        target_ref: str,
        item_ref: str,
        item_scope: str,
        stones_amount: int,
        quantity: int,
        exp_amount: int = 0,
        reason: str,
        note: str,
    ) -> dict[str, Any]:
        """在预览阶段生成快照。"""

        if action_type not in VALID_ACTIONS:
            raise AdminOperationError("操作类型不正确。")

        player = self.resolve_player(target_ref)
        player_id = str(player.get("client_id") or "")
        player_name = str(player.get("display_name") or "")
        if not player_id:
            raise AdminOperationError("没有找到目标玩家。")

        if action_type == "grant_stones":
            if stones_amount <= 0:
                raise AdminOperationError("发放源石数量必须大于 0。")
            before_stones = int(player.get("source_stones") or 0)
            after_stones = before_stones + stones_amount
            return {
                "target_client_id": player_id,
                "target_name_snapshot": player_name,
                "item_scope": "",
                "item_id_snapshot": "",
                "item_name_snapshot": "",
                "before": {
                    "source_stones": before_stones,
                    "display_name": player_name,
                    "reason": reason,
                    "note": note,
                },
                "after": {
                    "source_stones": after_stones,
                    "display_name": player_name,
                    "reason": reason,
                    "note": note,
                },
                "warnings": [],
                "rollback": {
                    "action_type": action_type,
                    "target_client_id": player_id,
                    "stones_amount": stones_amount,
                },
            }

        if action_type == "grant_exp":
            if exp_amount <= 0:
                raise AdminOperationError("发放经验数量必须大于 0。")
            before_level = int(player.get("level") or 1)
            before_exp = int(player.get("exp") or 0)
            cap_exp = player_exp_for_level(MAX_LEVEL)
            after_exp = min(cap_exp, before_exp + exp_amount)
            after_level = level_from_exp(after_exp)
            warnings: list[str] = []
            if after_level > before_level:
                warnings.append(f"玩家将从 {before_level} 级升到 {after_level} 级。")
            if after_exp >= cap_exp:
                warnings.append(f"经验已达到满级封顶值 {cap_exp}。")
            return {
                "target_client_id": player_id,
                "target_name_snapshot": player_name,
                "item_scope": "",
                "item_id_snapshot": "",
                "item_name_snapshot": "",
                "before": {
                    "level": before_level,
                    "exp": before_exp,
                    "display_name": player_name,
                    "reason": reason,
                    "note": note,
                },
                "after": {
                    "level": after_level,
                    "exp": after_exp,
                    "display_name": player_name,
                    "reason": reason,
                    "note": note,
                },
                "warnings": warnings,
                "rollback": {
                    "action_type": action_type,
                    "target_client_id": player_id,
                    "exp_amount": exp_amount,
                },
            }

        if quantity <= 0:
            raise AdminOperationError("发放物品数量必须大于 0。")
        item = self.resolve_item(item_ref, item_scope)
        item_id = str(item.get("item_id") or "")
        item_name = str(item.get("name") or "")
        item_scope = str(item.get("scope") or "")
        if not item_id:
            raise AdminOperationError("没有找到可发放的物品。")

        with db.transaction() as conn:
            player_row = conn.execute("SELECT * FROM players WHERE client_id = ?", (player_id,)).fetchone()
            if not player_row:
                raise AdminOperationError("目标玩家不存在，请重新搜索。")

            if item_scope == "backpack":
                ok, reason_text = self.core.can_add_backpack_conn(conn, player_id, item_id, quantity)
                if not ok:
                    raise AdminOperationError(reason_text or "背包空间不足。")
                before_quantity = self._backpack_quantity_conn(conn, player_id, item_id)
                after_quantity = before_quantity + quantity
                return {
                    "target_client_id": player_id,
                    "target_name_snapshot": player_name,
                    "item_scope": item_scope,
                    "item_id_snapshot": item_id,
                    "item_name_snapshot": item_name,
                    "before": {
                        "inventory_type": "backpack",
                        "quantity": before_quantity,
                        "display_name": player_name,
                        "item_name": item_name,
                        "reason": reason,
                        "note": note,
                    },
                    "after": {
                        "inventory_type": "backpack",
                        "quantity": after_quantity,
                        "display_name": player_name,
                        "item_name": item_name,
                        "reason": reason,
                        "note": note,
                    },
                    "warnings": [],
                    "rollback": {
                        "action_type": action_type,
                        "target_client_id": player_id,
                        "item_scope": item_scope,
                        "item_id": item_id,
                        "quantity": quantity,
                    },
                }

            before_quantity = self._ring_or_gem_quantity_conn(conn, player_id, item_id, str(item.get("category") or ""))
            after_quantity = before_quantity + quantity
            return {
                "target_client_id": player_id,
                "target_name_snapshot": player_name,
                "item_scope": item_scope,
                "item_id_snapshot": item_id,
                "item_name_snapshot": item_name,
                "before": {
                    "inventory_type": "gem" if str(item.get("category") or "") == "宝石" else "ring",
                    "quantity": before_quantity,
                    "display_name": player_name,
                    "item_name": item_name,
                    "reason": reason,
                    "note": note,
                },
                "after": {
                    "inventory_type": "gem" if str(item.get("category") or "") == "宝石" else "ring",
                    "quantity": after_quantity,
                    "display_name": player_name,
                    "item_name": item_name,
                    "reason": reason,
                    "note": note,
                },
                "warnings": [],
                "rollback": {
                    "action_type": action_type,
                    "target_client_id": player_id,
                    "item_scope": item_scope,
                    "item_id": item_id,
                    "quantity": quantity,
                },
            }

    def _execute_operation_conn(self, conn: sqlite3.Connection, op_row: dict[str, Any]) -> dict[str, Any]:
        """在事务里执行真正的发放。"""

        action_type = str(row_value(op_row, "action_type", ""))
        target_client_id = str(row_value(op_row, "target_client_id", ""))
        item_scope = str(row_value(op_row, "item_scope", ""))
        item_id = str(row_value(op_row, "item_id_snapshot", ""))
        stones_amount = int(row_value(op_row, "stones_amount", 0))
        quantity = int(row_value(op_row, "quantity", 0))

        if action_type == "grant_stones":
            player = conn.execute("SELECT * FROM players WHERE client_id = ?", (target_client_id,)).fetchone()
            if not player:
                raise AdminOperationError("目标玩家不存在，请重新搜索。")
            before_stones = int(row_value(player, "source_stones", 0))
            conn.execute(
                "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                (stones_amount, target_client_id),
            )
            after_row = conn.execute("SELECT source_stones FROM players WHERE client_id = ?", (target_client_id,)).fetchone()
            after_stones = int(row_value(after_row, "source_stones", before_stones + stones_amount))
            return {
                "before": {"source_stones": before_stones},
                "after": {"source_stones": after_stones},
            }

        if action_type == "grant_exp":
            player = conn.execute("SELECT * FROM players WHERE client_id = ?", (target_client_id,)).fetchone()
            if not player:
                raise AdminOperationError("目标玩家不存在，请重新搜索。")
            before_level = int(row_value(player, "level", 1))
            before_exp = int(row_value(player, "exp", 0))
            old_level, new_level = self.core.add_exp_conn(conn, target_client_id, quantity)
            after_row = conn.execute("SELECT level, exp FROM players WHERE client_id = ?", (target_client_id,)).fetchone()
            after_level = int(row_value(after_row, "level", new_level))
            after_exp = int(row_value(after_row, "exp", before_exp + quantity))
            return {
                "before": {"level": before_level, "exp": before_exp},
                "after": {"level": after_level, "exp": after_exp},
            }

        if action_type != "grant_item":
            raise AdminOperationError("操作类型不正确。")

        player = conn.execute("SELECT * FROM players WHERE client_id = ?", (target_client_id,)).fetchone()
        if not player:
            raise AdminOperationError("目标玩家不存在，请重新搜索。")
        item = self._resolve_item_by_id_conn(conn, item_id, item_scope)
        if not item:
            raise AdminOperationError("物品配置不存在，请重新搜索。")

        if item_scope == "backpack":
            ok, reason_text = self.core.can_add_backpack_conn(conn, target_client_id, item_id, quantity)
            if not ok:
                raise AdminOperationError(reason_text or "背包空间不足。")
            before_quantity = self._backpack_quantity_conn(conn, target_client_id, item_id)
            self.core.add_backpack_conn(conn, target_client_id, item_id, quantity)
            after_quantity = self._backpack_quantity_conn(conn, target_client_id, item_id)
            return {
                "before": {"quantity": before_quantity},
                "after": {"quantity": after_quantity},
            }

        before_quantity = self._ring_or_gem_quantity_conn(conn, target_client_id, item_id, str(row_value(item, "category", "")))
        self.core.add_ring_conn(conn, target_client_id, item_id, quantity)
        after_quantity = self._ring_or_gem_quantity_conn(conn, target_client_id, item_id, str(row_value(item, "category", "")))
        return {
            "before": {"quantity": before_quantity},
            "after": {"quantity": after_quantity},
        }

    def _resolve_item_by_id_conn(self, conn: sqlite3.Connection, item_id: str, scope: str) -> dict[str, Any] | None:
        """按物品 ID 读取定义。"""

        clean_scope = _text(scope).lower()
        if clean_scope == "backpack":
            row = conn.execute("SELECT * FROM item_defs WHERE item_id = ?", (item_id,)).fetchone()
            if not row:
                return None
            return {
                "scope": "backpack",
                "item_id": row["item_id"],
                "name": row["name"],
                "category": row["category"],
                "quality": row["quality"],
                "usable": row["usable"],
                "stack_limit": row["stack_limit"],
                "target_type": "",
            }
        if clean_scope == "ring":
            row = conn.execute("SELECT * FROM ring_item_defs WHERE ring_item_id = ?", (item_id,)).fetchone()
            if not row:
                return None
            return {
                "scope": "ring",
                "item_id": row["ring_item_id"],
                "name": row["name"],
                "category": row["category"],
                "quality": row["quality"],
                "usable": row["usable"],
                "stack_limit": None,
                "target_type": row["target_type"],
            }
        row = conn.execute("SELECT * FROM item_defs WHERE item_id = ?", (item_id,)).fetchone()
        if row:
            return {
                "scope": "backpack",
                "item_id": row["item_id"],
                "name": row["name"],
                "category": row["category"],
                "quality": row["quality"],
                "usable": row["usable"],
                "stack_limit": row["stack_limit"],
                "target_type": "",
            }
        row = conn.execute("SELECT * FROM ring_item_defs WHERE ring_item_id = ?", (item_id,)).fetchone()
        if row:
            return {
                "scope": "ring",
                "item_id": row["ring_item_id"],
                "name": row["name"],
                "category": row["category"],
                "quality": row["quality"],
                "usable": row["usable"],
                "stack_limit": None,
                "target_type": row["target_type"],
            }
        return None

    def _search_backpack_items(self, query: str, limit: int) -> list[dict[str, Any]]:
        """搜索背包物品。"""

        if not query:
            rows = db.fetch_all(
                """
                SELECT 'backpack' AS scope, item_id, name, category, quality, usable, stack_limit,
                       '' AS target_type, 4 AS match_rank
                FROM item_defs
                ORDER BY name ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in rows]

        like = _like_term(query)
        rows = db.fetch_all(
            """
            SELECT 'backpack' AS scope, item_id, name, category, quality, usable, stack_limit,
                   '' AS target_type,
                   CASE
                       WHEN item_id = ? THEN 0
                       WHEN name = ? THEN 1
                       WHEN name LIKE ? ESCAPE '\\' THEN 2
                       WHEN item_id LIKE ? ESCAPE '\\' THEN 3
                       ELSE 4
                   END AS match_rank
            FROM item_defs
            WHERE item_id = ?
               OR name = ?
               OR name LIKE ? ESCAPE '\\'
               OR item_id LIKE ? ESCAPE '\\'
            ORDER BY match_rank, name ASC
            LIMIT ?
            """,
            (query, query, f"{like}%", f"%{like}%", query, query, f"{like}%", f"%{like}%", limit),
        )
        return [dict(row) for row in rows]

    def _search_ring_items(self, query: str, limit: int) -> list[dict[str, Any]]:
        """搜索纳戒物品。"""

        if not query:
            rows = db.fetch_all(
                """
                SELECT 'ring' AS scope, ring_item_id AS item_id, name, category, quality, usable,
                       NULL AS stack_limit, target_type, 4 AS match_rank
                FROM ring_item_defs
                ORDER BY name ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in rows]

        like = _like_term(query)
        rows = db.fetch_all(
            """
            SELECT 'ring' AS scope, ring_item_id AS item_id, name, category, quality, usable,
                   NULL AS stack_limit, target_type,
                   CASE
                       WHEN ring_item_id = ? THEN 0
                       WHEN name = ? THEN 1
                       WHEN name LIKE ? ESCAPE '\\' THEN 2
                       WHEN ring_item_id LIKE ? ESCAPE '\\' THEN 3
                       ELSE 4
                   END AS match_rank
            FROM ring_item_defs
            WHERE ring_item_id = ?
               OR name = ?
               OR name LIKE ? ESCAPE '\\'
               OR ring_item_id LIKE ? ESCAPE '\\'
            ORDER BY match_rank, name ASC
            LIMIT ?
            """,
            (query, query, f"{like}%", f"%{like}%", query, query, f"{like}%", f"%{like}%", limit),
        )
        return [dict(row) for row in rows]

    def _player_search_row(self, row: dict[str, Any], match_rank: int) -> dict[str, Any]:
        """整理玩家搜索结果。"""

        result = dict(row)
        result["match_rank"] = match_rank
        return result

    def _backpack_quantity_conn(self, conn: sqlite3.Connection, client_id: str, item_id: str) -> int:
        """读取背包物品数量。"""

        row = conn.execute(
            "SELECT quantity FROM backpack_items WHERE client_id = ? AND item_id = ?",
            (client_id, item_id),
        ).fetchone()
        return int(row_value(row, "quantity", 0))

    def _ring_or_gem_quantity_conn(self, conn: sqlite3.Connection, client_id: str, item_id: str, category: str) -> int:
        """读取纳戒或宝石库存数量。"""

        if category == "宝石":
            row = conn.execute(
                "SELECT quantity FROM gem_items WHERE client_id = ? AND gem_id = ? AND level = 1",
                (client_id, item_id),
            ).fetchone()
            return int(row_value(row, "quantity", 0))
        row = conn.execute(
            "SELECT quantity FROM ring_items WHERE client_id = ? AND ring_item_id = ?",
            (client_id, item_id),
        ).fetchone()
        return int(row_value(row, "quantity", 0))

    def _mark_operation_status(
        self,
        operation_id: int,
        status: str,
        error_message: str = "",
        *,
        operator_ip: str = "",
        user_agent: str = "",
    ) -> None:
        """更新后台操作状态。"""

        current_at = ts()
        db.execute(
            """
            UPDATE admin_operations
            SET status = ?,
                error_message = ?,
                updated_at = ?,
                operator_ip = COALESCE(NULLIF(?, ''), operator_ip),
                user_agent = COALESCE(NULLIF(?, ''), user_agent)
            WHERE operation_id = ?
            """,
            (status, error_message, current_at, operator_ip, user_agent, int(operation_id)),
        )

    def _store_failed_operation(
        self,
        *,
        admin: dict[str, Any],
        request_id: str,
        action_type: str,
        target_ref: str,
        item_ref: str,
        item_scope: str,
        stones_amount: int,
        quantity: int,
        reason: str,
        note: str,
        error_message: str,
        operator_ip: str,
        user_agent: str,
        created_at: str,
        expires_at: str,
    ) -> int:
        """记录一次预览失败。"""

        cursor = db.execute(
            """
            INSERT INTO admin_operations (
                request_id, admin_id, action_type, target_client_id,
                target_name_snapshot, item_scope, item_id_snapshot,
                item_name_snapshot, stones_amount, quantity, reason, note,
                before_json, after_json, warnings_json, rollback_json,
                status, error_message, operator_ip, user_agent,
                created_at, confirmed_at, executed_at, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, '', ?, ?, '', ?, ?, ?, ?, '{}', '{}', '[]', '{}', 'failed', ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                request_id,
                int(row_value(admin, "admin_id", 0)),
                action_type,
                target_ref,
                item_scope,
                item_ref,
                int(stones_amount),
                int(quantity),
                reason,
                note,
                error_message,
                operator_ip,
                user_agent,
                created_at,
                expires_at,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    def _write_audit_log(
        self,
        *,
        operation_id: int,
        request_id: str,
        admin: dict[str, Any],
        stage: str,
        action_type: str,
        target_client_id: str = "",
        target_name_snapshot: str = "",
        item_scope: str = "",
        item_id_snapshot: str = "",
        item_name_snapshot: str = "",
        stones_amount: int = 0,
        quantity: int = 0,
        status: str,
        error_message: str = "",
        detail: dict[str, Any] | None = None,
        operator_ip: str = "",
        user_agent: str = "",
        created_at: str | None = None,
    ) -> int:
        """写入审计日志库。"""

        try:
            return self.log_db.record_operation(
                operation_id=operation_id,
                request_id=request_id,
                admin_id=int(row_value(admin, "admin_id", 0)),
                admin_username=str(row_value(admin, "username", "")),
                stage=stage,
                action_type=action_type,
                target_client_id=target_client_id,
                target_name_snapshot=target_name_snapshot,
                item_scope=item_scope,
                item_id_snapshot=item_id_snapshot,
                item_name_snapshot=item_name_snapshot,
                stones_amount=int(stones_amount),
                quantity=int(quantity),
                status=status,
                error_message=error_message,
                detail=detail or {},
                operator_ip=operator_ip,
                user_agent=user_agent,
                created_at=created_at or ts(),
            )
        except Exception:
            return 0

    def _operation_row_to_public(self, row: dict[str, Any]) -> dict[str, Any]:
        """把操作记录整理成前端可用结构。"""

        result = dict(row)
        result["before"] = _json_obj(row_value(row, "before_json", "{}"), {})
        result["after"] = _json_obj(row_value(row, "after_json", "{}"), {})
        result["warnings"] = _json_obj(row_value(row, "warnings_json", "[]"), [])
        result["rollback"] = _json_obj(row_value(row, "rollback_json", "{}"), {})
        result["created_at"] = row_value(row, "created_at", "")
        result["confirmed_at"] = row_value(row, "confirmed_at", "")
        result["executed_at"] = row_value(row, "executed_at", "")
        return result

    def _public_admin(self, row: dict[str, Any]) -> dict[str, Any]:
        """去掉密码字段后的管理员信息。"""

        return {
            "admin_id": int(row_value(row, "admin_id", 0)),
            "username": row_value(row, "username", ""),
            "role": row_value(row, "role", "super_admin"),
            "is_active": int(row_value(row, "is_active", 1)),
            "failed_login_count": int(row_value(row, "failed_login_count", 0)),
            "locked_until": row_value(row, "locked_until", ""),
            "last_login_at": row_value(row, "last_login_at", ""),
            "created_at": row_value(row, "created_at", ""),
            "updated_at": row_value(row, "updated_at", ""),
        }

    def _record_login_failure_conn(self, conn: sqlite3.Connection, admin_id: int) -> None:
        """记录登录失败次数。"""

        row = conn.execute("SELECT failed_login_count, locked_until FROM admin_users WHERE admin_id = ?", (admin_id,)).fetchone()
        if not row:
            return
        failed_count = int(row_value(row, "failed_login_count", 0)) + 1
        locked_until = row_value(row, "locked_until", "")
        if failed_count >= LOGIN_LOCK_THRESHOLD:
            locked_until = ts(now() + timedelta(minutes=LOGIN_LOCK_MINUTES))
        conn.execute(
            """
            UPDATE admin_users
            SET failed_login_count = ?,
                locked_until = ?,
                updated_at = ?
            WHERE admin_id = ?
            """,
            (failed_count, locked_until or None, ts(), admin_id),
        )

    def _scalar(self, sql: str, params: tuple[Any, ...] = (), default: Any = 0) -> Any:
        """读取单个标量值。"""

        row = db.fetch_one(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    def _meta_value(self, key: str, default: str = "") -> str:
        """读取后台元信息。"""

        row = db.fetch_one("SELECT value FROM admin_meta WHERE key = ?", (key,))
        return str(row_value(row, "value", default) or default).strip()

    def _meta_set(self, key: str, value: str) -> None:
        """写入后台元信息。"""

        db.execute(
            """
            INSERT INTO admin_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _meta_set_conn(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        """在事务里写入后台元信息。"""

        conn.execute(
            """
            INSERT INTO admin_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _validate_admin_username(self, username: str) -> str:
        """校验管理员账号。"""

        ok, result = validate_name(_text(username))
        if not ok:
            raise AdminOperationError(result)
        return result

    def _validate_admin_password(self, password: str) -> str:
        """校验管理员密码。"""

        clean = str(password or "")
        if len(clean) < 6:
            raise AdminOperationError("管理员密码至少需要 6 个字符。")
        if any(ch.isspace() for ch in clean):
            raise AdminOperationError("管理员密码不能包含空白字符。")
        return clean

    def _read_bootstrap_token_file(self) -> str:
        """读取一次性密钥文本文件。"""

        if not BOOTSTRAP_TOKEN_FILE.exists():
            return ""
        return _text(BOOTSTRAP_TOKEN_FILE.read_text(encoding="utf-8"))

    def _write_bootstrap_token_file(self, token: str) -> None:
        """写入一次性密钥文本文件。"""

        BOOTSTRAP_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOOTSTRAP_TOKEN_FILE.write_text(token, encoding="utf-8")

    @staticmethod
    def _is_valid_bootstrap_token(token: str) -> bool:
        """判断一次性密钥是否符合 16 位字符串要求。"""

        value = _text(token)
        return len(value) == 16 and value.isascii()

    def _payload_int(self, payload: dict[str, Any], *keys: str, default: int = 0) -> int:
        """按多个候选键安全读取整数。"""

        for key in keys:
            raw_value = payload.get(key)
            if raw_value is None or raw_value == "":
                continue
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                continue
        return default


service = AdminBackendService()


def _json_text(value: Any) -> str:
    """把对象转成 JSON 文本。"""

    return json.dumps(value, ensure_ascii=False)


def _json_obj(value: object, default: Any) -> Any:
    """安全解析 JSON。"""

    if not value:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """把密码转换成 PBKDF2 哈希。"""

    salt_bytes = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    salt_text = salt_hex or salt_bytes.hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 180_000)
    return salt_text, digest.hex()


def _verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    """校验密码哈希。"""

    _salt_text, hash_text = _hash_password(password, salt_hex or None)
    return secrets.compare_digest(hash_text, _text(expected_hash))


def _hash_session_token(token: str) -> str:
    """生成会话令牌哈希。"""

    return hashlib.sha256(_text(token).encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    """把输入整理成干净字符串。"""

    return str(value or "").strip()


def _int_or_default(value: object, default: int = 0) -> int:
    """把输入安全转成整数。"""

    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _like_term(value: str) -> str:
    """准备 SQLite LIKE 通配符。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = [
    "ADMIN_SESSION_COOKIE",
    "AdminBackendService",
    "AdminLogDB",
    "AdminOperationError",
    "BOOTSTRAP_TOKEN_FILE",
    "service",
]
