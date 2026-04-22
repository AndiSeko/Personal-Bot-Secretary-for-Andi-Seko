import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "andi_seko").lower().lstrip("@")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

OWNER_ID: int | None = None
