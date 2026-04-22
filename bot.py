import re
import asyncio
import logging
from datetime import datetime, timedelta

import uvicorn

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

import pytz

import config
import db

logger = logging.getLogger(__name__)

router = Router()
scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
tz = pytz.timezone(config.TIMEZONE)


def parse_relative_time(time_str: str) -> datetime:
    total_seconds = 0
    matches = re.findall(r'(\d+)([smhdw])', time_str.lower())
    if not matches:
        raise ValueError(f"Неверный формат: {time_str}")
    for value, unit in matches:
        value = int(value)
        multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        total_seconds += value * multipliers[unit]
    return datetime.now(tz) + timedelta(seconds=total_seconds)


def parse_absolute_time(time_str: str) -> datetime:
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m %H:%M", "%d.%m %H:%M:%S", "%H:%M", "%H:%M:%S"):
        try:
            dt = datetime.strptime(time_str, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            if dt.month == 1 and dt.day == 1 and fmt.startswith("%H"):
                now = datetime.now(tz)
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                result = tz.localize(dt)
                if result < now:
                    result += timedelta(days=1)
                return result
            return tz.localize(dt)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат: {time_str}")


def parse_time(time_str: str) -> datetime:
    try:
        return parse_relative_time(time_str)
    except ValueError:
        return parse_absolute_time(time_str)


def format_interval(seconds: int) -> str:
    if seconds % 604800 == 0:
        return f"каждые {seconds // 604800} нед."
    if seconds % 86400 == 0:
        return f"каждые {seconds // 86400} дн."
    if seconds % 3600 == 0:
        return f"каждые {seconds // 3600} ч."
    if seconds % 60 == 0:
        return f"каждые {seconds // 60} мин."
    return f"каждые {seconds} сек."


def schedule_reminder(reminder_id: int, remind_at: datetime, bot: Bot):
    scheduler.add_job(
        fire_reminder,
        trigger=DateTrigger(run_date=remind_at),
        id=f"reminder_{reminder_id}",
        replace_existing=True,
        args=[reminder_id, bot],
    )


async def fire_reminder(reminder_id: int, bot: Bot):
    reminder = await db.get_reminder_by_id(reminder_id)
    if not reminder:
        return

    owner_id = await db.get_owner_id()
    if not owner_id:
        return

    prefix = "🔔🔁 Цикличное напоминание" if reminder['is_cyclic'] else "🔔 Напоминание"
    try:
        await bot.send_message(owner_id, f"{prefix}:\n{reminder['text']}")
    except Exception as e:
        logger.error("Failed to send reminder %s: %s", reminder_id, e)
        return

    if reminder['is_cyclic']:
        next_time = datetime.now(tz) + timedelta(seconds=reminder['interval_seconds'])
        await db.update_remind_at(reminder_id, next_time.strftime("%Y-%m-%d %H:%M:%S"))
        schedule_reminder(reminder_id, next_time, bot)
    else:
        await db.deactivate_reminder(reminder_id)


async def load_reminders(bot: Bot):
    reminders = await db.get_active_reminders()
    now = datetime.now(tz)
    for r in reminders:
        remind_at = tz.localize(datetime.strptime(r['remind_at'], "%Y-%m-%d %H:%M:%S"))
        if r['is_cyclic'] and remind_at < now:
            interval = timedelta(seconds=r['interval_seconds'])
            while remind_at < now:
                remind_at += interval
            await db.update_remind_at(r['id'], remind_at.strftime("%Y-%m-%d %H:%M:%S"))
        elif not r['is_cyclic'] and remind_at < now:
            await db.deactivate_reminder(r['id'])
            continue
        schedule_reminder(r['id'], remind_at, bot)


class IsOwner(Filter):
    async def __call__(self, message: Message) -> bool:
        return config.OWNER_ID is not None and message.from_user.id == config.OWNER_ID


class IsNotOwner(Filter):
    async def __call__(self, message: Message) -> bool:
        return config.OWNER_ID is None or message.from_user.id != config.OWNER_ID


@router.message(Command("start"))
async def cmd_start(message: Message):
    username = (message.from_user.username or "").lower()
    user_id = message.from_user.id
    stored_owner = await db.get_owner_id()

    if user_id == stored_owner or username == config.OWNER_USERNAME:
        config.OWNER_ID = user_id
        await db.set_owner(user_id, username)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📱 Открыть кабинет", web_app=WebAppInfo(url=config.WEB_URL))
        ]])
        await message.answer(
            "👋 Привет, босс! Я твой личный секретарь.\n\n"
            "📋 Команды:\n"
            "/remind <время> <текст> — разовое напоминание\n"
            "  Время: 5m / 2h / 1d / 22.04.2026 15:30\n"
            "/recurring <интервал> <текст> — цикличное напоминание\n"
            "  Интервал: 30m / 1h / 2d / 1w\n"
            "/list — список напоминаний\n"
            "/delete <id> — удалить напоминание\n"
            "/deleteall — удалить все напоминания\n"
            "/app — открыть веб-кабинет",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"Привет! Я личный секретарь @{config.OWNER_USERNAME}.\n"
            "Напиши мне сообщение, и я перешлю его."
        )


