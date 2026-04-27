import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "andi_seko").lower().lstrip("@")
OWNER_ID: int | None = int(os.getenv("OWNER_ID", "0")) or None
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "secretary")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")
DATA_DIR = os.getenv("DATA_DIR", "")

OWNER_ID: int | None = None
