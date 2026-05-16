# FinanceBlackHole 🕳️

AI-powered Telegram-бот для осознанного управления личными финансами (бот + FastAPI + Mini App).

## Требования

- **Docker Desktop** (Windows/macOS) или **Docker Engine + Compose plugin** (Linux)
- Аккаунты и ключи: **Telegram** (@BotFather), **OpenAI** API

---

## Быстрый старт (Docker)

Рабочая папка проекта на обеих ОС: `financeblackhole/` (где лежат `docker-compose.yml` и `.env`).

### Linux

```bash
cd financeblackhole
cp .env.example .env
# отредактируй .env — см. раздел «Переменные окружения» ниже

docker compose up --build
```

### Windows (PowerShell или CMD)

```powershell
cd financeblackhole
copy .env.example .env
# отредактируй .env в блокноте / IDE

docker compose up --build
```

Остановка: `Ctrl+C` или в другом терминале `docker compose down`.

### Что поднимается

| Сервис    | Назначение                          | Порт на хосте |
| --------- | ----------------------------------- | ------------- |
| postgres  | PostgreSQL 16                       | 5432          |
| redis     | кэш, FSM, Celery                    | 6379          |
| api       | FastAPI + миграции Alembic          | **8000**      |
| bot       | aiogram, long polling              | —             |
| worker    | Celery worker                       | —             |
| beat      | Celery beat (расписание)            | —             |

Проверка API: открой в браузере `http://localhost:8000/health` — ответ `{"status":"ok",...}`.

Миграции применяются при старте контейнера `api`. Вручную:

```bash
docker compose exec api alembic upgrade head
```

---

## HTTPS и Mini App: зачем Cloudflare (или другой туннель)

Telegram открывает **кнопки Web App** и **Menu Button** только по **HTTPS**.  
Локальный `http://localhost:8000` из чата Telegram **не подойдёт**.

**Рекомендуется Cloudflare Tunnel** (`cloudflared`): бесплатный HTTPS без «заставки» ngrok, Mini App в WebView обычно ведёт себя стабильнее.

Альтернативы: свой домен + SSL на VPS, ngrok с платным/статическим доменом.

### Вариант A: Cloudflare Tunnel (Linux и Windows)

1. Установи **cloudflared**:
   - [Документация Cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) — есть пакеты для Linux и установщик для Windows.
2. Подними стек (API на **8000**):
   ```bash
   docker compose up --build
   ```
3. В **втором** терминале пробрось порт:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   На Windows то же самое в PowerShell/CMD, если `cloudflared` в `PATH`.
4. Скопируй выданный URL вида `https://something.trycloudflare.com` (**без** слэша на конце).
5. В `.env` на машине, где крутится Docker:
   ```env
   WEBHOOK_URL=https://something.trycloudflare.com
   WEBAPP_URL=https://something.trycloudflare.com
   ```
6. Перезапусти бота и API, чтобы подтянулись переменные и настроилась кнопка меню:
   ```bash
   docker compose up -d api bot
   ```

**Важно:** на бесплатном quick tunnel URL при каждом новом запуске `cloudflared` часто **меняется** — обновляй `WEBHOOK_URL` / `WEBAPP_URL` и снова `docker compose up -d api bot`.

Проверка: в обычном браузере открой `https://ВАШ-URL/dashboard` — в HTML должен быть `FinanceBlackHole` / страница загрузки Mini App.

### Вариант B: ngrok

```bash
ngrok http 8000
```

Подставь HTTPS URL в `WEBHOOK_URL` и `WEBAPP_URL` как выше. Учти предупреждающую страницу бесплатного ngrok — в Telegram WebView иногда «пустой» экран; тогда лучше Cloudflare или платный ngrok.

### BotFather и домен Mini App

Если BotFather просит привязать домен: **@BotFather** → твой бот → *Bot Settings* → *Configure Mini App* / *Domain* — укажи **хост без `https://`** (например `wolf-xxx.trycloudflare.com`).

---

## Переменные окружения (`.env`)

Скопируй из `.env.example` и заполни минимум:

| Переменная       | Описание |
| ---------------- | -------- |
| `BOT_TOKEN`      | от @BotFather |
| `OPENAI_API_KEY` | OpenAI |
| `WEBHOOK_SECRET` | случайная строка (вебхуки, если используешь) |
| `JWT_SECRET`     | случайная строка для JWT дашборда |
| `WEBHOOK_URL`    | публичный **HTTPS** базовый URL (туннель или домен) |
| `WEBAPP_URL`     | обычно **тот же** URL, что и `WEBHOOK_URL` |

Внутри Docker уже заданы:

```env
DATABASE_URL=postgresql+asyncpg://fbh:fbhpass@postgres:5432/financeblackhole
REDIS_URL=redis://redis:6379/0
```

---

## Локальная разработка (без Docker для Python)

Нужны Python 3.12+, PostgreSQL 16, Redis 7. В `.env` укажи `localhost` вместо имён сервисов (см. комментарии в `.env.example`).

```bash
pip install -r requirements.txt
alembic upgrade head
```

В отдельных терминалах:

```bash
python -m bot.main
uvicorn api.main:app --reload --port 8000
celery -A tasks.celery_app worker --loglevel=info
celery -A tasks.celery_app beat --loglevel=info
```

Для Mini App с локального API всё равно нужен **HTTPS-туннель** на порт 8000.

---

## Структура проекта

```
financeblackhole/
├── bot/           # Telegram-бот (aiogram 3)
├── api/           # FastAPI (REST + SSR веб-дашборд)
├── web/           # шаблоны Jinja2, статика
├── core/          # config, database, redis, auth
├── models/        # SQLAlchemy
├── services/      # AI, аналитика, траты, геймификация
├── tasks/         # Celery
└── alembic/       # миграции
```

---

## Команды бота (кратко)

| Команда | Описание |
| ------- | -------- |
| `/start` | Регистрация / возврат |
| `/help` | Список команд и кнопок |
| `/goal`, `/goals` | Цели |
| `/save N` | Накопление к цели |
| `/missions` | Миссии |
| `/today`, `/week` | Статистика |
| `/insight` | AI «зеркало» за 7 дней |
| `/categories` | Свои категории |
| `/budget` | Месячный лимит |
| `/profile`, `/dashboard` | Профиль и ссылки на Mini App |

Любое текстовое сообщение (не команда) — разбор как трата; голос и фото чека — тоже поддерживаются.

## Web Dashboard (Mini App)

Страницы: `/dashboard`, `/goals`, `/history`, `/profile` — с `?token=` из бота или через bootstrap с `initData` внутри Telegram.

В **Истории** в вебе: редактирование и удаление трат (JWT в query).

---

## Milestones

- **M1** — Бот + «чёрная дыра» + геймификация  
- **M2** — Цели, миссии, накопления, бюджет  
- **M3** — Личность, инсайты, API  
- **M4** — Веб-дашборд + Telegram Mini App  
