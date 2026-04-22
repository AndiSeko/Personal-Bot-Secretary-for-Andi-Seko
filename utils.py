import re
from datetime import datetime, timedelta

import pytz

import config

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
    scheduler.add_job(
        _fire_reminder,
        trigger=DateTrigger(run_date=remind_at),
        id=f"reminder_{reminder_id}",
        replace_existing=True,
        args=[reminder_id, bot, scheduler],
    )


async def _fire_reminder(reminder_id: int, bot, scheduler):
    import db
    reminder = await db.get_reminder_by_id(reminder_id)
    if not reminder:
        return
    owner_id = await db.get_owner_id()
    if not owner_id:
        return
    prefix = "🔁 Цикличное напоминание" if reminder['is_cyclic'] else "🔔 Напоминание"
    try:
        await bot.send_message(owner_id, f"{prefix}:\n{reminder['text']}")
    except Exception:
        return
    if reminder['is_cyclic']:
        next_time = datetime.now(tz) + timedelta(seconds=reminder['interval_seconds'])
        await db.update_remind_at(reminder_id, next_time.strftime("%Y-%m-%d %H:%M:%S"))
        schedule_reminder(reminder_id, next_time, bot, scheduler)
    else:
        await db.delete_reminder(reminder_id)
