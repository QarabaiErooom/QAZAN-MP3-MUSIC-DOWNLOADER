# db.py
"""SQLite persistence layer for QAZAN (aiosqlite)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import aiosqlite

import config

log = logging.getLogger("qazan.db")

DB_FILE = config.resolve_path(config.DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    video_title TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    worker_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    file_path TEXT,
    chat_id INTEGER,
    file_size INTEGER NOT NULL DEFAULT 0,
    duration_sec REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_chat ON tasks(chat_id);
CREATE INDEX IF NOT EXISTS idx_tasks_title ON tasks(video_title);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stats_daily (
    date TEXT PRIMARY KEY,
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0
);
"""

TERMINAL_STATUSES = ("done", "failed", "skipped")

ALLOWED_TASK_FIELDS = {
    "status",
    "worker_id",
    "attempts",
    "error_msg",
    "started_at",
    "finished_at",
    "file_path",
    "video_title",
    "file_size",
    "duration_sec",
    "chat_id",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _cast(key: str, value: Any) -> Any:
    default = config.DEFAULT_SETTINGS.get(key)
    if isinstance(default, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on", "да")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return default
    return str(value)


async def _connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=8000")
    return conn


async def init_db() -> None:
    conn = await _connect()
    try:
        await conn.executescript(SCHEMA)
        await conn.commit()

        ts = _now()
        for key, value in config.DEFAULT_SETTINGS.items():
            await conn.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?, ?, ?)",
                (key, _serialize(value), ts),
            )
        await conn.commit()
        log.info("Database ready: %s", DB_FILE)
    finally:
        await conn.close()


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
async def get_setting(key: str) -> Any:
    conn = await _connect()
    try:
        cursor = await conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return config.DEFAULT_SETTINGS.get(key)
        return _cast(key, row["value"])
    finally:
        await conn.close()


async def set_setting(key: str, value: Any) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, _serialize(value), _now()),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_all_settings() -> Dict[str, Any]:
    conn = await _connect()
    try:
        cursor = await conn.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        raw = {row["key"]: row["value"] for row in rows}
        result: Dict[str, Any] = {}
        for key, default in config.DEFAULT_SETTINGS.items():
            result[key] = _cast(key, raw[key]) if key in raw else default
        for key, value in raw.items():
            if key not in result:
                result[key] = value
        return result
    finally:
        await conn.close()


# ----------------------------------------------------------------------------
# Tasks
# ----------------------------------------------------------------------------
async def enqueue_task(url: str, chat_id: Optional[int] = None) -> int:
    conn = await _connect()
    try:
        cursor = await conn.execute(
            "INSERT INTO tasks(url, status, created_at, chat_id, attempts) VALUES(?, 'pending', ?, ?, 0)",
            (url, _now(), chat_id),
        )
        await conn.commit()
        task_id = int(cursor.lastrowid or 0)
        log.info("Task enqueued: id=%s url=%s chat=%s", task_id, url, chat_id)
        return task_id
    finally:
        await conn.close()


async def update_task_status(task_id: int, **fields: Any) -> None:
    updates = {key: value for key, value in fields.items() if key in ALLOWED_TASK_FIELDS}
    if not updates:
        return
    sets = ", ".join(f"{key}=?" for key in updates)
    params: List[Any] = list(updates.values()) + [task_id]
    conn = await _connect()
    try:
        await conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", params)
        await conn.commit()
    finally:
        await conn.close()


