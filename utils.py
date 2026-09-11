import re
import asyncio
import logging
from datetime import datetime, timedelta

import pytz

import config

logger = logging.getLogger(__name__)

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


def parse_natural_ru(text: str) -> datetime | None:
    """Пытается распарсить русские естественные выражения: завтра в 9 утра, через 2 часа, в понедельник 18:30 и т.д."""
    t = text.lower().strip()
    now = datetime.now(tz)
    # через X ... (поддержка составных: через 2 часа 30 минут)
    if "через" in t:
        total = timedelta(0)
        found = False
        for num, unit in re.findall(r'(\d+)\s*(?:минут|минуту|минуты|час|часа|часов|день|дня|дней|недел|секунд|секунду|секунды)', t):
            num = int(num)
            unit = unit.lower()
            if unit.startswith("минут"):
                total += timedelta(minutes=num)
                found = True
            elif unit.startswith("час"):
                total += timedelta(hours=num)
                found = True
            elif unit.startswith("дн") or unit.startswith("день"):
                total += timedelta(days=num)
                found = True
            elif unit.startswith("недел"):
                total += timedelta(weeks=num)
                found = True
            elif unit.startswith("секунд"):
                total += timedelta(seconds=num)
                found = True
        if found:
            return now + total
        # fallback: через 2ч30м etc уже handled by parse_relative
        try:
            return parse_relative_time(t.split("через",1)[1].strip().split()[0])
        except Exception:
            pass

    # завтра / послезавтра / сегодня
    base = None
    if "послезавтра" in t:
        base = now + timedelta(days=2)
    elif "завтра" in t:
        base = now + timedelta(days=1)
    elif "сегодня" in t:
        base = now

    # извлечь время
    hour = None
    minute = 0
    m = re.search(r'в\s*(\d{1,2})(?::(\d{2}))?\s*(утра|дня|вечера|ночи)?', t)
    if not m:
        m = re.search(r'(\d{1,2}):(\d{2})', t)
        if m:
            hour = int(m.group(1)); minute = int(m.group(2))
    else:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        suf = m.group(3) or ""
        if "вечера" in suf and hour < 12:
            hour += 12
        elif "ночи" in suf and hour < 6:
            pass
        elif "утра" in suf and hour == 12:
            hour = 0
        elif "дня" in suf and hour < 12 and hour <= 11:
            # 1 дня -> 13, etc. but 12 дня =12
            if 1 <= hour <= 11:
                hour += 12

    if hour is None:
        # попробовать "9 утра" без "в"
        m2 = re.search(r'(\d{1,2})\s*(утра|дня|вечера|ночи)', t)
        if m2:
            hour = int(m2.group(1))
            suf = m2.group(2)
            if "вечера" in suf and hour < 12:
                hour += 12
            minute = 0
        else:
            # нет явного времени — если есть завтра/сегодня, дефолт 09:00
            if base is not None:
                hour, minute = 9, 0
            else:
                return None

    if base is not None:
        try:
            res = tz.localize(datetime(base.year, base.month, base.day, hour, minute))
        except Exception:
            res = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if "сегодня" in t and res < now:
            res += timedelta(days=1)
        return res

    # дни недели
    weekdays = {"понедельник":0,"вторник":1,"среда":2,"среду":2,"четверг":3,"пятница":4,"пятницу":4,"суббота":5,"субботу":5,"воскресенье":6,"воскресения":6}
    for name, wd in weekdays.items():
        if name in t:
            days_ahead = (wd - now.weekday()) % 7
            if days_ahead == 0:
                # если сегодня этот день — проверить время
                try:
                    cand = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
                except Exception:
                    cand = now
                if cand <= now:
                    days_ahead = 7
            target = now + timedelta(days=days_ahead)
            return tz.localize(datetime(target.year, target.month, target.day, hour, minute))

    return None


