# GAMING CONTENT FACTORY — ПЛАН РЕАЛИЗАЦИИ ДЛЯ ИСПОЛНЯЮЩЕГО АГЕНТА

> Этот документ — полное техническое задание для агентской системы (Claude Code или аналог).
> Задача: реализовать систему автоматизированного производства YouTube-контента целиком,
> от пустого репозитория до автономной работы. Все регистрации и оплаты сервисов выполняет
> владелец — агент получает готовые ключи через `.env`.

---

## 0. ЦЕЛЬ И КОНТЕКСТ

Автоматизированный YouTube-канал (язык контента: английский), ниша: PC gaming (трафик) + desk setup / периферия (монетизация через аффилиатные ссылки).

Нагрузка в steady-state: **2 длинных видео (8–12 мин) + 7 шортсов в неделю** + дистрибуционный контур: **блог-статья на каждое длинное видео, 3–5 пинов Pinterest в неделю, посты в Threads (опционально, флаг)**.

Участие владельца: **одна сессия в воскресенье, 30–60 минут, только через Telegram** — одобрение тем и готовых видео. Всё остальное система делает сама.

Результат работы агента: задеплоенная на VPS система, которая прошла «сертификацию автономности» — 7 дней работы без единого вмешательства, кроме воскресной сессии.

---

## 1. ГРАНИЦЫ ОТВЕТСТВЕННОСТИ

### Делает владелец (НЕ входит в задачу агента)
- Регистрация всех сервисов и оплата тарифов
- Создание YouTube-канала, Google Cloud проекта, подача заявки на YouTube API audit
- Выбор и фиксация voice ID в ElevenLabs
- Запись/закупка геймплейного футажа (агент строит инструменты загрузки, но не контент)
- Заполнение `.env` реальными ключами

### Делает агент (входит в задачу)
- Весь код, инфраструктура-как-код, схема БД, миграции
- Деплой на VPS (доступ по SSH из `.env`)
- Все воркеры пайплайна, Telegram-бот, мониторинг
- Тесты, dry-run прогоны, документация (runbook)
- CLI-инструменты для владельца (загрузка футажа, управление)

### ENV-переменные, которые предоставит владелец

```env
# Инфраструктура
SSH_HOST=               # Hetzner VPS (Ubuntu 24.04, CX22+)
SSH_USER=
SSH_PRIVATE_KEY_PATH=

# База данных
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
SUPABASE_DB_URL=        # прямой Postgres connection string

# Хранилище
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_FOOTAGE=
R2_BUCKET_RENDERS=

# AI / генерация
ANTHROPIC_API_KEY=      # Claude Sonnet — скрипты, скоринг, SEO
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=    # зафиксирован владельцем, НЕ МЕНЯТЬ
FAL_API_KEY=            # Flux — превьюшки

# Сборка видео
CREATOMATE_API_KEY=

# YouTube
YT_CLIENT_ID=
YT_CLIENT_SECRET=
YT_REFRESH_TOKEN=       # агент пишет helper-скрипт для получения

# Источники данных
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=

# Интерфейс
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_CHAT_ID=

# Мониторинг
HEALTHCHECKS_PING_URL=

# Дистрибуция: блог (Cloudflare Pages)
CLOUDFLARE_API_TOKEN=     # права: Pages edit
CLOUDFLARE_ACCOUNT_ID=
BLOG_DOMAIN=              # например, rigreports.com — домен покупает владелец

# Дистрибуция: Pinterest (API v5; владелец проходит app review у Pinterest)
PINTEREST_ACCESS_TOKEN=
PINTEREST_BOARD_ID_GAMING=
PINTEREST_BOARD_ID_SETUP=

# Дистрибуция: Threads (Meta; long-lived token, авто-рефреш каждые ~50 дней)
THREADS_USER_ID=
THREADS_ACCESS_TOKEN=
```

Если переменная отсутствует на момент выполнения зависимой задачи — агент **останавливается и запрашивает её**, не имитирует и не мокает в production-коде.

---

## 2. АРХИТЕКТУРНЫЕ КОНСТАНТЫ (НАРУШАТЬ ЗАПРЕЩЕНО)