async def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    conn = await _connect()
    try:
        cursor = await conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_tasks_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    conn = await _connect()
    try:
        cursor = await conn.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY id ASC",
            list(ids),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_queue() -> List[Dict[str, Any]]:
    conn = await _connect()
    try:
        cursor = await conn.execute(
            "SELECT * FROM tasks WHERE status IN ('pending','downloading','processing') "
            "ORDER BY CASE status WHEN 'pending' THEN 1 ELSE 0 END, id ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_history(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 100

    conn = await _connect()
    try:
        if status in TERMINAL_STATUSES:
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE status IN ('done','failed','skipped') "
                "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def has_done_title(title: str) -> bool:
    if not title:
        return False
    conn = await _connect()
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM tasks WHERE video_title=? AND status='done' LIMIT 1", (title,)
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await conn.close()


async def record_task_result(status: str, file_size: int = 0) -> None:
    today = date.today().isoformat()
    success = 1 if status == "done" else 0
    failed = 1 if status == "failed" else 0
    size = int(file_size or 0)
    conn = await _connect()
    try:
        await conn.execute(
            """INSERT INTO stats_daily(date, total, success, failed, bytes)
               VALUES(?, 1, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 total = total + 1,
                 success = success + excluded.success,
                 failed = failed + excluded.failed,
                 bytes = bytes + excluded.bytes""",
            (today, success, failed, size),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_dashboard_stats() -> Dict[str, Any]:
    today = date.today().isoformat()
    conn = await _connect()
    try:
        cursor = await conn.execute(
            "SELECT total, success, failed, bytes FROM stats_daily WHERE date=?", (today,)
        )
        row = await cursor.fetchone()
        total = int(row["total"]) if row else 0
        success = int(row["success"]) if row else 0
        failed = int(row["failed"]) if row else 0
        total_bytes = int(row["bytes"]) if row else 0

        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status='skipped' "
            "AND substr(COALESCE(finished_at, created_at), 1, 10)=?",
            (today,),
        )
        skipped_row = await cursor.fetchone()
        skipped = int(skipped_row["c"]) if skipped_row else 0

        cursor = await conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks "
            "WHERE status IN ('pending','downloading','processing') GROUP BY status"
        )
        counts = {r["status"]: int(r["c"]) for r in await cursor.fetchall()}
        pending = counts.get("pending", 0)
        active = counts.get("downloading", 0) + counts.get("processing", 0)

        cursor = await conn.execute(
            "SELECT SUM(file_size) AS b, SUM(duration_sec) AS d FROM tasks "
            "WHERE status='done' AND substr(COALESCE(finished_at, created_at), 1, 10)=?",
            (today,),
        )
        speed_row = await cursor.fetchone()
        speed_bytes = float(speed_row["b"] or 0) if speed_row else 0.0
        speed_secs = float(speed_row["d"] or 0) if speed_row else 0.0
        avg_speed = speed_bytes / speed_secs if speed_secs > 0 and speed_bytes > 0 else 0.0

        if avg_speed <= 0:
            cursor = await conn.execute(
                "SELECT SUM(file_size) AS b, SUM(duration_sec) AS d FROM tasks WHERE status='done'"
            )
            speed_row = await cursor.fetchone()
            speed_bytes = float(speed_row["b"] or 0) if speed_row else 0.0
            speed_secs = float(speed_row["d"] or 0) if speed_row else 0.0
            avg_speed = speed_bytes / speed_secs if speed_secs > 0 and speed_bytes > 0 else 0.0

        cursor = await conn.execute(
            "SELECT date, total, success, failed FROM stats_daily ORDER BY date DESC LIMIT 7"
        )
        daily = [dict(r) for r in await cursor.fetchall()]
        daily.reverse()

        cursor = await conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 10")
        recent = [dict(r) for r in await cursor.fetchall()]

        return {
            "today": {
                "total": total,
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "bytes": total_bytes,
            },
            "queue": {"pending": pending, "active": active},
            "avg_speed": round(avg_speed, 2),
            "daily": daily,
            "recent": recent,
        }
    finally:
        await conn.close()


async def cleanup_old_tasks(days: int) -> int:
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return 0

    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    conn = await _connect()
    try:
        cursor = await conn.execute(
            "DELETE FROM tasks WHERE status IN ('done','failed','skipped') "
            "AND COALESCE(finished_at, created_at) < ?",
            (cutoff,),
        )
        await conn.commit()
        deleted = int(cursor.rowcount or 0)
        if deleted:
            log.info("Cleanup removed %s old tasks (older than %s days)", deleted, days)
        return deleted
    finally:
        await conn.close()