def parse_natural_remind(text: str) -> tuple[datetime | None, str]:
    """Пытается выделить время из начала текста (до 5 слов) и вернуть (datetime, оставшийся текст)."""
    # пробуем нарастающие префиксы: первые 1..6 слов как время
    parts = text.strip().split()
    for n in range(6, 0, -1):
        cand = " ".join(parts[:n])
        dt = parse_natural_ru(cand)
        if dt and dt > datetime.now(tz) - timedelta(minutes=1):
            remaining = " ".join(parts[n:]).strip()
            # эвристика: если оставшийся текст пустой — не считаем
            if remaining:
                return dt, remaining
            # если cand содержит числа/время, но remaining пустой — пробуем дальше
        # также пробуем старый parse_time
        try:
            dt2 = parse_time(cand)
            if dt2 and dt2 > datetime.now(tz) - timedelta(minutes=1):
                remaining = " ".join(parts[n:]).strip()
                if remaining:
                    return dt2, remaining
        except Exception:
            pass
    return None, text


async def parse_with_ai(text: str) -> tuple[datetime | None, str]:
    """AI fallback через Groq: просит модель выделить дату/время."""
    try:
        import ai
        if not ai.is_available():
            return None, text
        prompt = f"Извлеки из фразы дату и время для напоминания. Фраза: '{text}'. Сегодня {datetime.now(tz).strftime('%Y-%m-%d %H:%M')}, таймзона {tz}. Ответь строго JSON: {{\"datetime\": \"YYYY-MM-DD HH:MM\" или null, \"text\": \"оставшийся текст\"}}"
        raw = await ai.ask(prompt)
        import json as _json
        # найти JSON в ответе
        m = re.search(r'\{{.*\}}', raw, re.S)
        if not m:
            return None, text
        data = _json.loads(m.group(0))
        dt_str = data.get("datetime")
        remaining = data.get("text") or text
        if not dt_str:
            return None, text
        dt = tz.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
        if dt > datetime.now(tz):
            return dt, remaining
    except Exception as e:
        logger.debug("parse_with_ai failed: %s", e)
    return None, text


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


def schedule_reminder(reminder_id: int, remind_at: datetime, bot, scheduler):
    from apscheduler.triggers.date import DateTrigger
    loop = asyncio.get_event_loop()
    scheduler.add_job(
        _fire_reminder_sync,
        trigger=DateTrigger(run_date=remind_at),
        id=f"reminder_{reminder_id}",
        replace_existing=True,
        args=[reminder_id, bot, scheduler, loop],
    )


def schedule_calendar_event(event_id: int, remind_at: datetime, bot, scheduler):
    from apscheduler.triggers.date import DateTrigger
    loop = asyncio.get_event_loop()
    scheduler.add_job(
        _fire_calendar_sync,
        trigger=DateTrigger(run_date=remind_at),
        id=f"calendar_{event_id}",
        replace_existing=True,
        args=[event_id, bot, scheduler, loop],
    )


def _fire_calendar_sync(event_id: int, bot, scheduler, loop):
    asyncio.run_coroutine_threadsafe(
        _fire_calendar_event(event_id, bot, scheduler), loop
    )


def _fire_reminder_sync(reminder_id: int, bot, scheduler, loop):
    asyncio.run_coroutine_threadsafe(
        _fire_reminder(reminder_id, bot, scheduler), loop
    )