1. **Единый конечный автомат в Postgres — единственный источник правды.** Воркеры общаются ТОЛЬКО через смену статусов записей. Никаких прямых вызовов воркер→воркер, никаких очередей сообщений сверх этого.
2. **Человеческий гейт перед публикацией.** Ни одно видео не уходит в public без явного Approve в Telegram. Автопилот-публикация разрешена только для шортсов из заранее одобренного бэклога — и эта опция включается флагом конфига, по умолчанию выключена.
3. **Фиксированная идентичность канала.** Один voice ID, один набор Creatomate-шаблонов. Источник футажа жёстко привязан к формату:
   - `long_gaming` → собственный банк (`footage` table, наполняется `python -m src.cli.ingest_footage`)
   - `short` и `long_setup` → AI-генерация по `scene_description` через fal.ai/kling-video (см. §6.3a)
   - `bridge` → собственный банк (как `long_gaming`)

   Подмена голоса, кросс-форматное смешение источников или приём внешних видео-URL по-прежнему запрещены.
4. **Запрет чужого футажа.** Для `long_gaming`/`bridge` Assembler принимает клипы только из таблицы `footage`. Для `short`/`long_setup` Assembler принимает клипы только из `generated/<video_id>/<idx>.mp4` в R2_BUCKET_RENDERS, созданные `footage_generator`'ом для этого же video_id. Произвольные внешние URL запрещены везде.

> ✅ AL-001 ACCEPTED 2026-06-12 — переписаны §3 и §4 раздела 2. История запроса
> и обоснование решений в `docs/ARCHITECTURE_LOG.md`.

---

## 3. ТЕХНИЧЕСКИЙ СТЕК (БЕЗ АЛЬТЕРНАТИВ)

| Слой | Выбор | Обоснование |
|---|---|---|
| Язык | Python 3.12 | тестируемость, зрелые SDK всех нужных API |
| Структура | Монорепо, один Docker Compose | один VPS, простой деплой |
| Оркестрация | APScheduler (cron-триггеры) + polling-цикл по статусам | детерминизм, нет лишней инфраструктуры |
| БД | Supabase Postgres | состояние + бесплатный data browser = «окно» для владельца |
| Файлы | Cloudflare R2 (boto3, S3-совместимый) | футаж, рендеры, превью |
| Telegram | aiogram 3.x | единственный интерфейс владельца |
| LLM | Anthropic API, model `claude-sonnet-4-5` | скрипты, скоринг, SEO, факт-чек |
| Озвучка | ElevenLabs API | фиксированный voice ID |
| Сборка видео | Creatomate API (JSON-шаблоны) | рендер без своего GPU |
| Превью | fal.ai (Flux) + Creatomate image-шаблон | текст поверх арта |
| Публикация | YouTube Data API v3 + Analytics API | официальный путь |
| Мониторинг | healthchecks.io пинги + Telegram-алерты | внешний надзор |
| Блог | Astro (static) + Cloudflare Pages, деплой через wrangler | $0 хостинг, git-native — идеально для агента |
| Pinterest | Pinterest API v5 (пины из image-шаблонов Creatomate) | органика в нише battlestations |
| Threads | Threads API (Meta), флаг `threads_enabled`, по умолчанию OFF | дёшево в коде, низкий ROI — включается по желанию |
| Тесты | pytest, respx/responses для моков HTTP | CI-пригодность |
| Деплой | Docker Compose + systemd, GitHub Actions опционально | перезапуск при падении |

**Важно: оркестрация — код, не n8n.** Исходный план предполагал n8n для сборки человеком. Исполнитель — агент, поэтому код: версионируется, тестируется, отлаживается. Это осознанная ревизия.

---

## 4. СТРУКТУРА РЕПОЗИТОРИЯ

