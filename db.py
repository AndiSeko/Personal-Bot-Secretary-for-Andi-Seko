import aiosqlite
from datetime import datetime
import config

DB_PATH = (config.DATA_DIR + "/") if config.DATA_DIR else ""
DB_PATH += "secretary.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE reminders ADD COLUMN target_chat_id INTEGER")
            await db.commit()
        except Exception:
            pass


async def set_owner(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO owner_info (id, user_id, username) VALUES (1, ?, ?)",
            (user_id, username),
        )
        await db.commit()


async def get_owner_id() -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM owner_info WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def add_reminder(text: str, remind_at: str, is_cyclic: bool = False, interval_seconds: int | None = None, target_chat_id: int | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (text, remind_at, is_cyclic, interval_seconds, target_chat_id) VALUES (?, ?, ?, ?, ?)",
            (text, remind_at, int(is_cyclic), interval_seconds, target_chat_id),
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminders WHERE is_active = 1 ORDER BY remind_at") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_reminder_by_id(reminder_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def delete_reminder(reminder_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()
        return cursor.rowcount > 0


async def delete_all_reminders() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM reminders")
        await db.commit()
        return cursor.rowcount


async def update_remind_at(reminder_id: int, new_remind_at: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (new_remind_at, reminder_id))
        await db.commit()


async def save_message_map(bot_msg_id: int, from_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO message_map (bot_msg_id, from_user_id) VALUES (?, ?)",
            (bot_msg_id, from_user_id),
        )
        await db.commit()


async def get_original_user_id(bot_msg_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT from_user_id FROM message_map WHERE bot_msg_id = ?", (bot_msg_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def save_message(from_user_id: int, from_username: str, text: str | None = None, photo_file_id: str | None = None, is_from_owner: bool = False) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO messages (from_user_id, from_username, text, photo_file_id, is_from_owner) VALUES (?, ?, ?, ?, ?)",
            (from_user_id, from_username, text, photo_file_id, int(is_from_owner)),
        )
        await db.commit()
        return cursor.lastrowid


async def get_messages(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in reversed(rows)]


async def get_all_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminders ORDER BY remind_at") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_known_user(user_id: int, username: str, first_name: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO known_users (user_id, username, first_name, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (user_id, username, first_name),
        )
        await db.commit()


async def get_known_user_by_username(username: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        clean = username.lower().lstrip("@")
        async with db.execute("SELECT * FROM known_users WHERE LOWER(username) = ?", (clean,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_known_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM known_users ORDER BY updated_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]



