import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "andi_seko").lower().lstrip("@")
_raw_owner = os.getenv("OWNER_ID", "").strip()
try:
    OWNER_ID: int | None = int(_raw_owner) if _raw_owner else None
except ValueError:
    OWNER_ID = None
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "secretary")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "llama-3.1-8b-instant")
DATA_DIR = os.getenv("DATA_DIR", "")

# ── Database ──
# Если задан DATABASE_URL (postgres:// или postgresql://) — используется Postgres
# (Neon, Supabase, Koyeb, Northflank и т.д.). Иначе — SQLite файл.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Turso / libSQL (опционально): TURSO_DATABASE_URL=libsql://xxx.turso.io TURSO_AUTH_TOKEN=...
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