```
content-factory/
├── docker-compose.yml
├── .env.example
├── Makefile                  # make deploy, make test, make dry-run
├── migrations/               # SQL-миграции, нумерованные
├── src/
│   ├── core/
│   │   ├── db.py             # подключение, репозитории
│   │   ├── state_machine.py  # переходы статусов, валидация
│   │   ├── config.py
│   │   ├── retry.py          # backoff, dead-letter
│   │   └── llm.py            # обёртка Anthropic с логированием токенов
│   ├── workers/
│   │   ├── trend_scout.py
│   │   ├── topic_strategist.py
│   │   ├── scriptwriter.py
│   │   ├── voice.py
│   │   ├── assembler.py
│   │   ├── thumbnail.py
│   │   ├── seo_packager.py
│   │   ├── publisher.py
│   │   ├── analyst.py
│   │   ├── watchdog.py
│   │   ├── repurposer.py         # published → контент для блога/пинов/threads
│   │   ├── blog_publisher.py
│   │   ├── pinterest_publisher.py
│   │   └── threads_publisher.py
│   ├── bot/                  # aiogram: команды, кнопки, дайджест
│   ├── scheduler.py          # APScheduler: расписание всех воркеров
│   └── cli/
│       ├── ingest_footage.py # загрузка и тегирование футажа
│       ├── yt_auth.py        # получение refresh token
│       └── seed_products.py  # импорт products из CSV
├── blog/                     # Astro-проект: layout, компоненты, content/posts/*.md
├── templates/
│   ├── creatomate/           # JSON: short_v1, long_v1, setup_v1, thumb_v1, pin_v1
│   └── prompts/              # все LLM-промпты как .md файлы
├── tests/
└── docs/
    └── RUNBOOK.md            # «что делать если…» на 1 страницу
```

---

## 5. СХЕМА ДАННЫХ (SQL DDL — применить миграциями)

```sql
-- Статусы пайплайна
CREATE TYPE video_status AS ENUM (
  'raw',               -- тема найдена Trend Scout
  'scored',            -- оценена Topic Strategist
  'pending_approval',  -- ждёт решения владельца (тема)
  'approved',          -- тема одобрена
  'scripted',          -- скрипт готов и прошёл факт-чек
  'voiced',            -- озвучка готова, файл в R2
  'assembled',         -- видео срендерено
  'packaged',          -- превью + SEO готовы
  'ready_for_review',  -- ждёт Approve видео владельцем
  'publish_queued',    -- одобрено, ждёт слота
  'published',         -- загружено на YouTube
  'analyzed',          -- метрики сняты
  'rejected',          -- отклонено владельцем
  'failed'             -- ошибка, требует внимания
);

CREATE TYPE video_format AS ENUM ('short', 'long_gaming', 'long_setup', 'bridge');

CREATE TABLE topics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source text NOT NULL,             -- rss:<feed> | reddit:<sub>
  title text NOT NULL,
  url text,
  raw_payload jsonb,
  score numeric,                    -- 0-100 от Strategist
  score_reasoning text,
  suggested_format video_format,
  dedup_hash text UNIQUE,           -- защита от повторов
  created_at timestamptz DEFAULT now()
);

CREATE TABLE videos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id uuid REFERENCES topics(id),
  format video_format NOT NULL,
  status video_status NOT NULL DEFAULT 'raw',
  title text,
  script jsonb,                     -- {hook, sections[], cta, facts_checked}
  voice_r2_key text,
  render_r2_key text,
  thumbnail_r2_key text,
  seo jsonb,                        -- {title, description, tags[], chapters[], pinned_comment}
  youtube_video_id text,
  scheduled_publish_at timestamptz,
  status_updated_at timestamptz DEFAULT now(),
  attempts int DEFAULT 0,
  last_error text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE status_log (
  id bigserial PRIMARY KEY,
  video_id uuid REFERENCES videos(id),
  from_status video_status,
  to_status video_status,
  actor text NOT NULL,              -- имя воркера или 'owner'
  detail text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE footage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  r2_key text NOT NULL UNIQUE,
  game text NOT NULL,
  tags text[] NOT NULL,             -- ['combat','menu','victory','scenic']
  duration_sec numeric NOT NULL,
  resolution text NOT NULL,
  used_count int DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  category text NOT NULL,           -- mouse|keyboard|monitor|chair|...
  affiliate_url text NOT NULL,
  program text NOT NULL,            -- amazon|impact:<brand>|direct:<brand>
  commission_pct numeric,
  price_usd numeric,
  active boolean DEFAULT true,
  last_link_check timestamptz,
  link_ok boolean DEFAULT true
);

CREATE TABLE analytics (
  id bigserial PRIMARY KEY,
  video_id uuid REFERENCES videos(id),
  snapshot_at timestamptz DEFAULT now(),
  views bigint, impressions bigint, ctr numeric,
  avg_view_duration_sec numeric, retention_pct numeric,
  subs_gained int, estimated_revenue numeric
);

CREATE TABLE format_weights (
  format video_format PRIMARY KEY,
  weight numeric NOT NULL DEFAULT 1.0,
  killed boolean DEFAULT false,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE errors (
  id bigserial PRIMARY KEY,
  worker text NOT NULL,
  video_id uuid,
  error text NOT NULL,
  payload jsonb,
  resolved boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

-- Дистрибуционный контур: fan-out после публикации видео.
-- ОТДЕЛЬНЫЙ автомат — сбой дистрибуции никогда не блокирует видео-пайплайн.
CREATE TYPE dist_channel AS ENUM ('blog', 'pinterest', 'threads');
CREATE TYPE dist_status AS ENUM ('pending', 'content_ready', 'posted', 'failed', 'skipped');

CREATE TABLE distributions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid REFERENCES videos(id) NOT NULL,
  channel dist_channel NOT NULL,
  status dist_status NOT NULL DEFAULT 'pending',
  content jsonb,                    -- blog: {slug, title, markdown}; pin: {image_r2_key, title, description, board, link}; threads: {text, image_r2_key}
  external_url text,                -- URL опубликованного поста/пина/статьи
  attempts int DEFAULT 0,
  last_error text,
  scheduled_at timestamptz,
  posted_at timestamptz,
  UNIQUE (video_id, channel)
);
```