@router.message(IsOwner(), Command("app"))
async def cmd_app(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📱 Открыть кабинет", web_app=WebAppInfo(url=config.WEB_URL))
    ]])
    await message.answer("📱 Веб-кабинет секретаря:", reply_markup=kb)


def parse_remind_args(text: str) -> tuple[str, str]:
    tokens = text.split(maxsplit=1)
    if len(tokens) < 2:
        return "", ""
    args = tokens[1]
    parts = args.split()
    if len(parts) < 2:
        return "", ""

    if re.match(r'^\d+[smhdw]', parts[0], re.IGNORECASE):
        return parts[0], " ".join(parts[1:])

    if re.match(r'^\d{1,2}\.\d{1,2}(\.\d{4})?$', parts[0]):
        if len(parts) >= 3 and re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', parts[1]):
            return f"{parts[0]} {parts[1]}", " ".join(parts[2:])
        return parts[0], " ".join(parts[1:])

    if re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', parts[0]):
        return parts[0], " ".join(parts[1:])

    return parts[0], " ".join(parts[1:])


@router.message(IsOwner(), Command("remind"))
async def cmd_remind(message: Message, bot: Bot):
    time_str, text = parse_remind_args(message.text)
    if not time_str or not text:
        await message.answer(
            "❌ Формат: /remind <время> <текст>\n"
            "Примеры:\n"
            "  /remind 5m Проверить почту\n"
            "  /remind 2h Позвонить\n"
            "  /remind 22.04.2026 15:30 Встреча\n"
            "  /remind 10:00 Утренний отчёт"
        )
        return

    try:
        remind_at = parse_time(time_str)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return

    if remind_at < datetime.now(tz):
        await message.answer("❌ Это время уже прошло!")
        return

    remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
    reminder_id = await db.add_reminder(text, remind_at_str)
    schedule_reminder(reminder_id, remind_at, bot)

    await message.answer(
        f"✅ Напоминание #{reminder_id} установлено!\n"
        f"⏰ {remind_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📝 {text}"
    )


@router.message(IsOwner(), Command("recurring"))
async def cmd_recurring(message: Message, bot: Bot):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Формат: /recurring <интервал> <текст>\n"
            "Примеры:\n"
            "  /recurring 30m Пить воду\n"
            "  /recurring 1h Проверить задачи\n"
            "  /recurring 1d Утренний отчёт\n"
            "  /recurring 1w Еженедельный отчёт"
        )
        return

    try:
        remind_at = parse_relative_time(parts[1])
        interval_seconds = int((remind_at - datetime.now(tz)).total_seconds())
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return

    if interval_seconds < 60:
        await message.answer("❌ Минимальный интервал — 1 минута!")
        return

    remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
    reminder_id = await db.add_reminder(parts[2], remind_at_str, is_cyclic=True, interval_seconds=interval_seconds)
    schedule_reminder(reminder_id, remind_at, bot)

    await message.answer(
        f"✅ Цикличное напоминание #{reminder_id} установлено!\n"
        f"🔁 {format_interval(interval_seconds)}\n"
        f"⏰ Первое срабатывание: {remind_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📝 {parts[2]}"
    )


@router.message(IsOwner(), Command("list"))
async def cmd_list(message: Message):
    reminders = await db.get_active_reminders()
    if not reminders:
        await message.answer("📭 Нет активных напоминаний.")
        return

    lines = ["📋 Активные напоминания:\n"]
    for r in reminders:
        remind_at = datetime.strptime(r['remind_at'], "%Y-%m-%d %H:%M:%S")
        if r['is_cyclic']:
            lines.append(
                f"🔁 #{r['id']} — {r['text']}\n"
                f"   {format_interval(r['interval_seconds'])}, след.: {remind_at.strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            lines.append(
                f"🔔 #{r['id']} — {r['text']}\n"
                f"   ⏰ {remind_at.strftime('%d.%m.%Y %H:%M')}"
            )

    await message.answer("\n".join(lines))


