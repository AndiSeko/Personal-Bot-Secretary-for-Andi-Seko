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


import logging as _logging
_logger = _logging.getLogger(__name__)

# Если Postgres упал (suspend/expire как Render Free) — не крашим сервис,
# а один раз переключаемся на SQLite и пишем ясный лог.
_PG_FAILED = False


def _is_postgres() -> bool:
    return bool(IS_POSTGRES and DATABASE_URL and not _PG_FAILED)


def _mark_pg_failed(reason: Exception | str):
    global _PG_FAILED, _pool
    _PG_FAILED = True
    _pool = None
    _logger.warning("Postgres недоступен (%s) — переключаюсь на SQLite %s. Проверь DATABASE_URL (Render Free DB expire?)", reason, DB_PATH)


# ─── SQLite helpers (unchanged logic) ───
import aiosqlite as _aiosqlite  # always available as fallback


async def init_db():
    # Probe: если Postgres недоступен (Render Free suspend/expire) —
    # помечаем fallback и идём на SQLite, сервис не падает.
    if _is_postgres():
        try:
            _probe = await _get_pool()
            async with _probe.acquire() as _c:
                await _c.execute("SELECT 1")
        except Exception as _e:
            _mark_pg_failed(_e)
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
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'open',
                    priority INTEGER DEFAULT 1,
                    due_date TEXT,
                    parent_id INTEGER,
                    calendar_event_id INTEGER,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS task_items (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    is_done INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    embedding TEXT,
                    created_at TEXT DEFAULT (now()::text),
                    updated_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS habits (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    streak INTEGER DEFAULT 0,
                    last_done TEXT,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS habit_logs (
                    id SERIAL PRIMARY KEY,
                    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                    done_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    amount REAL NOT NULL,
                    category TEXT DEFAULT 'other',
                    comment TEXT DEFAULT '',
                    exp_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS watchers (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    region TEXT DEFAULT 'minsk',
                    target_price REAL,
                    check_interval INTEGER DEFAULT 3600,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (now()::text)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS watcher_urls (
                    id SERIAL PRIMARY KEY,
                    watcher_id INTEGER NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
                    store TEXT NOT NULL,
                    url TEXT NOT NULL,
                    selector TEXT DEFAULT '',
                    last_price REAL,
                    last_check TEXT,
                    status TEXT DEFAULT 'ok'
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id SERIAL PRIMARY KEY,
                    watcher_url_id INTEGER NOT NULL REFERENCES watcher_urls(id) ON DELETE CASCADE,
                    price REAL NOT NULL,
                    checked_at TEXT DEFAULT (now()::text)
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
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 1,
                due_date TEXT,
                parent_id INTEGER,
                calendar_event_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS task_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                is_done INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                embedding TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                streak INTEGER DEFAULT 0,
                last_done TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                done_date TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                category TEXT DEFAULT 'other',
                comment TEXT DEFAULT '',
                exp_date TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS watchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                region TEXT DEFAULT 'minsk',
                target_price REAL,
                check_interval INTEGER DEFAULT 3600,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS watcher_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watcher_id INTEGER NOT NULL,
                store TEXT NOT NULL,
                url TEXT NOT NULL,
                selector TEXT DEFAULT '',
                last_price REAL,
                last_check TEXT,
                status TEXT DEFAULT 'ok',
                FOREIGN KEY(watcher_id) REFERENCES watchers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watcher_url_id INTEGER NOT NULL,
                price REAL NOT NULL,
                checked_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(watcher_url_id) REFERENCES watcher_urls(id) ON DELETE CASCADE
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
            # new tables for all-features update
            for _q in [
                """CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', status TEXT DEFAULT 'open', priority INTEGER DEFAULT 1, due_date TEXT, parent_id INTEGER, calendar_event_id INTEGER, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS task_items (id SERIAL PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, text TEXT NOT NULL, is_done INTEGER DEFAULT 0, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', embedding TEXT, created_at TEXT DEFAULT (now()::text), updated_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS habits (id SERIAL PRIMARY KEY, name TEXT NOT NULL, streak INTEGER DEFAULT 0, last_done TEXT, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS habit_logs (id SERIAL PRIMARY KEY, habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE, done_date TEXT NOT NULL, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS expenses (id SERIAL PRIMARY KEY, amount REAL NOT NULL, category TEXT DEFAULT 'other', comment TEXT DEFAULT '', exp_date TEXT NOT NULL, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS watchers (id SERIAL PRIMARY KEY, title TEXT NOT NULL, region TEXT DEFAULT 'minsk', target_price REAL, check_interval INTEGER DEFAULT 3600, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT (now()::text))""",
                """CREATE TABLE IF NOT EXISTS watcher_urls (id SERIAL PRIMARY KEY, watcher_id INTEGER NOT NULL REFERENCES watchers(id) ON DELETE CASCADE, store TEXT NOT NULL, url TEXT NOT NULL, selector TEXT DEFAULT '', last_price REAL, last_check TEXT, status TEXT DEFAULT 'ok')""",
                """CREATE TABLE IF NOT EXISTS price_history (id SERIAL PRIMARY KEY, watcher_url_id INTEGER NOT NULL REFERENCES watcher_urls(id) ON DELETE CASCADE, price REAL NOT NULL, checked_at TEXT DEFAULT (now()::text))""",
            ]:
                try:
                    await conn.execute(_q)
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
        # new tables
        try:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'open',
                    priority INTEGER DEFAULT 1,
                    due_date TEXT,
                    parent_id INTEGER,
                    calendar_event_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS task_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    is_done INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    embedding TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS habits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    streak INTEGER DEFAULT 0,
                    last_done TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS habit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    habit_id INTEGER NOT NULL,
                    done_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL NOT NULL,
                    category TEXT DEFAULT 'other',
                    comment TEXT DEFAULT '',
                    exp_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS watchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    region TEXT DEFAULT 'minsk',
                    target_price REAL,
                    check_interval INTEGER DEFAULT 3600,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS watcher_urls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watcher_id INTEGER NOT NULL,
                    store TEXT NOT NULL,
                    url TEXT NOT NULL,
                    selector TEXT DEFAULT '',
                    last_price REAL,
                    last_check TEXT,
                    status TEXT DEFAULT 'ok',
                    FOREIGN KEY(watcher_id) REFERENCES watchers(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watcher_url_id INTEGER NOT NULL,
                    price REAL NOT NULL,
                    checked_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY(watcher_url_id) REFERENCES watcher_urls(id) ON DELETE CASCADE
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


# ─── Tasks (чеклисты) ───

async def add_task(title: str, description: str = "", priority: int = 1, due_date: str | None = None, parent_id: int | None = None) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO tasks (title, description, priority, due_date, parent_id) VALUES ($1,$2,$3,$4,$5) RETURNING id", title, description, priority, due_date, parent_id)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO tasks (title, description, priority, due_date, parent_id) VALUES (?,?,?,?,?)", (title, description, priority, due_date, parent_id))
        await db.commit()
        return cur.lastrowid

async def get_tasks(status: str | None = None) -> list[dict]:
    q = "SELECT * FROM tasks ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, priority DESC, id DESC"
    if status:
        q = "SELECT * FROM tasks WHERE status=$1 ORDER BY due_date" if _is_postgres() else "SELECT * FROM tasks WHERE status=? ORDER BY due_date"
        if _is_postgres():
            pool = await _get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(q, status)
                return [dict(r) for r in rows]
        async with _aiosqlite.connect(DB_PATH) as db:
            db.row_factory = _aiosqlite.Row
            async with db.execute(q, (status,)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(q)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute(q) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_task_by_id(task_id: int) -> dict | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def update_task(task_id: int, title: str | None = None, description: str | None = None, status: str | None = None, priority: int | None = None, due_date: str | None = None):
    # simple patch: only update provided
    fields = []
    vals = []
    if title is not None:
        fields.append("title")
        vals.append(title)
    if description is not None:
        fields.append("description")
        vals.append(description)
    if status is not None:
        fields.append("status")
        vals.append(status)
    if priority is not None:
        fields.append("priority")
        vals.append(priority)
    if due_date is not None:
        fields.append("due_date")
        vals.append(due_date)
    if not fields:
        return
    set_clause = ", ".join([f"{f}=${i+1}" if _is_postgres() else f"{f}=?" for i,f in enumerate(fields)])
    vals.append(task_id)
    q = f"UPDATE tasks SET {set_clause} WHERE id=${len(vals)}" if _is_postgres() else f"UPDATE tasks SET {set_clause} WHERE id=?"
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(q, *vals)
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(q, tuple(vals))
        await db.commit()

async def delete_task(task_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM task_items WHERE task_id=?", (task_id,))
        cur = await db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await db.commit()
        return cur.rowcount > 0

async def toggle_task(task_id: int) -> str | None:
    task = await get_task_by_id(task_id)
    if not task:
        return None
    new_status = "done" if task["status"] != "done" else "open"
    await update_task(task_id, status=new_status)
    return new_status

async def add_task_item(task_id: int, text: str) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO task_items (task_id, text) VALUES ($1,$2) RETURNING id", task_id, text)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO task_items (task_id, text) VALUES (?,?)", (task_id, text))
        await db.commit()
        return cur.lastrowid

async def get_task_items(task_id: int) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM task_items WHERE task_id=$1 ORDER BY id", task_id)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM task_items WHERE task_id=? ORDER BY id", (task_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def toggle_task_item(item_id: int) -> bool | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT is_done FROM task_items WHERE id=$1", item_id)
            if not row:
                return None
            new = 0 if row["is_done"] else 1
            await conn.execute("UPDATE task_items SET is_done=$1 WHERE id=$2", new, item_id)
            return bool(new)
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_done FROM task_items WHERE id=?", (item_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            new = 0 if row[0] else 1
            await db.execute("UPDATE task_items SET is_done=? WHERE id=?", (new, item_id))
            await db.commit()
            return bool(new)

async def delete_task_item(item_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM task_items WHERE id=$1", item_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM task_items WHERE id=?", (item_id,))
        await db.commit()
        return cur.rowcount > 0


# ─── Notes ───

async def add_note(title: str, body: str, tags: str = "") -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO notes (title, body, tags) VALUES ($1,$2,$3) RETURNING id", title, body, tags)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO notes (title, body, tags) VALUES (?,?,?)", (title, body, tags))
        await db.commit()
        return cur.lastrowid

async def get_notes(limit: int = 100, search: str | None = None) -> list[dict]:
    if search:
        like = f"%{search}%"
        if _is_postgres():
            pool = await _get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM notes WHERE title ILIKE $1 OR body ILIKE $1 OR tags ILIKE $1 ORDER BY updated_at DESC LIMIT $2", like, limit)
                return [dict(r) for r in rows]
        async with _aiosqlite.connect(DB_PATH) as db:
            db.row_factory = _aiosqlite.Row
            async with db.execute("SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?", (like, like, like, limit)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM notes ORDER BY updated_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_note_by_id(note_id: int) -> dict | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM notes WHERE id=$1", note_id)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM notes WHERE id=?", (note_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def update_note(note_id: int, title: str | None = None, body: str | None = None, tags: str | None = None):
    fields = []
    vals = []
    if title is not None:
        fields.append("title")
        vals.append(title)
    if body is not None:
        fields.append("body")
        vals.append(body)
    if tags is not None:
        fields.append("tags")
        vals.append(tags)
    if not fields:
        return
    fields.append("updated_at")
    vals.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    set_clause = ", ".join([f"{f}=${i+1}" if _is_postgres() else f"{f}=?" for i,f in enumerate(fields)])
    vals.append(note_id)
    q = f"UPDATE notes SET {set_clause} WHERE id=${len(vals)}" if _is_postgres() else f"UPDATE notes SET {set_clause} WHERE id=?"
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(q, *vals)
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute(q, tuple(vals))
        await db.commit()

async def delete_note(note_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM notes WHERE id=$1", note_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        await db.commit()
        return cur.rowcount > 0


# ─── Habits ───

async def add_habit(name: str) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO habits (name) VALUES ($1) RETURNING id", name)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO habits (name) VALUES (?)", (name,))
        await db.commit()
        return cur.lastrowid

async def get_habits() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM habits ORDER BY created_at")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM habits ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def delete_habit(habit_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM habits WHERE id=$1", habit_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM habit_logs WHERE habit_id=?", (habit_id,))
        cur = await db.execute("DELETE FROM habits WHERE id=?", (habit_id,))
        await db.commit()
        return cur.rowcount > 0

async def mark_habit_done(habit_id: int, done_date: str | None = None) -> int:
    from datetime import datetime as _dt
    import pytz as _pytz, config as _cfg
    _tz = _pytz.timezone(_cfg.TIMEZONE)
    if done_date is None:
        done_date = _dt.now(_tz).strftime("%Y-%m-%d")
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            # avoid duplicate
            exists = await conn.fetchrow("SELECT id FROM habit_logs WHERE habit_id=$1 AND done_date=$2", habit_id, done_date)
            if exists:
                return exists["id"]
            row = await conn.fetchrow("INSERT INTO habit_logs (habit_id, done_date) VALUES ($1,$2) RETURNING id", habit_id, done_date)
            # update streak (naive: count logs)
            cnt = await conn.fetchval("SELECT COUNT(*) FROM habit_logs WHERE habit_id=$1", habit_id)
            await conn.execute("UPDATE habits SET streak=$1, last_done=$2 WHERE id=$3", cnt, done_date, habit_id)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM habit_logs WHERE habit_id=? AND done_date=?", (habit_id, done_date)) as cur:
            row = await cur.fetchone()
            if row:
                return row[0]
        cur = await db.execute("INSERT INTO habit_logs (habit_id, done_date) VALUES (?,?)", (habit_id, done_date))
        await db.commit()
        # update streak
        async with db.execute("SELECT COUNT(*) FROM habit_logs WHERE habit_id=?", (habit_id,)) as cur2:
            cnt = (await cur2.fetchone())[0]
        await db.execute("UPDATE habits SET streak=?, last_done=? WHERE id=?", (cnt, done_date, habit_id))
        await db.commit()
        return cur.lastrowid

async def get_habit_logs(habit_id: int) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM habit_logs WHERE habit_id=$1 ORDER BY done_date DESC", habit_id)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM habit_logs WHERE habit_id=? ORDER BY done_date DESC", (habit_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ─── Expenses ───

async def add_expense(amount: float, category: str, comment: str, exp_date: str) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO expenses (amount, category, comment, exp_date) VALUES ($1,$2,$3,$4) RETURNING id", amount, category, comment, exp_date)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO expenses (amount, category, comment, exp_date) VALUES (?,?,?,?)", (amount, category, comment, exp_date))
        await db.commit()
        return cur.lastrowid

async def get_expenses(limit: int = 100) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM expenses ORDER BY exp_date DESC, id DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM expenses ORDER BY exp_date DESC, id DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def delete_expense(exp_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM expenses WHERE id=$1", exp_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM expenses WHERE id=?", (exp_id,))
        await db.commit()
        return cur.rowcount > 0

async def get_expense_stats() -> dict:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COALESCE(SUM(amount),0) as total, COUNT(*) as cnt FROM expenses")
            return dict(row) if row else {"total":0,"cnt":0}
    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COALESCE(SUM(amount),0) as total, COUNT(*) as cnt FROM expenses") as cur:
            row = await cur.fetchone()
            return {"total": row[0], "cnt": row[1]} if row else {"total":0,"cnt":0}


# ─── Watchers (BY price) ───

async def add_watcher(title: str, region: str, target_price: float | None, check_interval: int) -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO watchers (title, region, target_price, check_interval) VALUES ($1,$2,$3,$4) RETURNING id", title, region, target_price, check_interval)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO watchers (title, region, target_price, check_interval) VALUES (?,?,?,?)", (title, region, target_price, check_interval))
        await db.commit()
        return cur.lastrowid

async def get_watchers() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM watchers ORDER BY created_at DESC")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM watchers ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_watcher_by_id(wid: int) -> dict | None:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM watchers WHERE id=$1", wid)
            return dict(row) if row else None
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM watchers WHERE id=?", (wid,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def delete_watcher(wid: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM watchers WHERE id=$1", wid)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM price_history WHERE watcher_url_id IN (SELECT id FROM watcher_urls WHERE watcher_id=?)", (wid,))
        await db.execute("DELETE FROM watcher_urls WHERE watcher_id=?", (wid,))
        cur = await db.execute("DELETE FROM watchers WHERE id=?", (wid,))
        await db.commit()
        return cur.rowcount > 0

async def add_watcher_url(watcher_id: int, store: str, url: str, selector: str = "") -> int:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("INSERT INTO watcher_urls (watcher_id, store, url, selector) VALUES ($1,$2,$3,$4) RETURNING id", watcher_id, store, url, selector)
            return row["id"]
    async with _aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO watcher_urls (watcher_id, store, url, selector) VALUES (?,?,?,?)", (watcher_id, store, url, selector))
        await db.commit()
        return cur.lastrowid

async def get_watcher_urls(watcher_id: int) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM watcher_urls WHERE watcher_id=$1 ORDER BY id", watcher_id)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM watcher_urls WHERE watcher_id=? ORDER BY id", (watcher_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_all_watcher_urls() -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM watcher_urls ORDER BY watcher_id")
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM watcher_urls ORDER BY watcher_id") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def update_watcher_url_price(url_id: int, price: float, status: str = "ok"):
    from datetime import datetime as _dt
    import pytz as _pytz, config as _cfg
    _tz = _pytz.timezone(_cfg.TIMEZONE)
    now = _dt.now(_tz).strftime("%Y-%m-%d %H:%M:%S")
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE watcher_urls SET last_price=$1, last_check=$2, status=$3 WHERE id=$4", price, now, status, url_id)
            await conn.execute("INSERT INTO price_history (watcher_url_id, price) VALUES ($1,$2)", url_id, price)
        return
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE watcher_urls SET last_price=?, last_check=?, status=? WHERE id=?", (price, now, status, url_id))
        await db.execute("INSERT INTO price_history (watcher_url_id, price) VALUES (?,?)", (url_id, price))
        await db.commit()

async def get_price_history(watcher_url_id: int, limit: int = 50) -> list[dict]:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM price_history WHERE watcher_url_id=$1 ORDER BY checked_at DESC LIMIT $2", watcher_url_id, limit)
            return [dict(r) for r in rows]
    async with _aiosqlite.connect(DB_PATH) as db:
        db.row_factory = _aiosqlite.Row
        async with db.execute("SELECT * FROM price_history WHERE watcher_url_id=? ORDER BY checked_at DESC LIMIT ?", (watcher_url_id, limit)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def delete_watcher_url(url_id: int) -> bool:
    if _is_postgres():
        pool = await _get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute("DELETE FROM watcher_urls WHERE id=$1", url_id)
            return res.split()[-1] != "0"
    async with _aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM price_history WHERE watcher_url_id=?", (url_id,))
        cur = await db.execute("DELETE FROM watcher_urls WHERE id=?", (url_id,))
        await db.commit()
        return cur.rowcount > 0