**Правила конечного автомата** (реализовать в `state_machine.py`, единственная точка смены статуса):
- Разрешённые переходы — строго по порядку enum + `* → failed`, `pending_approval → rejected`, `ready_for_review → rejected`
- Любая смена статуса пишет строку в `status_log`
- `failed` при `attempts >= 3` → запись в `errors` + Telegram-алерт (dead-letter)
- Воркер берёт работу запросом вида `SELECT ... WHERE status = '<входной>' FOR UPDATE SKIP LOCKED LIMIT 1`

---

## 6. СПЕЦИФИКАЦИИ ВОРКЕРОВ

Каждый воркер: вход = записи в определённом статусе, выход = следующий статус. Все сетевые вызовы через `retry.py` (3 попытки, экспоненциальный backoff 5s/25s/125s).

### 6.1 trend_scout — `cron: */12h`
- Источники: единый список RSS-фидов из `src/core/trend_sources.py` (gaming-news + Reddit-сабреддиты как публичные `*.reddit.com/r/X/.rss`). **AL-002 (2026-06-15)**: Reddit перешёл с PRAW (OAuth + client_id/secret) на анонимный RSS-парсинг — авторизация не нужна.
- HTTP через httpx с управляемым backoff: на Reddit 429 ретраи 5/15/30/60с (Reddit жёстко режет VPS-IP). Список фидов перемешивается случайно при каждом прогоне — выравниваем coverage поверх rate-limit'а.
- Источник в payload: `rss:<host>` для news, `reddit:<sub>` для сабов. Reddit-RSS не отдаёт upvote count, но `hot` лента самого Reddit'а уже отфильтрована до постов с приблизительно >100 upvotes (это передаётся в Strategist как source-based prior).
- Для каждого айтема: `dedup_hash = sha256(normalize(title))`, INSERT в `topics` с `ON CONFLICT DO NOTHING`.
- Не вызывает LLM. Чистый сбор.

### 6.2 topic_strategist — `cron: воскресенье 06:00 UTC`
- Берёт `topics` за 7 дней без score
- Один батч-вызов Claude: скоринг 0–100 по критериям (поисковый потенциал, соответствие нише, монетизируемость, наличие футажа под игру в банке — передать список игр из `footage`), формат-рекомендация с учётом `format_weights`
- Топ-10 → создаёт записи `videos(status='pending_approval')`, остальное остаётся в topics как архив
- Промпт: `templates/prompts/strategist.md`

### 6.3 scriptwriter — поллинг `status='approved'`
- Два LLM-прохода: (1) генерация скрипта по формату (структура: hook ≤15 сек, секции, CTA, маркеры footage-тегов вида `[FOOTAGE: combat]`), (2) факт-чек собственного текста — проверка дат/цифр/названий, флаги сомнительного
- Длинные форматы: скрипт включает товарные интеграции — Scriptwriter получает топ-5 релевантных позиций из `products`
- Выход: `script` jsonb → `scripted`

