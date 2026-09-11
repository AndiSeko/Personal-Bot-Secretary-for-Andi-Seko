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


async def load_calendar_events(bot: Bot):
    events = await db.get_all_calendar_events()
    now = datetime.now(utils.tz)
    for ev in events:
        try:
            remind_at = utils.tz.localize(datetime.strptime(ev['remind_at'], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if remind_at < now:
            # просроченные события не планируем; они будут удалены автоочисткой по event_date
            continue
        utils.schedule_calendar_event(ev['id'], remind_at, bot, scheduler)


async def auto_cleanup():
    """Автоудаление прошедших одноразовых напоминаний и прошедших событий календаря — чтобы не засорять БД/хост."""
    try:
        now = datetime.now(utils.tz)
        # — reminders: только одноразовые с remind_at в прошлом
        rems = await db.get_all_reminders()
        cleaned_r = 0
        for r in rems:
            if r.get('is_cyclic'):
                continue
            try:
                dt = utils.tz.localize(datetime.strptime(r['remind_at'][:19], "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            if dt < now:
                try:
                    scheduler.remove_job(f"reminder_{r['id']}")
                except Exception:
                    pass
                await db.delete_reminder(r['id'])
                cleaned_r += 1
        # — calendar_events: по event_date + event_time
        evs = await db.get_all_calendar_events()
        cleaned_c = 0
        for ev in evs:
            try:
                ev_dt = utils.tz.localize(datetime.strptime(f"{ev['event_date']} {ev['event_time']}", "%Y-%m-%d %H:%M"))
            except Exception:
                continue
            if ev_dt < now:
                try:
                    scheduler.remove_job(f"calendar_{ev['id']}")
                except Exception:
                    pass
                await db.delete_calendar_event(ev['id'])
                cleaned_c += 1
        if cleaned_r or cleaned_c:
            logger.info("Auto-cleanup: удалено %s напоминаний, %s событий календаря", cleaned_r, cleaned_c)
    except Exception as e:
        logger.error("Auto-cleanup failed: %s", e)


async def morning_digest(bot: Bot):
    try:
        rems = await db.get_all_reminders()
        evs = await db.get_all_calendar_events()
        tasks = await db.get_tasks()
        # filter for today
        from datetime import date
        today = datetime.now(utils.tz).strftime("%Y-%m-%d")
        today_evs = [e for e in evs if e["event_date"]==today]
        today_tasks = [t for t in tasks if t.get("due_date")==today and t["status"]!="done"]
        if not rems and not today_evs and not today_tasks:
            return
        lines = ["☀️ Доброе утро! На сегодня:\n"]
        if today_evs:
            lines.append("📅 События:")
            for e in today_evs:
                lines.append(f" • {e['event_time']} — {e['title']}")
        if today_tasks:
            lines.append("\n📋 Задачи:")
            for t in today_tasks:
                lines.append(f" • {t['title']}")
        if rems:
            lines.append("\n🔔 Напоминания:")
            for r in rems[:5]:
                lines.append(f" • {r['text']} — {r['remind_at'][:16]}")
        # AI summary if available
        if ai.is_available() and len("\n".join(lines))>20:
            try:
                prompt = "Сделай краткий план дня из списка:\n" + "\n".join(lines)
                ai_text = await ai.ask(prompt)
                if ai_text:
                    lines.append("\n🤖 AI:\n"+ai_text[:800])
            except Exception:
                pass
        await bot.send_message(config.OWNER_ID, "\n".join(lines))
    except Exception as e:
        logger.error("morning_digest failed: %s", e)

async def evening_digest(bot: Bot):
    try:
        tasks = await db.get_tasks()
        done = len([t for t in tasks if t["status"]=="done"])
        open_cnt = len([t for t in tasks if t["status"]!="done"])
        exps = await db.get_expenses(limit=20)
        total = sum([e["amount"] for e in exps if e["exp_date"]==datetime.now(utils.tz).strftime("%Y-%m-%d")])
        lines = ["🌙 Вечерний отчёт:\n", f"Задачи: {done} выполнено, {open_cnt} открытых"]
        if total:
            lines.append(f"Траты сегодня: {total}")
        if exps:
            lines.append("Последние траты:")
            for e in exps[:5]:
                lines.append(f" • {e['amount']} {e['category']} — {e['comment']}")
        if ai.is_available():
            try:
                prompt = "Сделай вечерний отчёт и мотивацию на завтра из: " + "\n".join(lines)
                ai_text = await ai.ask(prompt)
                if ai_text:
                    lines.append("\n🤖 AI:\n"+ai_text[:800])
            except Exception:
                pass
        await bot.send_message(config.OWNER_ID, "\n".join(lines))
    except Exception as e:
        logger.error("evening_digest failed: %s", e)


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


class NotCommand(Filter):
    async def __call__(self, message: Message) -> bool:
        return not message.text.startswith("/")


@router.message(IsOwner(), F.text, lambda m: m.text.lower().startswith("напомни"))
async def cmd_natural_remind_free(message: Message, bot: Bot):
    raw = message.text.split("напомни",1)[1].strip() if "напомни" in message.text.lower() else message.text.strip()
    if not raw:
        return
    dt, txt = utils.parse_natural_remind(raw)
    if not dt or not txt:
        dt, txt = await utils.parse_with_ai(raw)
    if not dt or not txt:
        return
    if dt < datetime.now(utils.tz):
        await message.answer("❌ Время уже прошло, попробуй иначе")
        return
    rid = await db.add_reminder(txt, dt.strftime("%Y-%m-%d %H:%M:%S"))
    utils.schedule_reminder(rid, dt, bot, scheduler)
    await message.answer(f"✅ Понял! Напомню {dt.strftime('%d.%m.%Y %H:%M')}\n📝 {txt}")


@router.message(IsOwner(), F.text, NotCommand(), ~F.reply_to_message)
async def owner_text_to_ai(message: Message):
    if not ai.is_available():
        return
    msg = await message.answer("🤔 Думаю...")
    # Стриминг как в OpenClaw: постепенная печать с курсором ▌
    import time
    from aiogram.enums import ParseMode
    full = ""
    last_edit = 0
    cursor = " ▌"
    try:
        async for chunk in ai.ask_stream(message.text):
            full += chunk
            now = time.time()
            # троттлинг 0.5с чтобы не словить FloodWait
            if now - last_edit < 0.5:
                continue
            last_edit = now
            # во время стрима шлём без HTML чтобы не ломать незакрытые теги
            try:
                await msg.edit_text(full + cursor)
            except Exception:
                pass
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error("AI stream failed: %s", e)
        # fallback на обычный ask
        full = await ai.ask(message.text)

    # финальный рендер с HTML (спойлеры, жирный, etc.)
    # убираем курсор и рендерим красиво
    if not full:
        full = await ai.ask(message.text)
    try:
        await msg.edit_text(full, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await msg.edit_text(full)
        except Exception:
            pass


@router.message(IsOwner(), Command("remind"))
async def cmd_remind(message: Message, bot: Bot):
    # 1) пробуем естественный язык: "/remind завтра в 9 утра позвонить"
    raw = message.text[len("/remind"):].strip()
    dt, txt = utils.parse_natural_remind(raw)
    remind_at = None
    text = None
    if dt and txt:
        remind_at = dt
        text = txt
    else:
        # 2) пробуем AI fallback для естественного
        dt2, txt2 = await utils.parse_with_ai(raw)
        if dt2 and txt2:
            remind_at = dt2
            text = txt2
        else:
            # 3) классический парсер
            time_str, t = utils.parse_remind_args(message.text)
            if not time_str or not t:
                await message.answer(
                    "❌ Формат: /remind <время> <текст>\n"
                    "Примеры:\n"
                    "  /remind 5m Проверить почту\n"
                    "  /remind 2h Позвонить\n"
                    "  /remind 22.04.2026 15:30 Встреча\n"
                    "  /remind 10:00 Утренний отчёт\n"
                    "  /remind завтра в 9 утра Позвонить\n"
                    "  /remind через 2 часа Проверить"
                )
                return
            try:
                remind_at = utils.parse_time(time_str)
            except ValueError:
                # пробуем естественный на time_str
                nat = utils.parse_natural_ru(time_str)
                if nat:
                    remind_at = nat
                else:
                    await message.answer(f"❌ Не понял время: {time_str}")
                    return
            text = t

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


@router.message(IsOwner(), Command("cleanup"))
async def cmd_cleanup(message: Message):
    await auto_cleanup()
    await message.answer("🧹 Очистка выполнена: просроченные напоминания и события календаря удалены.")


# ─── Tasks ───
@router.message(IsOwner(), Command("task"))
async def cmd_task(message: Message):
    raw = message.text[len("/task"):].strip()
    if not raw:
        tasks = await db.get_tasks()
        if not tasks:
            await message.answer("📋 Нет задач. /task <название> — создать\n/task <id> done — завершить\n/tasks — список")
            return
        lines = ["📋 Задачи:\n"]
        for t in tasks[:20]:
            st = "✅" if t["status"]=="done" else "⬜"
            due = f" | {t['due_date']}" if t["due_date"] else ""
            lines.append(f"{st} #{t['id']} — {t['title']}{due}")
        await message.answer("\n".join(lines))
        return
    # check for done
    if raw.lower().endswith(" done"):
        try:
            tid = int(raw.split()[0])
            await db.update_task(tid, status="done")
            await message.answer(f"✅ Задача #{tid} выполнена")
            return
        except Exception:
            pass
    # create
    # support: title | priority | due_date (simple)
    title = raw
    tid = await db.add_task(title)
    await message.answer(f"✅ Задача #{tid} создана: {title}\n/tasks — список, /done {tid} — завершить")

@router.message(IsOwner(), Command("tasks"))
async def cmd_tasks(message: Message):
    await cmd_task(message)

@router.message(IsOwner(), Command("done"))
async def cmd_done(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Используй: /done <id>")
        return
    try:
        tid = int(parts[1])
        st = await db.toggle_task(tid)
        await message.answer(f"{'✅' if st=='done' else '⬜'} Задача #{tid} — {st}")
    except Exception as e:
        await message.answer(f"❌ {e}")

# ─── Notes ───
@router.message(IsOwner(), Command("note"))
async def cmd_note(message: Message):
    raw = message.text[len("/note"):].strip()
    if not raw:
        notes = await db.get_notes(limit=10)
        if not notes:
            await message.answer("📝 Нет заметок. /note <заголовок> | <текст> — создать\n/search <запрос> — поиск")
            return
        lines = ["📝 Заметки:\n"]
        for n in notes:
            lines.append(f"#{n['id']} — {n['title']} | {n['tags']}")
        await message.answer("\n".join(lines))
        return
    if "|" in raw:
        title, body = raw.split("|",1)
        title=title.strip(); body=body.strip()
    else:
        title=raw[:30]; body=raw
    nid = await db.add_note(title, body)
    await message.answer(f"✅ Заметка #{nid} сохранена")

@router.message(IsOwner(), Command("search"))
async def cmd_search(message: Message):
    q = message.text[len("/search"):].strip()
    if not q:
        await message.answer("Используй: /search <запрос>")
        return
    notes = await db.get_notes(search=q, limit=10)
    if not notes:
        await message.answer("Ничего не найдено")
        return
    lines = [f"🔍 По '{q}':\n"]
    for n in notes:
        lines.append(f"#{n['id']} {n['title']}: {n['body'][:80]}")
    await message.answer("\n".join(lines))

# ─── Habits ───
@router.message(IsOwner(), Command("habit"))
async def cmd_habit(message: Message):
    raw = message.text[len("/habit"):].strip()
    if not raw:
        habits = await db.get_habits()
        if not habits:
            await message.answer("🏃 Нет привычек. /habit <название> — создать\n/habit done <id> — отметить")
            return
        lines=["🏃 Привычки:\n"]
        for h in habits:
            lines.append(f"#{h['id']} {h['name']} — streak {h['streak']} | last {h['last_done'] or '-'}")
        await message.answer("\n".join(lines))
        return
    if raw.lower().startswith("done"):
        try:
            hid=int(raw.split()[1])
            await db.mark_habit_done(hid)
            await message.answer(f"✅ Привычка #{hid} отмечена")
        except Exception as e:
            await message.answer(f"❌ {e}")
        return
    hid=await db.add_habit(raw)
    await message.answer(f"✅ Привычка #{hid} создана: {raw}")

# ─── Expenses ───
@router.message(IsOwner(), Command("spend"))
async def cmd_spend(message: Message):
    raw = message.text[len("/spend"):].strip()
    if not raw:
        exps = await db.get_expenses(limit=10)
        stats = await db.get_expense_stats()
        lines=[f"💰 Траты: всего {stats['total']} ({stats['cnt']} записей)\n"]
        for e in exps:
            lines.append(f"#{e['id']} {e['amount']} {e['category']} — {e['comment']} | {e['exp_date']}")
        await message.answer("\n".join(lines) or "Нет трат")
        return
    parts = raw.split(maxsplit=1)
    try:
        amount=float(parts[0])
    except:
        await message.answer("Формат: /spend <сумма> [категория] [коммент]\nПример: /spend 500 еда обед")
        return
    cat="other"; comment=""
    if len(parts)>1:
        rest=parts[1].split()
        cat=rest[0]
        comment=" ".join(rest[1:]) if len(rest)>1 else ""
    exp_date=datetime.now(utils.tz).strftime("%Y-%m-%d")
    eid=await db.add_expense(amount, cat, comment, exp_date)
    await message.answer(f"✅ Трата #{eid}: {amount} {cat}")

@router.message(IsOwner(), Command("expenses"))
async def cmd_expenses(message: Message):
    await cmd_spend(message)

# ─── Voice → text ───
@router.message(IsOwner(), F.voice | F.audio)
async def voice_to_task(message: Message, bot: Bot):
    if not ai.is_available():
        await message.answer("⚠️ AI не настроен (GROQ_API_KEY)")
        return
    try:
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        f = await bot.get_file(file_id)
        # download
        import io, httpx
        url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{f.file_path}"
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            ogg = r.content
        # transcribe via Groq whisper
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(ogg); tmp_path=tmp.name
        try:
            groq = ai.get_client()
            if groq:
                with open(tmp_path, "rb") as af:
                    tr = groq.audio.transcriptions.create(model="whisper-large-v3", file=af, language="ru")
                    text = tr.text
            else:
                text = ""
        finally:
            try: os.unlink(tmp_path)
            except: pass
        if not text:
            await message.answer("❌ Не удалось распознать голос")
            return
        await message.answer(f"🎤 Распознано: \"{text}\"\nПытаюсь создать напоминание/задачу...")
        dt, rem = utils.parse_natural_remind(text)
        if dt and rem:
            rid=await db.add_reminder(rem, dt.strftime("%Y-%m-%d %H:%M:%S"))
            utils.schedule_reminder(rid, dt, bot, scheduler)
            await message.answer(f"✅ Напоминание #{rid} на {dt.strftime('%d.%m.%Y %H:%M')}: {rem}")
            return
        # иначе — просто спросить AI или создать задачу
        tid=await db.add_task(text)
        await message.answer(f"✅ Задача #{tid} создана из голоса: {text}")
    except Exception as e:
        logger.error("voice failed: %s", e)
        await message.answer(f"❌ Ошибка голоса: {e}")


@router.message(IsNotOwner(), F.text)
async def forward_text_to_owner(message: Message, bot: Bot):
    if config.OWNER_ID is None:
        await message.answer("⚠️ Владелец ещё не авторизован. Попробуйте позже.")
        return

    user = message.from_user
    tag = f"@{user.username}" if user.username else user.first_name

    await db.save_known_user(user.id, user.username or "", user.first_name or "")

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

    await db.save_known_user(user.id, user.username or "", user.first_name or "")

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
    await db.migrate_db()

    from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats

    await bot.set_my_commands(
        [
            BotCommand(command="remind", description="Разовое напоминание"),
            BotCommand(command="recurring", description="Цикличное напоминание"),
            BotCommand(command="list", description="Список напоминаний"),
            BotCommand(command="delete", description="Удалить напоминание"),
            BotCommand(command="deleteall", description="Удалить все"),
            BotCommand(command="cleanup", description="Очистить просроченное"),
            BotCommand(command="clearai", description="Очистить контекст AI"),
            BotCommand(command="app", description="Веб-кабинет"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    owner_id = await db.get_owner_id()
    if owner_id:
        config.OWNER_ID = owner_id
    elif config.OWNER_ID:
        username = config.OWNER_USERNAME
        await db.set_owner(config.OWNER_ID, username)
        await bot.set_my_commands(
            [
                BotCommand(command="remind", description="Разовое напоминание"),
                BotCommand(command="recurring", description="Цикличное напоминание"),
                BotCommand(command="list", description="Список напоминаний"),
                BotCommand(command="delete", description="Удалить напоминание"),
                BotCommand(command="deleteall", description="Удалить все"),
                BotCommand(command="cleanup", description="Очистить просроченное"),
                BotCommand(command="clearai", description="Очистить контекст AI"),
                BotCommand(command="app", description="Веб-кабинет"),
            ],
            scope=BotCommandScopeChat(chat_id=config.OWNER_ID),
        )

    await load_reminders(bot)
    await load_calendar_events(bot)
    # разовая очистка просроченного сразу при старте
    try:
        await auto_cleanup()
    except Exception as e:
        logger.warning("Initial auto-cleanup failed: %s", e)

    ai.init()

    if not scheduler.running:
        scheduler.start()

    # периодическая автоочистка: каждый час + ежедневно в 04:00
    try:
        scheduler.add_job(auto_cleanup, "interval", hours=1, id="auto_cleanup_hourly", replace_existing=True, max_instances=1)
        scheduler.add_job(auto_cleanup, "cron", hour=4, minute=0, id="auto_cleanup_daily", replace_existing=True, max_instances=1)
        logger.info("Auto-cleanup scheduled (hourly + daily 04:00)")
    except Exception as e:
        logger.warning("Failed to schedule auto-cleanup: %s", e)

    # дайджесты
    try:
        scheduler.add_job(morning_digest, "cron", hour=8, minute=0, args=[bot], id="morning_digest", replace_existing=True, max_instances=1)
        scheduler.add_job(evening_digest, "cron", hour=21, minute=0, args=[bot], id="evening_digest", replace_existing=True, max_instances=1)
        logger.info("Digests scheduled 08:00/21:00")
    except Exception as e:
        logger.warning("Failed to schedule digests: %s", e)

    # вотчеры цен BY — проверка каждый час
    try:
        from watchers import check_watchers as _check_watchers
        scheduler.add_job(lambda: asyncio.create_task(_check_watchers(bot, scheduler)), "interval", hours=1, id="watchers_hourly", replace_existing=True, max_instances=1)
        logger.info("Watchers scheduled hourly")
    except Exception as e:
        logger.warning("Watchers scheduler not set (no watchers.py yet): %s", e)

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
