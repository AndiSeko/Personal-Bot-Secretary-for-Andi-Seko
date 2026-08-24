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
            # Ensure target_chat_id column exists (for old DBs)
            try:
                await conn.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS target_chat_id BIGINT")
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
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE reminders ADD COLUMN target_chat_id INTEGER")
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