### 6.4 voice — поллинг `status='scripted'`
- ElevenLabs TTS, voice ID строго из ENV, модель `eleven_turbo_v2_5`
- Склейка секций, нормализация громкости (ffmpeg, -16 LUFS), загрузка mp3 в R2 → `voiced`
- Контроль квоты: остаток символов < 20% месячного лимита → Telegram-предупреждение

### 6.5 assembler — поллинг `status='voiced'`
- Подбор клипов из `footage` по `[FOOTAGE: тег]` маркерам + игре темы; инкремент `used_count`; приоритет наименее использованным
- Если подходящих клипов нет → статус `failed` с `last_error='footage_gap: <игра/тег>'` (алерт владельцу — это сигнал к сессии захвата)
- Creatomate: подстановка в шаблон (`short_v1` | `long_v1` | `setup_v1`): тайминги из длительности аудиосекций, субтитры из скрипта, фоновая музыка из R2-папки `music/` (лицензированная, заливает владелец)
- Рендер → mp4 в R2 → `assembled`

### 6.6 thumbnail — поллинг `status='assembled'`, только длинные форматы
- fal.ai Flux: фон по промпту из темы; Creatomate image-шаблон: текст-оверлей (≤4 слов, из SEO-заготовки)
- 2 варианта → R2, ключ варианта A в `thumbnail_r2_key`, B хранится рядом (`_b` суффикс) для YT Test & Compare
- Шортсы пропускают этот шаг (`assembled → packaged` через seo_packager)

### 6.7 seo_packager — поллинг `status='assembled'` (шортсы) / после thumbnail (длинные)
- Claude: title (≤60 симв.), description (главы по таймкодам секций, аффилиатные ссылки релевантных `products`, FTC-дисклеймер обязательной строкой), tags, pinned comment
- Валидация: все ссылки отвечают 200 → `packaged`, далее автоматически `ready_for_review` + пуш в Telegram с превью

### 6.8 publisher — поллинг `status='publish_queued'` + слоты расписания
- Расписание слотов в конфиге: шортсы пн/ср/пт/сб 17:00 UTC, длинные вт/пт 16:00 UTC
- YouTube Data API: загрузка с `privacyStatus='private'` + `publishAt=<слот>` (до прохождения API-аудита Google published-видео остаются private — Publisher шлёт владельцу напоминание в воскресный дайджест: «переключи N видео в public»; после аудита флаг конфига `api_audit_passed=true` включает полную автопубликацию)
- Установка thumbnail, пост pinned comment → `published`

### 6.9 analyst — `cron: ежедневно 05:00 UTC + воскресенье 05:30 (недельный свод)`
- YouTube Analytics API → снапшоты в `analytics` для видео < 30 дней
- Пересчёт `format_weights`: weight = нормализованная функция (CTR, retention, subs_gained) по формату
- **Kill-критерий:** 3 последних видео формата подряд CTR < 2.5% ИЛИ retention < 35% → `killed=true` + алерт; Strategist игнорирует killed-форматы
- Недельный свод → данные для дайджеста

### 6.10 watchdog — `cron: */30min`
- Проверки: (а) записи, висящие в промежуточном статусе > 6 часов; (б) `errors.resolved=false`; (в) квоты ElevenLabs/Creatomate; (г) `products` link-check раз в сутки (битая ссылка → `link_ok=false`, исключение из вставки, алерт); (д) пинг healthchecks.io (его отсутствие = внешний сигнал, что система мертва)
- Эскалация молчания владельца: 14 дней без действий в боте → предупреждение; 21 день → система в standby (воркеры с LLM/деньгами останавливаются, сбор тем продолжается)
- Дополнительно: рефреш long-lived токена Threads каждые 50 дней (если `threads_enabled`)