async def _fire_reminder(reminder_id: int, bot, scheduler):
    import db
    reminder = await db.get_reminder_by_id(reminder_id)
    if not reminder:
        logger.warning("Reminder %s not found in DB", reminder_id)
        return
    owner_id = await db.get_owner_id()
    if not owner_id:
        logger.warning("Owner ID not found in DB")
        return

    target_chat_id = reminder.get('target_chat_id') or owner_id
    is_other = target_chat_id != owner_id

    prefix = "🔁 Цикличное напоминание" if reminder['is_cyclic'] else "🔔 Напоминание"
    if is_other:
        text_for_target = f"{prefix}:\n{reminder['text']}"
        text_for_owner = f"✅ Напоминание доставлено: {reminder['text']}"
    else:
        text_for_target = f"{prefix}:\n{reminder['text']}"
        text_for_owner = None

    try:
        await bot.send_message(target_chat_id, text_for_target)
        logger.info("Reminder %s sent to %s", reminder_id, target_chat_id)
    except Exception as e:
        logger.error("Failed to send reminder %s to %s: %s", reminder_id, target_chat_id, e)
        try:
            await bot.send_message(owner_id, f"❌ Не удалось доставить напоминание пользователю: {e}")
        except Exception:
            pass
        return

    if is_other and text_for_owner:
        try:
            await bot.send_message(owner_id, text_for_owner)
        except Exception:
            pass

    if reminder['is_cyclic']:
        # Следующее срабатывание = предыдущее время + интервал (сохраняем wall-clock время),
        # а не now + интервал (дрейф). Если проспали несколько интервалов — догоняем.
        try:
            prev = tz.localize(datetime.strptime(reminder['remind_at'], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            prev = datetime.now(tz)
        next_time = prev + timedelta(seconds=reminder['interval_seconds'])
        now = datetime.now(tz)
        # если бот был оффлайн долго — прокручиваем до будущего
        while next_time < now:
            next_time += timedelta(seconds=reminder['interval_seconds'])
        await db.update_remind_at(reminder_id, next_time.strftime("%Y-%m-%d %H:%M:%S"))
        schedule_reminder(reminder_id, next_time, bot, scheduler)
    else:
        await db.delete_reminder(reminder_id)


async def _fire_calendar_event(event_id: int, bot, scheduler):
    import db
    ev = await db.get_calendar_event_by_id(event_id)
    if not ev:
        logger.warning("Calendar event %s not found", event_id)
        return
    owner_id = await db.get_owner_id()
    if not owner_id:
        logger.warning("Owner ID not found in DB")
        return
    target_chat_id = ev.get('target_chat_id') or owner_id
    is_other = target_chat_id != owner_id

    event_dt_str = f"{ev['event_date']} {ev['event_time']}"
    title = ev['title']
    desc = ev.get('description') or ""
    offset = ev.get('remind_offset_minutes', 0) or 0

    if offset == 0:
        when_txt = f"сейчас ({event_dt_str})"
    else:
        h = offset // 60
        m = offset % 60
        parts = []
        if h:
            parts.append(f"{h}ч")
        if m:
            parts.append(f"{m}м")
        when_txt = f"через {' '.join(parts)} до события" if False else f"напоминание: событие в {event_dt_str} (за {' '.join(parts)} до)"
        # Simplify: show offset

    prefix = "📅 Напоминание о событии"
    body = f"{prefix}:\n<b>{title}</b>\n📆 {event_dt_str}"
    if desc:
        body += f"\n📝 {desc}"
    if offset:
        h = offset // 60; m = offset % 60
        off_str = f"{h}ч {m}м" if h and m else (f"{h}ч" if h else f"{m}м")
        body += f"\n⏰ За {off_str} до события"

    try:
        await bot.send_message(target_chat_id, body, parse_mode="HTML")
        logger.info("Calendar event %s sent to %s", event_id, target_chat_id)
    except Exception as e:
        logger.error("Failed to send calendar event %s: %s", event_id, e)
        try:
            await bot.send_message(owner_id, f"❌ Не удалось доставить событие календаря: {e}")
        except Exception:
            pass
        return
    if is_other:
        try:
            await bot.send_message(owner_id, f"✅ Событие доставлено пользователю: {title}")
        except Exception:
            pass
    # calendar events are one-time, no reschedule - keep record but inactive? we keep it active for calendar view
    # Optionally mark as done but keep visible; do not delete. We just unschedule.
    # If you want to delete after firing, uncomment:
    # await db.delete_calendar_event(event_id)