@router.message(IsOwner(), Command("delete"))
async def cmd_delete(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Формат: /delete <id>")
        return

    try:
        reminder_id = int(parts[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом!")
        return

    try:
        scheduler.remove_job(f"reminder_{reminder_id}")
    except Exception:
        pass

    if await db.deactivate_reminder(reminder_id):
        await message.answer(f"✅ Напоминание #{reminder_id} удалено.")
    else:
        await message.answer(f"❌ Напоминание #{reminder_id} не найдено.")


@router.message(IsOwner(), Command("deleteall"))
async def cmd_deleteall(message: Message):
    reminders = await db.get_active_reminders()
    for r in reminders:
        try:
            scheduler.remove_job(f"reminder_{r['id']}")
        except Exception:
            pass

    count = await db.deactivate_all_reminders()
    await message.answer(f"✅ Удалено напоминаний: {count}")


@router.message(IsNotOwner(), F.text)
async def forward_text_to_owner(message: Message, bot: Bot):
    if config.OWNER_ID is None:
        await message.answer("⚠️ Владелец ещё не авторизован. Попробуйте позже.")
        return

    user = message.from_user
    tag = f"@{user.username}" if user.username else user.first_name

    try:
        sent = await bot.send_message(config.OWNER_ID, f"[{tag}]: {message.text}")
        await db.save_message_map(sent.message_id, user.id)
        await db.save_message(user.id, tag, text=message.text, is_from_owner=False)
    except Exception as e:
        logger.error("Failed to forward message: %s", e)
        await message.answer("⚠️ Не удалось доставить сообщение.")


@router.message(IsNotOwner(), F.photo)
async def forward_photo_to_owner(message: Message, bot: Bot):
    if config.OWNER_ID is None:
        await message.answer("⚠️ Владелец ещё не авторизован. Попробуйте позже.")
        return

    user = message.from_user
    tag = f"@{user.username}" if user.username else user.first_name
    caption = f"[{tag}]: {message.caption}" if message.caption else f"[{tag}]: 📷 Фото"

    try:
        sent = await bot.send_photo(
            config.OWNER_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
        )
        await db.save_message_map(sent.message_id, user.id)
        await db.save_message(user.id, tag, text=message.caption, photo_file_id=message.photo[-1].file_id, is_from_owner=False)
    except Exception as e:
        logger.error("Failed to forward photo: %s", e)
        await message.answer("⚠️ Не удалось доставить фото.")


@router.message(IsOwner(), F.reply_to_message, F.text)
async def reply_to_user(message: Message, bot: Bot):
    original_user_id = await db.get_original_user_id(message.reply_to_message.message_id)
    if original_user_id is None:
        return

    try:
        await bot.send_message(original_user_id, message.text)
        owner_tag = f"@{config.OWNER_USERNAME}"
        await db.save_message(config.OWNER_ID, owner_tag, text=message.text, is_from_owner=True)
        await message.answer("✅ Ответ отправлен.")
    except Exception as e:
        logger.error("Failed to send reply: %s", e)
        await message.answer("⚠️ Не удалось отправить ответ.")


@router.message(IsOwner(), F.reply_to_message, F.photo)
async def reply_photo_to_user(message: Message, bot: Bot):
    original_user_id = await db.get_original_user_id(message.reply_to_message.message_id)
    if original_user_id is None:
        return

    try:
        await bot.send_photo(original_user_id, photo=message.photo[-1].file_id, caption=message.caption)
        owner_tag = f"@{config.OWNER_USERNAME}"
        await db.save_message(config.OWNER_ID, owner_tag, text=message.caption, photo_file_id=message.photo[-1].file_id, is_from_owner=True)
        await message.answer("✅ Фото отправлено.")
    except Exception as e:
        logger.error("Failed to send photo reply: %s", e)
        await message.answer("⚠️ Не удалось отправить фото.")


async def on_startup(bot: Bot):
    await db.init_db()

    owner_id = await db.get_owner_id()
    if owner_id:
        config.OWNER_ID = owner_id

    await load_reminders(bot)

    if not scheduler.running:
        scheduler.start()

    import web
    web.setup(bot)

    config_obj = uvicorn.Config(app=web.app, host="0.0.0.0", port=config.WEB_PORT, log_level="info")
    server = uvicorn.Server(config_obj)
    asyncio.create_task(server.serve())

    logger.info("Bot + Web App started (port %s)", config.WEB_PORT)


async def on_shutdown(bot: Bot):
    scheduler.shutdown(wait=False)
    logger.info("Bot stopped")


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN not set in .env")
        return

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties())
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