### 6.11 repurposer — поллинг `videos.status='published'` без строк в distributions
- При появлении published-видео создаёт строки `distributions`: блог — только для длинных форматов; Pinterest — для длинных и setup; Threads — если `threads_enabled=true`, иначе строка со статусом `skipped`
- Один батч-вызов Claude на видео генерирует `content` для всех каналов сразу:
  - **Блог:** статья 800–1200 слов — НЕ транскрипт, а переработка скрипта в самостоятельный материал (данные, сравнения, выводы) + embed видео + product-блоки с аффилиатными ссылками из того же `seo.products` + FTC-дисклеймер. Slug из title
  - **Pinterest:** заголовок ≤100 симв., описание ≤500 с 2–3 хэштегами, ссылка на блог-статью (не на YouTube — пин должен вести на наш монетизируемый актив)
  - **Threads:** пост ≤500 симв., hook + 1 тезис + ссылка
- Pin-изображение: Creatomate image-шаблон `pin_v1` (1000×1500, вертикаль) из thumbnail-арта + текст → R2
- Выход: `content_ready`

### 6.12 blog_publisher — поллинг `distributions(channel='blog', status='content_ready')`
- Пишет markdown в `blog/src/content/posts/<slug>.md` (frontmatter: title, date, video_id, products)
- Сборка Astro + `wrangler pages deploy` → проверка, что URL отвечает 200 → `posted`, `external_url` заполнен
- Блог собирается инкрементально — деплой всего сайта при каждой статье (static, это секунды)

### 6.13 pinterest_publisher — поллинг `content_ready`, слоты пн/ср/пт 14:00 UTC
- Pinterest API v5: создание пина (image из R2, board по формату: gaming→BOARD_GAMING, setup→BOARD_SETUP, link на блог)
- Rate-вежливость: не более 2 пинов за прогон. Ошибка квоты/ревью API → `failed` с понятным `last_error`, алерт владельцу
- `posted` + `external_url`

### 6.14 threads_publisher — поллинг `content_ready`, только при `threads_enabled=true`
- Threads API: двухшаговая публикация (create container → publish), текст + опционально изображение
- `posted` + `external_url`

**Общее для 6.12–6.14:** ретраи по стандартной схеме, 3 фейла → `failed` + запись в `errors` + алерт. Статус `failed` в distributions НЕ влияет на `videos` — контуры независимы.

---

## 7. TELEGRAM-БОТ (единственный интерфейс владельца)

### Команды
| Команда | Действие |
|---|---|
| `/status` | сводка: записи по статусам, очередь публикаций, статус дистрибуции (блог/пины/threads за неделю), последние ошибки |
| `/digest` | принудительно вызвать воскресный дайджест |
| `/pause` `/resume` | глобальная пауза пайплайна (флаг в конфиг-таблице) |
| `/budget` | расход за месяц: токены Claude, символы ElevenLabs, минуты Creatomate |
| `/footage` | статистика банка: игры, клипы, использование, дыры |

