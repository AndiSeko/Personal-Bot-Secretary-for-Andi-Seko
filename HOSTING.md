# Хостинг бота — где бесплатно, стабильно и с персистом (2026)

> **Итог для ленивых:** хочешь *абсолютно бесплатно без карты и чтобы не спал* — бери **Northflank Sandbox** (2 сервиса всегда включены) + внешний Postgres **Neon** (free).  
> Хочешь *максимально надёжно бесплатно навсегда* с картой — **Oracle Cloud Always Free** (ARM 2 OCPU / 12GB) + Docker, твой VPS навечно.  
> Хочешь *1 клик без возни* с лёгким сном — **Render Free** + UptimeRobot (пингует `/health` каждые 5 мин) + Neon/Supabase.

Код уже подготовлен ко всем трём: `DATABASE_URL` → Postgres, иначе SQLite в `/data`, `/health` и `/ping` для внешних пингеров.

---

## 1) Почему обычные бесплатные PaaS — проблема для твоего бота

Твой бот — это **long-polling** + `APScheduler` (напоминания). Ему нужен:
- **Always-on процесс** — иначе напоминания не сработают.
- **Персист диска/БД** — `secretary.db` не должен стираться при деплое.

Что случилось с бесплатными тарифами к 2026:

| Платформа | Бесплатный тариф сейчас | Спит? | Диск | Вывод |
|---|---|---|---|---|
| **Railway** | $5 trial → $1/мес, реально $10/мес за 512MB | Нет, но $ кончится за дни | Эфемерный | Не подходит как фри |
| **Fly.io** | Trial 2 часа, далее pay-as-you-go (~$2-5/мес) | Нет | Volume $0.15/GB | Платно для новых акков |
| **Render Free** | 750 часов/мес есть | **Да, 15 мин idle** | Эфемерный | Только с пингером + внешняя БД |
| **Koyeb** | С 02.2026: free только на платных планах ($29/мес) | Да (scale-to-zero) | Эфемерный | Закрыт для новых |
| **Northflank** | **Sandbox — 2 сервиса + 1 БД — Always-on, no sleeping** | **Нет** | Есть | ✅ **Рекомендуется как фри** |
| **Oracle Cloud** | Always Free: 2 OCPU 12GB ARM + 2 micro VMs навсегда | **Нет** | 200GB | ✅ **Самый стабильный фри** |
| **Cloudflare Workers** | 100k req/day | Нет | Нет long-polling | Не подходит (serverless) |

Поэтому делаем так: **внешняя БД** (Neon/Supabase/Turso) → данные живут даже если контейнер пересоздали, + пингер не даёт уснуть на Render.

---

## 2) Подготовка: внешняя БД (1 раз, 3 минуты)

Без этого на Render/Koyeb и любом эфемерном хосте данные пропадут после рестарта.

### Вариант A — Neon (рекомендуется, Postgres)
1. https://neon.tech → Sign up → Create Project (Free: 3 проекта, 0.5GB)
2. Скопируй `DATABASE_URL`: `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`
3. Вставь в переменные окружения хоста как `DATABASE_URL`

### Вариант B — Supabase
1. https://supabase.com → New Project → Database → Connection string → `DATABASE_URL`
2. Free: проект ставится на паузу после 1 недели без активности — добавь пингер на БД или используй Neon.

### Вариант C — без внешней БД (volume)
Если хост даёт persistent volume (Fly.io, Northflank, Oracle), можно оставить `DATABASE_URL` пустым — будет `DATA_DIR=/data/secretary.db` на volume.

> Код сам определяет: если `DATABASE_URL` начинается с `postgres://` → `asyncpg`, иначе `aiosqlite`.

---

## 3) Рекомендуемый хост #1 — Northflank (бесплатно, не спит, без сюрпризов)

- https://northflank.com → Sign up (проверка карты без списания)
- New Project → Service → From GitHub → выбери репо
- Build: Dockerfile, Port 8000, Health check `/health`
- Env: `BOT_TOKEN`, `OWNER_USERNAME`, `OWNER_ID`, `WEB_PASSWORD`, `GROQ_API_KEY`, `DATABASE_URL` (Neon)
- Add Database → Postgres (free) — можно и без Neon, тогда оставь `DATABASE_URL` пустым и используй внутренний
- Deploy. Сервис **не спит**, 2 сервиса бесплатно.

## 4) Рекомендуемый хост #2 — Render + UptimeRobot (бесплатно без карты)

Подходит если не хочешь вводить карту вообще.

1. Подключи GitHub на https://dashboard.render.com → New → Blueprint → выбери репо (подхватит `render.yaml`)
   - Или вручную: New → Web Service → Docker → Port 8000 → Health `/health`
