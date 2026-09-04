import os
import asyncio
from datetime import datetime
import config

DB_PATH = (config.DATA_DIR + "/") if config.DATA_DIR else ""
DB_PATH += "secretary.db"

# ── Determine backend ──
DATABASE_URL = getattr(config, "DATABASE_URL", "") or ""
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

_pool = None
_pool_lock = asyncio.Lock()

# Normalise postgres:// → postgresql:// for asyncpg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


async def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        import asyncpg
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        return _pool


def _is_postgres() -> bool:
    return bool(IS_POSTGRES and DATABASE_URL)


# ─── SQLite helpers (unchanged logic) ───
import aiosqlite as _aiosqlite  # always available as fallback


async def init_db():
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    is_cyclic INTEGER DEFAULT 0,
                    interval_seconds INTEGER,
                    is_active INTEGER DEFAULT 1,
                    target_chat_id BIGINT,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS message_map (
                    bot_msg_id BIGINT PRIMARY KEY,
                    from_user_id BIGINT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS owner_info (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    user_id BIGINT NOT NULL,
                    username TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    from_user_id BIGINT NOT NULL,
                    from_username TEXT NOT NULL,
                    text TEXT,
                    photo_file_id TEXT,
                    is_from_owner INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS known_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT NOT NULL,
                    first_name TEXT DEFAULT '',
                    updated_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    remind_offset_minutes INTEGER DEFAULT 0,
                    remind_at TEXT NOT NULL,
                    color TEXT DEFAULT '#5b7fff',
                    target_chat_id BIGINT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT (now()::text)
                );
            """)
            # Ensure target_chat_id column exists (for old DBs)
            try:
                await conn.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS target_chat_id BIGINT")
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS target_chat_id BIGINT")
            except Exception:
                pass
        return

    # SQLite
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                is_cyclic INTEGER DEFAULT 0,
                interval_seconds INTEGER,
                is_active INTEGER DEFAULT 1,
                target_chat_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS message_map (
                bot_msg_id INTEGER PRIMARY KEY,
                from_user_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_info (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER NOT NULL,
                from_username TEXT NOT NULL,
                text TEXT,
                photo_file_id TEXT,
                is_from_owner INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS known_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                first_name TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL,
                remind_offset_minutes INTEGER DEFAULT 0,
                remind_at TEXT NOT NULL,
                color TEXT DEFAULT '#5b7fff',
                target_chat_id INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


async def migrate_db():
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS target_chat_id BIGINT")
            except Exception:
                pass
            try:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS calendar_events (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        event_date TEXT NOT NULL,
                        event_time TEXT NOT NULL,
                        remind_offset_minutes INTEGER DEFAULT 0,
                        remind_at TEXT NOT NULL,
                        color TEXT DEFAULT '#5b7fff',
                        target_chat_id BIGINT,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT (now()::text)
                    );
                """)
            except Exception:
                pass
            try:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT DEFAULT (now()::text)
                    );
                """)
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS target_chat_id BIGINT")
            except Exception:
                pass
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE reminders ADD COLUMN target_chat_id INTEGER")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    remind_offset_minutes INTEGER DEFAULT 0,
                    remind_at TEXT NOT NULL,
                    color TEXT DEFAULT '#5b7fff',
                    target_chat_id INTEGER,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
            """)
            await db.commit()
        except Exception:
            pass


async def set_owner(user_id: int, username: str):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO owner_info (id, user_id, username) VALUES (1, $1, $2) ON CONFLICT (id) DO UPDATE SET user_id=$1, username=$2",
                user_id, username,
            )
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO owner_info (id, user_id, username) VALUES (1, ?, ?)",
            (user_id, username),
        )
        await db.commit()


