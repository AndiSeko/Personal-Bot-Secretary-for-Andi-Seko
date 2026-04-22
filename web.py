import os
import json
import hmac
import hashlib
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot

import db
import config

from datetime import datetime, timedelta

import pytz

tz = pytz.timezone(config.TIMEZONE)

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
    import json
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
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

    active_count = len(reminders)
    cyclic_count = sum(1 for r in reminders if r['is_cyclic'])
    msg_count = len(messages)

    for r in reminders:
        dt = datetime.strptime(r['remind_at'], "%Y-%m-%d %H:%M:%S")
        r['remind_at_fmt'] = dt.strftime("%d.%m.%Y %H:%M")
        if r['is_cyclic'] and r['interval_seconds']:
            r['interval_fmt'] = format_interval(r['interval_seconds'])
        else:
            r['interval_fmt'] = ""

    for m in messages:
        dt = datetime.strptime(m['created_at'], "%Y-%m-%d %H:%M:%S")
        m['created_at_fmt'] = dt.strftime("%d.%m.%Y %H:%M")

    return templates.TemplateResponse("index.html", {
        "request": request,
        "reminders": reminders,
        "messages": messages,
        "active_count": active_count,
        "cyclic_count": cyclic_count,
        "msg_count": msg_count,
        "owner_username": config.OWNER_USERNAME,
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
async def add_reminder(request: Request, time_str: str = Form(...), text: str = Form(...), is_cyclic: str = Form("off")):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=303)

    now = datetime.now(tz)

    if is_cyclic == "on":
        try:
            rel_time = parse_relative_time(time_str)
            interval_seconds = int((rel_time - now).total_seconds())
        except Exception:
            interval_seconds = 3600
        if interval_seconds < 60:
            interval_seconds = 60
        remind_at = now + __import__("datetime").timedelta(seconds=interval_seconds)
        remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await db.add_reminder(text, remind_at_str, is_cyclic=True, interval_seconds=interval_seconds)
    else:
        try:
            remind_at = parse_time(time_str)
        except ValueError:
            return RedirectResponse(url="/", status_code=303)
        if remind_at < now:
            return RedirectResponse(url="/", status_code=303)
        remind_at_str = remind_at.strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await db.add_reminder(text, remind_at_str)

    if bot_instance:
        schedule_reminder(reminder_id, remind_at, bot_instance)

    return RedirectResponse(url="/", status_code=303)


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