2. Задай env: `BOT_TOKEN`, `OWNER_USERNAME`, `OWNER_ID`, `WEB_PASSWORD`, `GROQ_API_KEY`, `DATABASE_URL` (из Neon)
   - `PORT` на Render ставится автоматически (10000) — код читает `PORT`
3. Deploy. Подожди пока появится URL вида `https://xxx.onrender.com`.
4. **Анти-сон:** зайди на https://uptimerobot.com (бесплатно) → Add Monitor → Type HTTP(s) → URL `https://xxx.onrender.com/health` → Interval 5 minutes. Это держит контейнер проснувшимся.
   - **Важно:** Render free всё равно имеет лимит 750 часов/мес — пингер его съест полностью, но это ОК (750ч = 31 день). Хватает на месяц.
   - Free Postgres Render умирает через 30 дней — поэтому используй Neon, а не `fromDatabase`.

## 5) Рекомендуемый хост #3 — Oracle Cloud Always Free (вечный VPS)

Самый надёжный бесплатный вариант — твой личный VPS навсегда.

1. https://cloud.oracle.com → Create Account (требует карту, $0)
2. Create Instance → Image Ubuntu 22.04 → Shape Ampere A1 (ARM 2 OCPU 12GB) — Always Free
3. Открой порт 8000 в Security List (Ingress 0.0.0.0/0 TCP 8000)
4. На инстансе:
```bash
sudo apt update && sudo apt install -y docker.io docker-compose
git clone https://github.com/<you>/Personal-Bot-Secretary-for-Andi-Seko.git
cd Personal-Bot-Secretary-for-Andi-Seko
cp .env.example .env
nano .env  # заполни BOT_TOKEN и т.д., DATABASE_URL можно оставить пустым
docker compose up -d
docker logs -f secretary-bot
```
Данные в Docker volume `secretary_data` — переживут перезапуск. Рекомендуется поставить автообновление через Watchtower или `git pull && docker compose up -d --build`.

## 6) Fly.io (если есть карта, $5/мес минимум)

```bash
npm i -g flyctl
fly auth login
fly volumes create secretary_data --region waw --size 1
fly secrets set BOT_TOKEN=... OWNER_ID=... WEB_PASSWORD=... GROQ_API_KEY=... DATABASE_URL=...
fly deploy
fly logs
```
`fly.toml` уже настроен: `auto_stop_machines = false`, `min_machines_running = 1`, volume `/data`.

## 7) Переменные окружения (все хосты)

| Переменная | Обязательно | Пример |
|---|---|---|
| `BOT_TOKEN` | Да | `123456:ABC...` |
| `OWNER_USERNAME` | Да | `andi_seko` |
| `OWNER_ID` | Да | `123456789` |
| `WEB_URL` | Для кнопки в боте | `https://xxx.onrender.com` |
| `WEB_PASSWORD` | Да | `secretary` |
| `GROQ_API_KEY` | Нет | `gsk_...` |
| `DATABASE_URL` | **Да для Render/эфимеров** | `postgresql://...?sslmode=require` |
| `DATA_DIR` | Нет | `/data` (Docker по умолчанию) |
| `PORT` | Авто | `8000` / `10000` |

## 8) Как проверить что персист работает

1. Создай напоминание: `/remind 10m тест персиста`
2. Сделай Redeploy на хосте (Render: Manual Deploy, Northflank: Redeploy, Oracle: `docker compose restart`)
3. Снова `/list` — напоминание должно остаться. Если пропало — `DATABASE_URL` не подключён и используется эфемерный SQLite.

Health: `curl https://your-app/health` → `{"status":"ok"}`

## 9) Что уже исправлено в коде под фри-хостинг

- `config.py` — починен баг `OWNER_ID` (раньше перезатирался в `None`), добавлены `DATABASE_URL`, `TURSO_*`.
- `db.py` — двойной бэкенд: Postgres (`asyncpg`) если есть `DATABASE_URL`, иначе SQLite. Миграции и `ON CONFLICT` для Postgres.
- `web.py` — добавлены `/health` и `/ping` для UptimeRobot/Koyeb/Render, исправлен импорт `timedelta`.
- `Dockerfile` — `DATA_DIR=/data` + `HEALTHCHECK`.
- `requirements.txt` — добавлен `asyncpg`.
- `render.yaml`, `fly.toml`, `docker-compose.yml`, `.dockerignore` — готовы к деплою.

---

**Выбор автора:** для твоих требований *«бесплатно + стабильно + не теряет данные + напоминания»* бери **Northflank + Neon**. Один раз настроил — забыл. Если хочешь совсем без внешних сервисов — Oracle Cloud VPS.