### Воскресный дайджест (автоматически, вс 07:00 UTC)
1. **Аналитика** — 5 строк: просмотры/подписчики за неделю, лучшее и худшее видео, статус форматов
2. **Темы** — до 10 карточек `pending_approval`, каждая с кнопками `✅ Одобрить` / `❌ Отклонить` / `✏️ Правка` (правка = владелец отвечает текстом, текст идёт Scriptwriter'у как директива)
3. **Видео на ревью** — карточки `ready_for_review`: превью-ссылка (R2 signed URL), title, описание свёрнуто; кнопки `✅ Публиковать` / `❌ Отклонить` / `✏️ Правка`
4. **Дистрибуция** — 3 строки: статьи в блоге, пины, threads за неделю + битые каналы, если есть
5. Напоминание о private-видео (до прохождения YT-аудита) и о footage-дырах, если есть

### Алерты (немедленно, вне дайджеста)
- dead-letter (3 неудачных попытки), footage_gap, квоты < 20%, kill формата, упавший воркер

---

## 8. ЭТАПЫ РЕАЛИЗАЦИИ (исполнять строго по порядку)

Каждая задача имеет acceptance criteria (AC). Этап закрыт = все AC этапа проходят. Перед переходом к следующему этапу — прогон `make test`.

### Этап 1 — Каркас и фундамент
| # | Задача | AC |
|---|---|---|
| 1.1 | Монорепо: структура, Docker Compose, Makefile, .env.example, конфиг-loader | `make test` запускается; compose поднимается локально |
| 1.2 | Миграции схемы из раздела 5 + конфиг-таблица `settings` | миграции применяются на чистую БД идемпотентно |
| 1.3 | `state_machine.py`: переходы, лог, FOR UPDATE SKIP LOCKED | юнит-тесты: запрещённый переход кидает исключение; конкурентный захват не дублирует |
| 1.4 | `retry.py` + `errors` + dead-letter | тест: 3 фейла → запись в errors |
| 1.5 | Telegram-бот: скелет, /status, /pause, /resume | команды отвечают на реальном боте |
| 1.6 | CLI `ingest_footage.py`: принимает папку с mp4 + CSV (game, tags), льёт в R2, пишет в `footage` | тестовый файл появляется в R2 и в таблице |
| 1.7 | CLI `yt_auth.py`: OAuth-флоу для refresh token | инструкция в docs, токен получается |
| 1.8 | Деплой на VPS: compose + systemd unit, авторестарт | `systemctl status` green; падение контейнера → автоподъём |

### Этап 2 — Производственное ядро (тема → готовое видео)
| # | Задача | AC |
|---|---|---|
| 2.1 | `llm.py`: обёртка Anthropic, логирование токенов в `settings`-счётчики | вызов возвращает текст; токены посчитаны |
| 2.2 | scriptwriter + промпты + факт-чек проход | dry-run: на тестовую тему — валидный script jsonb с hook/sections/footage-маркерами |
| 2.3 | voice: TTS, склейка, нормализация, R2 | mp3 в R2, громкость -16±1 LUFS |
| 2.4 | Creatomate-шаблоны: short_v1, long_v1, setup_v1 (JSON в templates/) | тест-рендер каждого проходит в Creatomate API |
| 2.5 | assembler: подбор футажа, footage_gap, рендер | dry-run: из voiced-записи получается mp4 в R2; при пустом банке — failed с footage_gap |
| 2.6 | thumbnail: Flux + image-шаблон, 2 варианта | 2 jpg в R2 правильного размера 1280×720 |
| 2.7 | seo_packager + link-валидация + FTC-строка | seo jsonb полный; битая ссылка в products исключается |
| 2.8 | CLI `seed_products.py` (CSV → products) | импорт 20 тестовых строк |
| 2.9 | **E2E dry-run:** вручную созданная approved-тема проходит до `ready_for_review` без вмешательства | один прогон зелёный для short и для long |

### Этап 3 — Публикация и человеческий гейт
| # | Задача | AC |
|---|---|---|
| 3.1 | Бот: карточки ready_for_review, Approve/Reject/Правка, signed URL превью | нажатие Approve переводит в publish_queued |
| 3.2 | publisher: слоты, private+publishAt, thumbnail, pinned comment | тестовое видео появляется на канале (private), thumbnail стоит |
| 3.3 | Режим до YT-аудита: напоминания о ручном переключении | в дайджесте появляется блок с private-видео |
| 3.4 | Бот: карточки тем pending_approval с кнопками | Approve темы → approved, пайплайн подхватывает |

### Этап 4 — Вход пайплайна
| # | Задача | AC |
|---|---|---|
| 4.1 | trend_scout: RSS + Reddit, дедуп | за прогон ≥20 тем в topics, повторный прогон не дублирует |
| 4.2 | topic_strategist: батч-скоринг, учёт footage-банка и format_weights | топ-10 в pending_approval с reasoning |
| 4.3 | Воскресный дайджест полностью (раздел 7) | дайджест приходит по cron и по /digest, все кнопки работают |
| 4.4 | scheduler.py: все cron-задания + поллинг-циклы под APScheduler | расписание видно в логах, задания срабатывают |

### Этап 5 — Петля обратной связи и надзор
| # | Задача | AC |
|---|---|---|
| 5.1 | analyst: снапшоты, format_weights, kill-критерий | weights меняются от тестовых данных; kill срабатывает на синтетике |
| 5.2 | watchdog: все 5 проверок + эскалация молчания | убитый вручную воркер → алерт ≤30 мин; залипшая запись → алерт |
| 5.3 | /budget и квоты | команда показывает реальные счётчики |
| 5.4 | Бэкап: pg_dump в R2 ежедневно + restore-скрипт | бэкап появляется; restore на чистую БД проходит |

### Этап 6 — Дистрибуционный контур
| # | Задача | AC |
|---|---|---|
| 6.1 | Миграция: distributions + enum'ы; конфиг-флаг `threads_enabled` (default false) | миграция идемпотентна |
| 6.2 | Astro-проект блога: layout, главная, страница статьи, product-блок компонент, RSS | локальная сборка; тест-деплой на Pages отвечает 200 |
| 6.3 | repurposer: fan-out + батч-генерация контента + pin-шаблон `pin_v1` | dry-run: published-видео порождает строки с валидным content; pin-картинка 1000×1500 в R2 |
| 6.4 | blog_publisher: md → сборка → деплой → верификация URL | статья реального тест-видео открывается по BLOG_DOMAIN |
| 6.5 | pinterest_publisher: пин по слотам, обработка квот | тест-пин виден на доске, ведёт на блог |
| 6.6 | threads_publisher + авто-рефреш токена в watchdog | при флаге ON тест-пост публикуется; при OFF строки skipped |
| 6.7 | Изоляция контуров: тест — failed в distributions не трогает videos | юнит-тест зелёный |

### Этап 7 — Hardening и сертификация
| # | Задача | AC |
|---|---|---|
| 7.1 | Прогон всех тестов, покрытие state_machine и retry 100% | `make test` зелёный |
| 7.2 | docs/RUNBOOK.md: рестарт, восстановление из бэкапа, смена ключа, частые ошибки | документ ≤1 стр., команды копипастятся |
| 7.3 | **Сертификация автономности:** 7 календарных дней работы; допустимые касания владельца — только воскресная сессия | за 7 дней: 0 неразрешённых dead-letter, дайджест пришёл, ≥1 видео прошло полный цикл, ≥1 статья в блоге и ≥1 пин опубликованы автоматически |
| 7.4 | Финальный отчёт владельцу: что задеплоено, расход за тестовую неделю, известные ограничения | отчёт в Telegram и в docs/ |

---

## 9. DEFINITION OF DONE (вся работа)

1. Система задеплоена на VPS, поднимается одной командой `make deploy`, переживает перезагрузку сервера
2. Полный цикл «тема из RSS → одобрение → published» работает без ручных шагов, кроме двух кнопок владельца
3. Дистрибуционный fan-out работает: published длинное видео автоматически порождает статью в блоге и пин в Pinterest без участия владельца
4. Сертификация автономности (7.3) пройдена
5. Все секреты только в `.env` на сервере, в репозитории — только `.env.example`
6. `make test` зелёный; e2e dry-run воспроизводим командой `make dry-run`
7. RUNBOOK написан; владелец может восстановить систему из бэкапа по нему

---

## 10. ПРАВИЛА РАБОТЫ ДЛЯ ИСПОЛНЯЮЩЕГО АГЕНТА

1. **Архитектуру не менять.** Константы раздела 2, стек раздела 3, схема раздела 5 — фиксированы. Заметил проблему — останавливайся и описывай её владельцу с предложением, не реализуй самовольно.
2. **Этапы строго по порядку.** Не начинать этап N+1 при незакрытых AC этапа N.
3. **Блокер (нет ключа, API отвечает не по документации, лимит тарифа) → стоп + вопрос владельцу** одним сообщением: что заблокировано, что нужно, что будешь делать после.
4. **Никаких моков в production-коде.** Моки живут только в tests/.
5. **Коммиты по задачам:** `feat(2.3): voice worker — TTS + normalization`. Один коммит = одна задача из таблиц.
6. **Деньги:** до этапа 6 все LLM/TTS/рендер-вызовы — на минимальных объёмах (короткие тестовые тексты, 360p тест-рендеры там, где Creatomate позволяет). Полноразмерные прогоны — только e2e в 2.9 и сертификация.
7. **Безопасность:** voice ID, owner chat ID и api_audit_passed читать только из ENV/settings; не логировать секреты; signed URL для превью с TTL 7 дней.
8. После каждого этапа — короткий отчёт владельцу в Telegram (что сделано, что дальше, есть ли вопросы).

---

*Версия плана: 1.0. При расхождении этого документа с реальностью API — правило 10.3.*
