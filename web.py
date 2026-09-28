import os
import json
import hmac
import hashlib
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot

import db
import config
import utils

from datetime import datetime, timedelta
import json as _json

WEB_PASSWORD = os.getenv("WEB_PASSWORD", "secretary")
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", os.getenv("GIT_COMMIT", "local"))[:7]

app = FastAPI(title="Secretary")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

bot_instance: Bot | None = None
_scheduler = None


def setup(bot: Bot, scheduler):
    global bot_instance, _scheduler
    bot_instance = bot
    _scheduler = scheduler


def verify_webapp_signature(init_data: str) -> bool:
    secret_key = hmac.new(
        b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256
    ).digest()

    pairs = urllib.parse.parse_qs(init_data)
    hash_val = pairs.get("hash", [None])[0]
    if not hash_val:
        return False

    check_string = "\n".join(
        f"{k}={v[0]}" for k, v in sorted(pairs.items()) if k != "hash"
    )

    computed = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed, hash_val)


def get_user_from_init_data(init_data: str) -> dict | None:
    pairs = urllib.parse.parse_qs(init_data)
    user_json = pairs.get("user", [None])[0]
    if not user_json:
        return None
    try:
        return json.loads(user_json)
    except Exception:
        return None


def check_auth(request: Request) -> bool:
    session = request.cookies.get("session")
    if session == WEB_PASSWORD:
        return True

    init_data = request.query_params.get("tgWebAppData") or ""
    if init_data and verify_webapp_signature(init_data):
        user = get_user_from_init_data(init_data)
        if user and user.get("id") == config.OWNER_ID:
            return True

    return False


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index(request: Request):
    if request.method == "HEAD":
        return HTMLResponse(status_code=200)
    init_data = request.query_params.get("tgWebAppData") or ""
    tg_auth = False
    if init_data and verify_webapp_signature(init_data):
        user = get_user_from_init_data(init_data)
        if user and user.get("id") == config.OWNER_ID:
            tg_auth = True

    cookie_auth = request.cookies.get("session") == WEB_PASSWORD

    if not tg_auth and not cookie_auth:
        if init_data:
            return HTMLResponse("<html><body style='background:#1a1a2e;color:#e0e0e0;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif'><div style='text-align:center'><h2>Доступ запрещён</h2><p style='color:#6b7280;margin-top:8px'>Этот кабинет только для владельца</p></div></body></html>")
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": False})

    import asyncio as _aio
    import time as _time
    _t0 = _time.monotonic()
    # Было 10+N последовательных roundtrip'ов к Neon — теперь 2 захода:
    # 1) все независимые запросы параллельно, 2) urls вотчеров параллельно.
    (reminders, messages, calendar_events, tasks, notes,
     habits, expenses, watchers, theme_json) = await _aio.gather(
        db.get_all_reminders(),
        db.get_messages(limit=50),
        db.get_all_calendar_events(),
        db.get_tasks(),
        db.get_notes(limit=50),
        db.get_habits(),
        db.get_expenses(limit=50),
        db.get_watchers(),
        db.get_setting("theme"),
    )
    # enrich watchers with urls — параллельно, а не N запросов по очереди
    if watchers:
        _urls_lists = await _aio.gather(*[db.get_watcher_urls(w["id"]) for w in watchers])
        for w, _urls in zip(watchers, _urls_lists):
            w["urls"] = _urls
            # last prices preview
            w["min_price"] = min([u["last_price"] for u in _urls if u.get("last_price")], default=None)
            w["max_price"] = max([u["last_price"] for u in _urls if u.get("last_price")], default=None)
    import logging as _logging
    _logging.getLogger(__name__).debug("index db fetch %.0fms", (_time.monotonic() - _t0) * 1000)

    active_count = len(reminders)
    cyclic_count = sum(1 for r in reminders if r['is_cyclic'])
    msg_count = len(messages)

    def _fmt_dt(s: str, out_fmt="%d.%m.%Y %H:%M"):
        if not s:
            return ""
        # postgres now()::text = "2026-08-29 14:33:45.123+00" — режем до 19 символов
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").strftime(out_fmt)
        except Exception:
            try:
                return datetime.fromisoformat(s.replace(" ", "T")).strftime(out_fmt)
            except Exception:
                return s[:16]

    for r in reminders:
        try:
            dt = datetime.strptime(r['remind_at'][:19], "%Y-%m-%d %H:%M:%S")
            r['remind_at_fmt'] = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            r['remind_at_fmt'] = _fmt_dt(r.get('remind_at',''))
        if r['is_cyclic'] and r['interval_seconds']:
            r['interval_fmt'] = utils.format_interval(r['interval_seconds'])
        else:
            r['interval_fmt'] = ""
        # for calendar integration: extract date
        try:
            r['ev_date'] = datetime.strptime(r['remind_at'][:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        except:
            r['ev_date'] = ""

    for m in messages:
        m['created_at_fmt'] = _fmt_dt(m.get('created_at',''))

    for ev in calendar_events:
        try:
            dt = datetime.strptime(ev['remind_at'], "%Y-%m-%d %H:%M:%S")
            ev['remind_at_fmt'] = dt.strftime("%d.%m.%Y %H:%M")
        except:
            ev['remind_at_fmt'] = ev.get('remind_at','')
        # offset fmt
        off = ev.get('remind_offset_minutes', 0) or 0
        if off:
            h = off // 60; mm = off % 60
            if h and mm:
                ev['offset_fmt'] = f"{h}ч {mm}м до"
            elif h:
                ev['offset_fmt'] = f"{h}ч до"
            else:
                ev['offset_fmt'] = f"{mm}м до"
        else:
            ev['offset_fmt'] = "в момент события"

    # theme settings (theme_json уже получен через gather выше)
    theme = None
    if theme_json:
        try:
            theme = _json.loads(theme_json)
        except:
            theme = None

    resp = templates.TemplateResponse(request, "index.html", {
        "request": request,
        "reminders": reminders,
        "messages": messages,
        "calendar_events": calendar_events,
        "calendar_events_json": _json.dumps(calendar_events, ensure_ascii=False),
        "reminders_json": _json.dumps(reminders, ensure_ascii=False),
        "tasks": tasks,
        "tasks_json": _json.dumps(tasks, ensure_ascii=False),
        "notes": notes,
        "notes_json": _json.dumps(notes, ensure_ascii=False),
        "habits": habits,
        "habits_json": _json.dumps(habits, ensure_ascii=False),
        "expenses": expenses,
        "expenses_json": _json.dumps(expenses, ensure_ascii=False),
        "watchers": watchers,
        "watchers_json": _json.dumps(watchers, ensure_ascii=False),
        "active_count": active_count,
        "cyclic_count": cyclic_count,
        "msg_count": msg_count,
        "calendar_count": len(calendar_events),
        "tasks_count": len(tasks),
        "notes_count": len(notes),
        "watchers_count": len(watchers),
        "owner_username": config.OWNER_USERNAME,
        "owner_id": config.OWNER_ID or 0,
        "known_users": await db.get_all_known_users(),
        "theme_json": _json.dumps(theme) if theme else "null",
        "git_commit": GIT_COMMIT,
    })
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == WEB_PASSWORD:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("session", WEB_PASSWORD, max_age=86400 * 30)
        return response
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": True})


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session")
    return response


@app.post("/reminders/add")
async def add_reminder(request: Request, text: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    mode = form.get("mode", "exact")
    target_username = (form.get("target_username") or "").strip().lstrip("@")
    now = datetime.now(utils.tz)

    target_chat_id = None
    if target_username:
        known = await db.get_known_user_by_username(target_username)
        if known:
            target_chat_id = known['user_id']
        else:
            return RedirectResponse(url="/", status_code=303)

    remind_at = None
    interval_seconds = None

    if mode == "cyclic":
        date_val = form.get("date", "")
        time_val = form.get("time", "")
        if not date_val or not time_val:
            return RedirectResponse(url="/", status_code=303)

        try:
            remind_at = utils.tz.localize(datetime.strptime(f"{date_val} {time_val}", "%Y-%m-%d %H:%M"))
        except ValueError:
            return RedirectResponse(url="/", status_code=303)

        int_d = int(form.get("interval_days", 0) or 0)
        int_h = int(form.get("interval_hours", 0) or 0)
        int_m = int(form.get("interval_minutes", 0) or 0)
        interval_seconds = int_d * 86400 + int_h * 3600 + int_m * 60
        if interval_seconds < 60:
            interval_seconds = 60

        # Сохраняем заданное пользователем время даже если оно в прошлом:
        # сдвигаем по интервалу вперёд пока не окажется в будущем
        if remind_at < now:
            # предотвращаем бесконечный цикл при очень маленьком интервале
            # уже гарантировано interval_seconds >=60
            while remind_at < now:
                remind_at += timedelta(seconds=interval_seconds)

        remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await db.add_reminder(text, remind_at_str, is_cyclic=True, interval_seconds=interval_seconds, target_chat_id=target_chat_id)

    elif mode == "after":
        a_d = int(form.get("after_days", 0) or 0)
        a_h = int(form.get("after_hours", 0) or 0)
        a_m = int(form.get("after_minutes", 0) or 0)
        if a_d + a_h + a_m == 0:
            return RedirectResponse(url="/", status_code=303)

        remind_at = now + timedelta(days=a_d, hours=a_h, minutes=a_m)
        remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await db.add_reminder(text, remind_at_str, target_chat_id=target_chat_id)

    else:
        date_val = form.get("date", "")
        time_val = form.get("time", "")
        if not date_val or not time_val:
            return RedirectResponse(url="/", status_code=303)

        try:
            remind_at = utils.tz.localize(datetime.strptime(f"{date_val} {time_val}", "%Y-%m-%d %H:%M"))
        except ValueError:
            return RedirectResponse(url="/", status_code=303)

        if remind_at < now:
            return RedirectResponse(url="/", status_code=303)

        remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await db.add_reminder(text, remind_at_str, target_chat_id=target_chat_id)

    if bot_instance and remind_at:
        utils.schedule_reminder(reminder_id, remind_at, bot_instance, _scheduler)

    return RedirectResponse(url="/", status_code=303)


@app.get("/api/known-users")
async def api_known_users(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    users = await db.get_all_known_users()
    return [{"user_id": u["user_id"], "username": u["username"], "first_name": u["first_name"]} for u in users]


# ─── Calendar API ───

def _calc_calendar_remind_at(event_date: str, event_time: str, offset_minutes: int) -> datetime | None:
    try:
        dt = utils.tz.localize(datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M"))
        remind_at = dt - timedelta(minutes=offset_minutes)
        return remind_at
    except Exception:
        return None

@app.get("/api/calendar")
async def api_calendar(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    events = await db.get_all_calendar_events()
    rems = await db.get_all_reminders()
    return {"events": events, "reminders": rems}

@app.post("/calendar/add")
async def calendar_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    event_date = (form.get("event_date") or "").strip()
    event_time = (form.get("event_time") or "").strip()
    color = (form.get("color") or "#5b7fff").strip()
    target_username = (form.get("target_username") or "").strip().lstrip("@")
    off_h = int(form.get("offset_hours", 0) or 0)
    off_m = int(form.get("offset_minutes", 0) or 0)
    offset_minutes = off_h*60 + off_m

    if not title or not event_date or not event_time:
        return RedirectResponse(url="/", status_code=303)

    remind_at_dt = _calc_calendar_remind_at(event_date, event_time, offset_minutes)
    if not remind_at_dt:
        return RedirectResponse(url="/", status_code=303)

    target_chat_id = None
    if target_username:
        known = await db.get_known_user_by_username(target_username)
        if known:
            target_chat_id = known['user_id']

    remind_at_str = remind_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    event_id = await db.add_calendar_event(title, description, event_date, event_time, offset_minutes, remind_at_str, color, target_chat_id)

    # Only schedule if remind time is in future
    if remind_at_dt > datetime.now(utils.tz) and bot_instance and _scheduler:
        utils.schedule_calendar_event(event_id, remind_at_dt, bot_instance, _scheduler)

    # support JSON API
    if request.headers.get("accept","").find("json")>=0 or request.query_params.get("json")=="1":
        return JSONResponse({"ok":True, "id": event_id})
    return RedirectResponse(url="/", status_code=303)

@app.post("/calendar/update/{event_id}")
async def calendar_update(request: Request, event_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    event_date = (form.get("event_date") or "").strip()
    event_time = (form.get("event_time") or "").strip()
    color = (form.get("color") or "#5b7fff").strip()
    target_username = (form.get("target_username") or "").strip().lstrip("@")
    off_h = int(form.get("offset_hours", 0) or 0)
    off_m = int(form.get("offset_minutes", 0) or 0)
    offset_minutes = off_h*60 + off_m

    if not title or not event_date or not event_time:
        return RedirectResponse(url="/", status_code=303)

    remind_at_dt = _calc_calendar_remind_at(event_date, event_time, offset_minutes)
    if not remind_at_dt:
        return RedirectResponse(url="/", status_code=303)

    target_chat_id = None
    if target_username:
        known = await db.get_known_user_by_username(target_username)
        if known:
            target_chat_id = known['user_id']

    remind_at_str = remind_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    await db.update_calendar_event(event_id, title, description, event_date, event_time, offset_minutes, remind_at_str, color, target_chat_id)

    # reschedule
    try:
        _scheduler.remove_job(f"calendar_{event_id}")
    except Exception:
        pass
    if remind_at_dt > datetime.now(utils.tz) and bot_instance and _scheduler:
        utils.schedule_calendar_event(event_id, remind_at_dt, bot_instance, _scheduler)

    if request.headers.get("accept","").find("json")>=0 or request.query_params.get("json")=="1":
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.post("/calendar/delete/{event_id}")
async def calendar_delete(request: Request, event_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    try:
        _scheduler.remove_job(f"calendar_{event_id}")
    except Exception:
        pass
    await db.delete_calendar_event(event_id)
    if request.headers.get("accept","").find("json")>=0 or request.query_params.get("json")=="1":
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/theme")
async def api_save_theme(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    data = await request.json()
    # data expected to be dict with colors
    await db.set_setting("theme", _json.dumps(data, ensure_ascii=False))
    return JSONResponse({"ok":True})

@app.get("/api/theme")
async def api_get_theme(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    val = await db.get_setting("theme")
    if not val:
        return JSONResponse({})
    try:
        return JSONResponse(_json.loads(val))
    except:
        return JSONResponse({})


@app.post("/api/parse_natural")
async def api_parse_natural(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error":"empty"})
    dt, remaining = utils.parse_natural_remind(text)
    if dt and remaining:
        return JSONResponse({"ok": True, "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"), "text": remaining, "display": dt.strftime("%d.%m.%Y %H:%M")})
    # try ai
    dt2, rem2 = await utils.parse_with_ai(text)
    if dt2 and rem2:
        return JSONResponse({"ok": True, "datetime": dt2.strftime("%Y-%m-%d %H:%M:%S"), "text": rem2, "display": dt2.strftime("%d.%m.%Y %H:%M"), "ai": True})
    return JSONResponse({"ok": False})


@app.post("/api/cleanup")
async def api_cleanup(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    # удаляем просроченные одноразовые напоминания и прошедшие события
    # также снимаем джобы планировщика если есть
    result = await db.cleanup_expired()
    # попытка снять джобы для удалённых (если остались)
    if _scheduler:
        # для надёжности: перебираем все джобы и удаляем те, у которых id соответствует удалённым?
        # но cleanup_expired уже вернул счётчики, а id неизвестны — джобы для прошедших всё равно истекли
        # дополнительно чистим по времени: пройдёмся по всем напоминаниям/событиям и снимем просроченные джобы
        try:
            for job in list(_scheduler.get_jobs()):
                jid = job.id
                if jid.startswith("reminder_") or jid.startswith("calendar_"):
                    # если run_date в прошлом — удаляем (APScheduler должен сам, но на всякий)
                    if job.next_run_time and job.next_run_time < datetime.now(utils.tz):
                        try:
                            _scheduler.remove_job(jid)
                        except Exception:
                            pass
        except Exception:
            pass
    return JSONResponse({"ok": True, "deleted": result})


# ─── Tasks API ───
@app.get("/api/tasks")
async def api_tasks(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={"error":"forbidden"})
    tasks = await db.get_tasks()
    for t in tasks:
        t["items"] = await db.get_task_items(t["id"])
    return tasks

@app.post("/tasks/add")
async def tasks_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return RedirectResponse(url="/", status_code=303)
    desc = (form.get("description") or "").strip()
    priority = int(form.get("priority", 1) or 1)
    due = (form.get("due_date") or "").strip() or None
    tid = await db.add_task(title, desc, priority, due)
    # subitems
    items_raw = (form.get("items") or "").strip()
    if items_raw:
        for line in items_raw.splitlines():
            line=line.strip()
            if line:
                await db.add_task_item(tid, line)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True, "id":tid})
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/toggle/{task_id}")
async def tasks_toggle(request: Request, task_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.toggle_task(task_id)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/delete/{task_id}")
async def tasks_delete(request: Request, task_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.delete_task(task_id)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.post("/task_items/toggle/{item_id}")
async def task_item_toggle(request: Request, item_id: int):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    await db.toggle_task_item(item_id)
    return JSONResponse({"ok":True})

# ─── Notes API ───
@app.get("/api/notes")
async def api_notes(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    q = request.query_params.get("q")
    notes = await db.get_notes(search=q)
    return notes

@app.post("/notes/add")
async def notes_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    title = (form.get("title") or "").strip()
    body = (form.get("body") or "").strip()
    tags = (form.get("tags") or "").strip()
    if not title and not body:
        return RedirectResponse(url="/", status_code=303)
    if not title:
        title = body[:30]
    nid = await db.add_note(title, body, tags)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True,"id":nid})
    return RedirectResponse(url="/", status_code=303)

@app.post("/notes/delete/{note_id}")
async def notes_delete(request: Request, note_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.delete_note(note_id)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

# ─── Habits & Expenses ───
@app.get("/api/habits")
async def api_habits(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    return await db.get_habits()

@app.post("/habits/add")
async def habits_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return RedirectResponse(url="/", status_code=303)
    hid = await db.add_habit(name)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True,"id":hid})
    return RedirectResponse(url="/", status_code=303)

@app.post("/habits/done/{habit_id}")
async def habits_done(request: Request, habit_id: int):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    await db.mark_habit_done(habit_id)
    return JSONResponse({"ok":True})

@app.post("/habits/delete/{habit_id}")
async def habits_delete(request: Request, habit_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.delete_habit(habit_id)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/expenses")
async def api_expenses(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    return await db.get_expenses()

@app.post("/expenses/add")
async def expenses_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    try:
        amount = float(form.get("amount") or 0)
    except:
        amount = 0
    if amount <=0:
        return RedirectResponse(url="/", status_code=303)
    cat = (form.get("category") or "other").strip()
    comment = (form.get("comment") or "").strip()
    exp_date = (form.get("exp_date") or "").strip() or datetime.now(utils.tz).strftime("%Y-%m-%d")
    eid = await db.add_expense(amount, cat, comment, exp_date)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True,"id":eid})
    return RedirectResponse(url="/", status_code=303)

@app.post("/expenses/delete/{exp_id}")
async def expenses_delete(request: Request, exp_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.delete_expense(exp_id)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

# ─── Watchers (BY prices) ───
@app.get("/api/watchers")
async def api_watchers(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    w = await db.get_watchers()
    for it in w:
        it["urls"] = await db.get_watcher_urls(it["id"])
    return w

@app.post("/watchers/add")
async def watchers_add(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    form = await request.form()
    title = (form.get("title") or "").strip()
    region = (form.get("region") or "minsk").strip()
    try:
        target = float(form.get("target_price") or 0) or None
    except:
        target = None
    interval = int(form.get("check_interval", 3600) or 3600)
    if not title:
        return RedirectResponse(url="/", status_code=303)
    wid = await db.add_watcher(title, region, target, interval)
    # urls: expect store_url_1, store_url_2 etc or single url+store
    # support form fields: store, url
    store = (form.get("store") or "").strip()
    url = (form.get("url") or "").strip()
    if url:
        # auto-detect store from url if not provided
        if not store:
            if "21vek" in url: store="21vek"
            elif "5element" in url: store="5element"
            elif "evroopt" in url or "e-dostavka" in url: store="evroopt"
            elif "kopeechka" in url: store="kopeechka"
            elif "groshyk" in url: store="groshyk"
            elif "oz.by" in url: store="oz"
            else: store="other"
        await db.add_watcher_url(wid, store, url)
    # alternative: multiple urls via json field urls_json
    urls_json = form.get("urls_json")
    if urls_json:
        try:
            import json as _j
            arr = _j.loads(urls_json)
            for u in arr:
                await db.add_watcher_url(wid, u.get("store","other"), u.get("url",""), u.get("selector",""))
        except Exception:
            pass
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True,"id":wid})
    return RedirectResponse(url="/", status_code=303)

@app.post("/watchers/delete/{wid}")
async def watchers_delete(request: Request, wid: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)
    await db.delete_watcher(wid)
    if request.headers.get("accept","").find("json")>=0:
        return JSONResponse({"ok":True})
    return RedirectResponse(url="/", status_code=303)

@app.post("/watcher_urls/add")
async def watcher_urls_add(request: Request):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    data = await request.json()
    wid = int(data.get("watcher_id") or 0)
    store = data.get("store") or "other"
    url = data.get("url") or ""
    if not wid or not url:
        return JSONResponse(status_code=400, content={"error":"missing"})
    nid = await db.add_watcher_url(wid, store, url, data.get("selector",""))
    return JSONResponse({"ok":True,"id":nid})

@app.get("/api/watchers/preview")
async def watchers_preview(request: Request, url: str = ""):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    if not url:
        return JSONResponse({"ok":False,"error":"no url"})
    # simple fetch and try to extract price (without region for now)
    try:
        import httpx, re as _re
        from bs4 import BeautifulSoup
        headers = {"User-Agent":"Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            html = r.text[:20000]
            soup = BeautifulSoup(html, "lxml")
            # try common selectors
            price = None
            for sel in ['[data-price]', '.price__value', '.price', 'meta[property=\"product:price:amount\"]', '[itemprop=\"price\"]']:
                el = soup.select_one(sel)
                if el:
                    txt = el.get("content") or el.get_text()
                    m = _re.search(r'(\d+[\.,]\d+|\d+)', txt.replace(" ",""))
                    if m:
                        price = float(m.group(1).replace(",","."))
                        break
            if not price:
                # fallback regex
                m = _re.search(r'(\d+[\.,]\d+)\s*(?:р|BYN|руб)', html)
                if m:
                    price = float(m.group(1).replace(",","."))
            return JSONResponse({"ok": bool(price), "price": price, "title": soup.title.string[:80] if soup.title else ""})
    except Exception as e:
        return JSONResponse({"ok":False,"error":str(e)})

@app.get("/api/watchers/history/{url_id}")
async def watchers_history(request: Request, url_id: int):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    hist = await db.get_price_history(url_id, limit=50)
    return hist

@app.post("/watchers/check/{wid}")
async def watchers_check(request: Request, wid: int):
    if not check_auth(request):
        return JSONResponse(status_code=403, content={})
    # trigger manual check via same logic as scheduler (fetch each url)
    urls = await db.get_watcher_urls(wid)
    import httpx, re as _re
    from bs4 import BeautifulSoup
    w = await db.get_watcher_by_id(wid)
    region = w.get("region") if w else "minsk"
    checked = 0
    for u in urls:
        try:
            headers = {"User-Agent":"Mozilla/5.0"}
            # region cookie example for 21vek
            cookies = {}
            if "21vek" in u["url"] and region != "minsk":
                cookies["city"] = region
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, cookies=cookies) as client:
                r = await client.get(u["url"], headers=headers)
                soup = BeautifulSoup(r.text[:20000], "lxml")
                price = None
                for sel in [u.get("selector") or '', '[data-price]', '.price__value', '.price', 'meta[property=\"product:price:amount\"]']:
                    if not sel:
                        continue
                    el = soup.select_one(sel)
                    if el:
                        txt = el.get("content") or el.get_text()
                        m = _re.search(r'(\d+[\.,]\d+|\d+)', txt.replace(" ",""))
                        if m:
                            price = float(m.group(1).replace(",","."))
                            break
                if price:
                    await db.update_watcher_url_price(u["id"], price, "ok")
                    checked += 1
                else:
                    await db.update_watcher_url_price(u["id"], 0, "not_found")
        except Exception as e:
            try:
                await db.update_watcher_url_price(u["id"], 0, f"error:{e}"[:50])
            except:
                pass
    return JSONResponse({"ok":True,"checked":checked})

@app.post("/reminders/delete/{reminder_id}")
async def delete_reminder(request: Request, reminder_id: int):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)

    try:
        _scheduler.remove_job(f"reminder_{reminder_id}")
    except Exception:
        pass

    await db.delete_reminder(reminder_id)
    return RedirectResponse(url="/", status_code=303)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request):
    """Liveness probe для UptimeRobot / Koyeb / Render — не требует авторизации."""
    from fastapi.responses import JSONResponse
    if request.method == "HEAD":
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ok", "scheduler_running": bool(_scheduler and _scheduler.running), "commit": GIT_COMMIT})


@app.get("/version")
async def version():
    return JSONResponse({"commit": GIT_COMMIT, "status": "ok"})


@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping(request: Request):
    """То же что /health, короткое имя для внешних пингеров."""
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ok"})


@app.post("/reminders/deleteall")
async def delete_all_reminders(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)

    reminders = await db.get_active_reminders()
    for r in reminders:
        try:
            _scheduler.remove_job(f"reminder_{r['id']}")
        except Exception:
            pass

    await db.delete_all_reminders()
    return RedirectResponse(url="/", status_code=303)