async def get_owner_id() -> int | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT user_id FROM owner_info WHERE id = 1")
            return row["user_id"] if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM owner_info WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def add_reminder(text: str, remind_at: str, is_cyclic: bool = False, interval_seconds: int | None = None, target_chat_id: int | None = None) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO reminders (text, remind_at, is_cyclic, interval_seconds, target_chat_id) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                text, remind_at, int(is_cyclic), interval_seconds, target_chat_id,
            )
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (text, remind_at, is_cyclic, interval_seconds, target_chat_id) VALUES (?, ?, ?, ?, ?)",
            (text, remind_at, int(is_cyclic), interval_seconds, target_chat_id),
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_reminders() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM reminders WHERE is_active = 1 ORDER BY remind_at")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM reminders WHERE is_active = 1 ORDER BY remind_at") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_reminder_by_id(reminder_id: int) -> dict | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def delete_reminder(reminder_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM reminders WHERE id = $1", reminder_id)
            # asyncpg returns "DELETE 1"
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()
        return cursor.rowcount > 0


async def delete_all_reminders() -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM reminders")
            try:
                return int(res.split()[-1])
            except Exception:
                return 0
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM reminders")
        await db.commit()
        return cursor.rowcount


async def update_remind_at(reminder_id: int, new_remind_at: str):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE reminders SET remind_at = $1 WHERE id = $2", new_remind_at, reminder_id)
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (new_remind_at, reminder_id))
        await db.commit()


async def save_message_map(bot_msg_id: int, from_user_id: int):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO message_map (bot_msg_id, from_user_id) VALUES ($1, $2) ON CONFLICT (bot_msg_id) DO UPDATE SET from_user_id=$2",
                bot_msg_id, from_user_id,
            )
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO message_map (bot_msg_id, from_user_id) VALUES (?, ?)",
            (bot_msg_id, from_user_id),
        )
        await db.commit()


async def get_original_user_id(bot_msg_id: int) -> int | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT from_user_id FROM message_map WHERE bot_msg_id = $1", bot_msg_id)
            return row["from_user_id"] if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT from_user_id FROM message_map WHERE bot_msg_id = ?", (bot_msg_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def save_message(from_user_id: int, from_username: str, text: str | None = None, photo_file_id: str | None = None, is_from_owner: bool = False) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO messages (from_user_id, from_username, text, photo_file_id, is_from_owner) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                from_user_id, from_username, text, photo_file_id, int(is_from_owner),
            )
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO messages (from_user_id, from_username, text, photo_file_id, is_from_owner) VALUES (?, ?, ?, ?, ?)",
            (from_user_id, from_username, text, photo_file_id, int(is_from_owner)),
        )
        await db.commit()
        return cursor.lastrowid


async def get_messages(limit: int = 50) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM messages ORDER BY id DESC LIMIT $1", limit)
            return [dict(r) for r in reversed(rows)]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in reversed(rows)]


async def get_all_reminders() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM reminders ORDER BY remind_at")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM reminders ORDER BY remind_at") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_known_user(user_id: int, username: str, first_name: str = ""):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO known_users (user_id, username, first_name, updated_at) VALUES ($1, $2, $3, now()::text) ON CONFLICT (user_id) DO UPDATE SET username=$2, first_name=$3, updated_at=now()::text",
                user_id, username, first_name,
            )
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO known_users (user_id, username, first_name, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (user_id, username, first_name),
        )
        await db.commit()


