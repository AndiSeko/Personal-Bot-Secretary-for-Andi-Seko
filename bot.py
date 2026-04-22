import asyncio
import logging
from datetime import datetime, timedelta

import uvicorn

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import db
import utils
import ai

logger = logging.getLogger(__name__)

router = Router()
scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)


async def load_reminders(bot: Bot):
    reminders = await db.get_active_reminders()
    now = datetime.now(utils.tz)
    for r in reminders:
        remind_at = utils.tz.localize(datetime.strptime(r['remind_at'], "%Y-%m-%d %H:%M:%S"))
        if r['is_cyclic'] and remind_at < now:
            interval = timedelta(seconds=r['interval_seconds'])
            while remind_at < now:
                remind_at += interval
            await db.update_remind_at(r['id'], remind_at.strftime("%Y-%m-%d %H:%M:%S"))
        elif not r['is_cyclic'] and remind_at < now:
            await db.delete_reminder(r['id'])
            continue
        utils.schedule_reminder(r['id'], remind_at, bot, scheduler)


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
            "/app — открыть веб-кабинет\n"
            "/ask <вопрос> — спросить AI-ассистента\n"
            "/clearai — очистить историю диалога с AI",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"Привет! Я личный секретарь Andi Seko.\n"
            "Напиши мне сообщение, и я перешлю его."
        )


@router.message(IsOwner(), Command("app"))
async def cmd_app(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📱 Открыть кабинет", web_app=WebAppInfo(url=config.WEB_URL))
    ]])
    await message.answer("📱 Веб-кабинет секретаря:", reply_markup=kb)


@router.message(IsOwner(), Command("clearai"))
async def cmd_clearai(message: Message):
    ai.clear_history()
    await message.answer("🧹 История диалога с AI очищена.")


@router.message(IsOwner(), F.text, ~F.reply_to_message, ~Command())
async def owner_text_to_ai(message: Message):
    if not ai.is_available():
        return
    msg = await message.answer("🤔 Думаю...")
    answer = await ai.ask(message.text)
    await msg.edit_text(answer)


@router.message(IsOwner(), Command("remind"))
async def cmd_remind(message: Message, bot: Bot):
    time_str, text = utils.parse_remind_args(message.text)
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
        remind_at = utils.parse_time(time_str)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return

    if remind_at < datetime.now(utils.tz):
        await message.answer("❌ Это время уже прошло!")
        return

    remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
    reminder_id = await db.add_reminder(text, remind_at_str)
    utils.schedule_reminder(reminder_id, remind_at, bot, scheduler)

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
        remind_at = utils.parse_relative_time(parts[1])
        interval_seconds = int((remind_at - datetime.now(utils.tz)).total_seconds())
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return

    if interval_seconds < 60:
        await message.answer("❌ Минимальный интервал — 1 минута!")
        return

    remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
    reminder_id = await db.add_reminder(parts[2], remind_at_str, is_cyclic=True, interval_seconds=interval_seconds)
    utils.schedule_reminder(reminder_id, remind_at, bot, scheduler)

    await message.answer(
        f"✅ Цикличное напоминание #{reminder_id} установлено!\n"
        f"🔁 {utils.format_interval(interval_seconds)}\n"
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
                f"   {utils.format_interval(r['interval_seconds'])}, след.: {remind_at.strftime('%d.%m.%Y %H:%M')}"
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

    if await db.delete_reminder(reminder_id):
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

    count = await db.delete_all_reminders()
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

    from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats

    await bot.set_my_commands(
        [
            BotCommand(command="remind", description="Разовое напоминание"),
            BotCommand(command="recurring", description="Цикличное напоминание"),
            BotCommand(command="list", description="Список напоминаний"),
            BotCommand(command="delete", description="Удалить напоминание"),
            BotCommand(command="deleteall", description="Удалить все"),
            BotCommand(command="clearai", description="Очистить контекст AI"),
            BotCommand(command="app", description="Веб-кабинет"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    owner_id = await db.get_owner_id()
    if owner_id:
        config.OWNER_ID = owner_id
        await bot.set_my_commands(
            [
                BotCommand(command="remind", description="Разовое напоминание"),
                BotCommand(command="recurring", description="Цикличное напоминание"),
                BotCommand(command="list", description="Список напоминаний"),
                BotCommand(command="delete", description="Удалить напоминание"),
                BotCommand(command="deleteall", description="Удалить все"),
                BotCommand(command="clearai", description="Очистить контекст AI"),
                BotCommand(command="app", description="Веб-кабинет"),
            ],
            scope=BotCommandScopeChat(chat_id=owner_id),
        )

    await load_reminders(bot)

    ai.init()

    if not scheduler.running:
        scheduler.start()

    import web
    web.setup(bot, scheduler)

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
