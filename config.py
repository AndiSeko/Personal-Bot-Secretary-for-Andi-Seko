import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "andi_seko").lower().lstrip("@")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "secretary")

OWNER_ID: int | None = None
