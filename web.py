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
        return templates.TemplateResponse("login.html", {"request": request, "error": False})

    reminders = await db.get_all_reminders()
    messages = await db.get_messages(limit=50)
    calendar_events = await db.get_all_calendar_events()

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

    # theme settings
    theme_json = await db.get_setting("theme")
    theme = None
    if theme_json:
        try:
            theme = _json.loads(theme_json)
        except:
            theme = None

    return templates.TemplateResponse("index.html", {
        "request": request,
        "reminders": reminders,
        "messages": messages,
        "calendar_events": calendar_events,
        "calendar_events_json": _json.dumps(calendar_events, ensure_ascii=False),
        "reminders_json": _json.dumps(reminders, ensure_ascii=False),
        "active_count": active_count,
        "cyclic_count": cyclic_count,
        "msg_count": msg_count,
        "calendar_count": len(calendar_events),
        "owner_username": config.OWNER_USERNAME,
        "owner_id": config.OWNER_ID or 0,
        "known_users": await db.get_all_known_users(),
        "theme_json": _json.dumps(theme) if theme else "null",
    })


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == WEB_PASSWORD:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("session", WEB_PASSWORD, max_age=86400 * 30)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": True})


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
    return JSONResponse({"status": "ok", "scheduler_running": bool(_scheduler and _scheduler.running)})


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