async def get_known_user_by_username(username: str) -> dict | None:
    clean = username.lower().lstrip("@")
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM known_users WHERE LOWER(username) = LOWER($1)", clean)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM known_users WHERE LOWER(username) = ?", (clean,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_known_users() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM known_users ORDER BY updated_at DESC")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM known_users ORDER BY updated_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# ─── Calendar Events ───

async def add_calendar_event(title: str, description: str, event_date: str, event_time: str, remind_offset_minutes: int, remind_at: str, color: str = "#5b7fff", target_chat_id: int | None = None) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO calendar_events (title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
                title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id,
            )
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO calendar_events (title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id) VALUES (?,?,?,?,?,?,?,?)",
            (title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id),
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_calendar_events() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM calendar_events WHERE is_active=1 ORDER BY event_date, event_time")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM calendar_events WHERE is_active=1 ORDER BY event_date, event_time") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_calendar_event_by_id(event_id: int) -> dict | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM calendar_events WHERE id=$1", event_id)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_calendar_event(event_id: int, title: str, description: str, event_date: str, event_time: str, remind_offset_minutes: int, remind_at: str, color: str, target_chat_id: int | None = None):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE calendar_events SET title=$1, description=$2, event_date=$3, event_time=$4, remind_offset_minutes=$5, remind_at=$6, color=$7, target_chat_id=$8 WHERE id=$9",
                title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id, event_id,
            )
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE calendar_events SET title=?, description=?, event_date=?, event_time=?, remind_offset_minutes=?, remind_at=?, color=?, target_chat_id=? WHERE id=?",
            (title, description, event_date, event_time, remind_offset_minutes, remind_at, color, target_chat_id, event_id),
        )
        await db.commit()


async def delete_calendar_event(event_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM calendar_events WHERE id=$1", event_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        await db.commit()
        return cursor.rowcount > 0


# ─── App Settings (for theme etc) ───

async def get_setting(key: str) -> str | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM app_settings WHERE key=$1", key)
            return row["value"] if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM app_settings WHERE key=?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str):
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES ($1,$2,now()::text) ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=now()::text",
                key, value,
            )
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?,?,datetime('now'))",
            (key, value),
        )
        await db.commit()


async def get_all_settings() -> dict:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM app_settings")
            return {r["key"]: r["value"] for r in rows}
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM app_settings") as cursor:
            rows = await cursor.fetchall()
            return {r[0]: r[1] for r in rows}


# ─── Cleanup expired (для автоочистки хоста) ───

async def delete_expired_reminders(now_str: str | None = None) -> int:
    """Удаляет одноразовые напоминания, у которых remind_at < now. Цикличные не трогает."""
    if now_str is None:
        from datetime import datetime as _dt
        import pytz as _pytz, config as _cfg
        _tz = _pytz.timezone(_cfg.TIMEZONE)
        now_str = _dt.now(_tz).strftime("%Y-%m-%d %H:%M:%S")
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM reminders WHERE is_cyclic = 0 AND remind_at < $1", now_str)
            try:
                return int(res.split()[-1])
            except Exception:
                return 0
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM reminders WHERE is_cyclic = 0 AND remind_at < ?", (now_str,))
        await db.commit()
        return cursor.rowcount


async def delete_expired_calendar_events(now_str: str | None = None, grace_hours: int = 0) -> int:
    """Удаляет события календаря, у которых event_date+event_time < now - grace.
    grace_hours=0 — сразу после наступления события, >0 — хранить N часов после."""
    if now_str is None:
        from datetime import datetime as _dt, timedelta as _td
        import pytz as _pytz, config as _cfg
        _tz = _pytz.timezone(_cfg.TIMEZONE)
        now = _dt.now(_tz)
        if grace_hours:
            now = now - _td(hours=grace_hours)
        now_str = now.strftime("%Y-%m-%d %H:%M")
    # сравниваем как строки: event_date(YYYY-MM-DD) + ' ' + event_time(HH:MM) < now_str(YYYY-MM-DD HH:MM)
    # берём первые 16 символов now_str для сравнения
    cmp = now_str[:16]
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            # конкатенация в postgres: event_date || ' ' || event_time
            res = await conn.execute("DELETE FROM calendar_events WHERE (event_date || ' ' || event_time) < $1", cmp)
            try:
                return int(res.split()[-1])
            except Exception:
                return 0
    async with _aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM calendar_events WHERE (event_date || ' ' || event_time) < ?", (cmp,))
        await db.commit()
        return cursor.rowcount


async def cleanup_expired(grace_hours: int = 0) -> dict:
    """Комплексная очистка: напоминания + события. Возвращает счётчики."""
    r = await delete_expired_reminders()
    c = await delete_expired_calendar_events(grace_hours=grace_hours)
    return {"reminders": r, "calendar_events": c}
