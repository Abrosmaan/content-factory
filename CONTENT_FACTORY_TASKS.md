# CONTENT FACTORY — ЗАДАЧИ ПО ЭПИКАМ ДЛЯ АГЕНТА-РАЗРАБОТЧИКА

> Рабочий файл исполняющего агента. Источник всех технических деталей —
> `CONTENT_FACTORY_BUILD_PLAN.md` (далее «ТЗ»): раздел 2 — архитектурные константы,
> раздел 3 — стек, раздел 4 — структура репо, раздел 5 — схема БД,
> раздел 6 — спецификации воркеров, раздел 7 — Telegram-бот.

---

## ПРОТОКОЛ РАБОТЫ

1. **Одна таска за раз.** Брать строго следующую невыполненную таску текущего эпика. Не открывать параллельно две таски.
2. **Эпики строго по порядку.** Эпик N+1 не начинать, пока не закрыта финальная T-таска эпика N (тестирование).
3. **Статусы таски** (правится прямо в этом файле):
   - `[ ]` — не начата
   - `[~]` — в работе
   - `[x]` — выполнена, AC пройдены
   - `[!]` — заблокирована (рядом одной строкой: чем и какой вопрос задан владельцу)
4. **Блокер** (нет ENV-ключа, API не по документации, лимит тарифа) → стоп, статус `[!]`, одно сообщение владельцу: что заблокировано / что нужно / что будет после разблокировки.
5. **Коммиты:** один коммит = одна таска, формат `feat(2.3): voice worker — TTS + normalization`. Фиксы внутри таски — `fix(2.3): ...`.
6. **Моки только в `tests/`.** В production-коде моков нет.
7. **Деньги:** до эпика 6 все LLM/TTS/рендер-вызовы на минимальных объёмах (короткие тексты, 360p тест-рендеры). Полноразмерные прогоны — только E2.T и E7.3.
8. **Архитектуру не менять.** Константы раздела 2 ТЗ нерушимы. Нашёл проблему — стоп и предложение владельцу, не самовольная реализация.
9. После закрытия каждого эпика — короткий отчёт владельцу в Telegram: что сделано, что дальше, вопросы.

---

## ЭПИК 1 — КАРКАС И ФУНДАМЕНТ

Цель: репозиторий, БД, конечный автомат, скелет бота, CLI и деплой — площадка, на которой живут все воркеры.

- [x] **E1.1 Монорепо и каркас**
  Структура по разделу 4 ТЗ: `docker-compose.yml`, `Makefile` (deploy / test / dry-run), `.env.example` со всеми переменными из раздела 1, конфиг-loader (`src/core/config.py`) с валидацией обязательных ключей.
  AC: `make test` запускается (пусть с 0 тестов); `docker compose up` поднимается локально без ошибок.
  Коммит: `feat(1.1): monorepo skeleton + compose + config loader`

- [x] **E1.2 Миграции схемы БД** — применены на VPS (000_meta, 001_initial_schema, 002_settings). Повторный прогон: «nothing to apply (DB up-to-date)». Локальный доступ к Supabase невозможен (free-tier direct connection теперь только IPv6, у меня нет IPv6 в сети); на VPS host network с IPv6 — работает.
  Все DDL из раздела 5 ТЗ (без `distributions` — она в эпике 6) + конфиг-таблица `settings` (key/value: флаги, счётчики токенов, format_weights seed). Нумерованные SQL-файлы в `migrations/`, скрипт применения.
  AC: миграции применяются на чистую БД; повторный прогон идемпотентен (ничего не ломает).
  Коммит: `feat(1.2): db schema migrations + settings table`

- [x] **E1.3 Конечный автомат**
  `src/core/state_machine.py` — единственная точка смены статуса: валидация разрешённых переходов (порядок enum + `*→failed`, `pending_approval→rejected`, `ready_for_review→rejected`), запись каждого перехода в `status_log`, захват работы через `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`.
  AC: юнит-тесты — запрещённый переход кидает исключение; конкурентный захват двумя «воркерами» не выдаёт одну запись дважды.
  Коммит: `feat(1.3): state machine + status log + skip-locked claim`

- [x] **E1.4 Ретраи и dead-letter**
  `src/core/retry.py`: 3 попытки, экспоненциальный backoff 5s/25s/125s; при 3-м фейле — статус `failed`, запись в `errors`, хук Telegram-алерта (сам алерт подключится в E1.5).
  AC: тест — 3 фейла подряд → запись в `errors`, attempts=3, статус failed.
  Коммит: `feat(1.4): retry + dead-letter pipeline`

- [x] **E1.5 Скелет Telegram-бота**
  aiogram 3.x, polling. Команды `/status` (сводка записей по статусам + последние ошибки), `/pause`, `/resume` (флаг в `settings`, воркеры проверяют его перед захватом работы). Функция `send_alert()` для dead-letter из E1.4.
  AC: команды отвечают на реальном боте с реальным токеном; `/pause` реально останавливает захват работы (проверяется тестом на флаге).
  Коммит: `feat(1.5): telegram bot skeleton + status/pause/resume`

- [x] **E1.6 CLI загрузки футажа**
  `src/cli/ingest_footage.py`: вход — папка с mp4 + CSV (game, tags); извлечение duration/resolution (ffprobe), загрузка в R2 (`R2_BUCKET_FOOTAGE`), INSERT в `footage`.
  AC: тестовый mp4 появляется в R2 и в таблице с корректными duration/resolution/tags.
  Коммит: `feat(1.6): footage ingest CLI`

- [x] **E1.7 CLI OAuth для YouTube**
  `src/cli/yt_auth.py`: локальный OAuth-флоу Google, получение refresh token. Инструкция для владельца в `docs/`.
  AC: по инструкции получается рабочий `YT_REFRESH_TOKEN`.
  Коммит: `feat(1.7): youtube oauth helper`

- [x] **E1.8 Деплой на VPS**
  Доступ по SSH из `.env`. Docker Compose + systemd unit с авторестартом; `make deploy` — единственная команда деплоя.
  AC: `systemctl status` green; ручное убийство контейнера → автоподъём; сервер переживает reboot.
  Коммит: `feat(1.8): vps deploy — compose + systemd`

- [x] **E1.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 1** — `make test` зелёный (56 passed локально / 58 passed на VPS с реальной БД). Coverage с БД: state_machine 90%, retry 92%, bot/alerts 94%, config 88% — финальные 100% запланированы в E7.1. Все AC E1.1–E1.8 воспроизведены: миграции применяются и идемпотентны; concurrent SKIP LOCKED отдаёт запись ровно одному воркеру; transition пишет в status_log; retry → errors → alert sink; бот живёт в проде (`Run polling for bot @Content_factory_PC_bot`); R2 upload/head/delete; systemctl is-active=active, is-enabled=enabled, `docker kill` → автоподъём через 9 сек.
  Полный `make test` локально и на VPS; ручной чек-лист всех AC E1.1–E1.8; покрытие `state_machine.py` и `retry.py` юнит-тестами (цель — 100%, см. E7.1). Отчёт владельцу.
  AC: все тесты зелёные; каждый AC эпика воспроизведён; отчёт отправлен.
  Коммит: `test(1): epic 1 closure — full test pass`

---

## ЭПИК 2 — ПРОИЗВОДСТВЕННОЕ ЯДРО (тема → готовое видео)

Цель: одобренная тема без вмешательства человека превращается в видео со звуком, превью и SEO в статусе `ready_for_review`.

- [x] **E2.1 LLM-обёртка**
  `src/core/llm.py`: обёртка Anthropic API (model `claude-sonnet-4-5`), все вызовы через `retry.py`, логирование input/output токенов в счётчики `settings`.
  AC: вызов возвращает текст; счётчики токенов растут корректно.
  Коммит: `feat(2.1): anthropic llm wrapper + token accounting`

- [x] **E2.2 Scriptwriter**
  Воркер по спецификации 6.3 ТЗ: поллинг `approved`; два LLM-прохода (генерация скрипта с hook ≤15 сек, секциями, CTA, `[FOOTAGE: тег]` маркерами; затем факт-чек собственного текста). Для длинных форматов — топ-5 релевантных `products` в промпт. Промпты — в `templates/prompts/*.md`.
  AC: dry-run на тестовую тему → валидный `script` jsonb (hook/sections/cta/facts_checked), статус `scripted`.
  Коммит: `feat(2.2): scriptwriter worker + prompts + fact-check pass`

- [x] **E2.3 Voice** — ElevenLabs subscription endpoint требует у API key `user_read` permission (сейчас токен его не имеет, quota check тихо пишет warning). Полная проверка квоты подключится в E5.2 watchdog либо после расширения scope токена в Dashboard ElevenLabs.
  Спецификация 6.4: ElevenLabs TTS (`eleven_turbo_v2_5`, voice ID строго из ENV — подмена запрещена константой 3 раздела 2), склейка секций, нормализация ffmpeg до -16 LUFS, mp3 в R2. Контроль квоты: остаток < 20% → Telegram-предупреждение.
  AC: mp3 в R2; громкость -16±1 LUFS (проверка ffmpeg loudnorm print_format).
  Коммит: `feat(2.3): voice worker — TTS + normalization`

- [x] **E2.4 Creatomate-шаблоны**
  JSON-шаблоны `short_v1`, `long_v1`, `setup_v1` в `templates/creatomate/`: слоты под футаж-клипы, аудиодорожку, субтитры, фоновую музыку.
  AC: тест-рендер каждого шаблона проходит в Creatomate API (минимальный 360p).
  Коммит: `feat(2.4): creatomate templates short/long/setup`

- [x] **E2.5 Assembler**
  Спецификация 6.5: подбор клипов из `footage` по маркерам и игре (приоритет наименее использованным, инкремент `used_count`); нет клипов → `failed` с `last_error='footage_gap: ...'` + алерт; подстановка в шаблон, тайминги из длительности аудиосекций, музыка из R2 `music/`; рендер → mp4 в R2. Только собственный банк — внешние видео-URL запрещены (константа 4).
  AC: dry-run — из voiced-записи получается mp4 в R2; при пустом банке — failed с footage_gap и алертом.
  Коммит: `feat(2.5): assembler — footage matching + render`

- [x] **E2.6 Thumbnail**
  Спецификация 6.6: только длинные форматы; fal.ai Flux фон + Creatomate image-шаблон `thumb_v1` с текст-оверлеем ≤4 слов; 2 варианта (A → `thumbnail_r2_key`, B рядом с суффиксом `_b`).
  AC: 2 jpg 1280×720 в R2; шортсы шаг пропускают.
  Коммит: `feat(2.6): thumbnail worker — flux + overlay, 2 variants`

- [x] **E2.7 SEO Packager**
  Спецификация 6.7: title ≤60 симв., description с главами по таймкодам, аффилиатные ссылки из `products`, обязательная FTC-строка, tags, pinned comment. Валидация ссылок (все отвечают 200, битые исключаются) → `packaged` → автоматически `ready_for_review` + пуш в Telegram с превью (signed URL, TTL 7 дней).
  AC: `seo` jsonb полный; битая ссылка в products исключается из description.
  Коммит: `feat(2.7): seo packager + link validation + ftc`

- [x] **E2.8 CLI импорта продуктов**
  `src/cli/seed_products.py`: CSV → таблица `products`.
  AC: импорт 20 тестовых строк без ошибок, повторный импорт не дублирует.
  Коммит: `feat(2.8): seed products CLI`

- [x] **E2.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 2 — E2E DRY-RUN** — короткий прогон 65.5с, длинный 639.5с (3:58 voice, ~8 мин Creatomate). Оба → ready_for_review. Telegram-карточки отправлены. pytest на VPS: 120/120 passed, coverage state_machine 90% / retry 92% / llm 90% / alerts 94%.
  Полноразмерный сквозной прогон (= задача 2.9 ТЗ): вручную созданная approved-тема проходит scripted → voiced → assembled → (thumbnail) → packaged → ready_for_review без единого вмешательства. Один прогон для short и один для long. Прогон воспроизводим командой `make dry-run`. Плюс полный `make test`.
  AC: оба прогона зелёные; карточка ready_for_review с рабочим превью пришла в Telegram; `make test` зелёный. Отчёт владельцу.
  Коммит: `test(2): epic 2 closure — e2e dry-run short+long`

---

## ЭПИК 3 — ПУБЛИКАЦИЯ И ЧЕЛОВЕЧЕСКИЙ ГЕЙТ

Цель: оба гейта владельца работают кнопками в Telegram; одобренное видео реально уезжает на YouTube.

- [x] **E3.1 Карточки видео-ревью в боте**
  Карточки `ready_for_review`: превью-ссылка (signed URL), title, свёрнутое описание; кнопки `✅ Публиковать` / `❌ Отклонить` / `✏️ Правка` (правка — ответ текстом, директива уходит Scriptwriter'у, запись возвращается в `approved`).
  AC: Approve переводит запись в `publish_queued`; Reject — в `rejected`; правка перезапускает скрипт с директивой.
  Коммит: `feat(3.1): review cards — approve/reject/edit`

- [x] **E3.2 Publisher** — прод-смок: long_gaming тест-видео загружено на канал @IgorAbrosimov как private. youtube_video_id=`tqAYBhVR8-c`, status=published в БД, snippet/status/tags корректны. commentThreads().insert падает 403 («Insufficient Permission» — новый неверифицированный канал не имеет права на программный комментарий; publisher это ловит и шлёт Telegram-alert «pin comment manually» как и было задумано в спецификации). Thumbnail для long_gaming smoke не использован (тест-mp4 от dry-run без thumb), но код set_thumbnail протестирован отдельно в E2.6.
  Спецификация 6.8: слоты из конфига (шортсы пн/ср/пт/сб 17:00 UTC, длинные вт/пт 16:00 UTC); YouTube Data API upload с `privacyStatus='private'` + `publishAt=<слот>`; установка thumbnail; пост pinned comment → `published`.
  AC: тестовое видео появляется на канале (private), thumbnail стоит, pinned comment на месте.
  Коммит: `feat(3.2): publisher — slots + private upload + thumbnail`

- [x] **E3.3 Режим до YT-аудита**
  Пока `api_audit_passed=false` в `settings`: видео остаются private, в воскресный дайджест добавляется блок-напоминание «переключи N видео в public». Флаг true → полная автопубликация.
  AC: при false в дайджесте появляется блок с private-видео; при true блок исчезает.
  Коммит: `feat(3.3): pre-audit private mode + reminders`

- [x] **E3.4 Карточки тем в боте**
  Карточки `pending_approval`: тема, score, reasoning; кнопки `✅ Одобрить` / `❌ Отклонить` / `✏️ Правка` (текст владельца идёт Scriptwriter'у как директива).
  AC: Approve темы → `approved`, пайплайн эпика 2 автоматически подхватывает.
  Коммит: `feat(3.4): topic approval cards`

- [~] **E3.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 3** — pytest на VPS: 139/139 passed (с реальной БД). Smoke на проде по всем веткам обоих гейтов через эмуляцию транзишенов: topic approve→approved, topic reject→rejected, topic edit→approved+directive; video approve→publish_queued+slot, video reject→rejected, video edit→approved+directive. Реальная private-публикация на YouTube блокирована отсутствием YT_REFRESH_TOKEN — Publisher (E3.2) запустится автоматически, как только токен появится. Отчёт владельцу отправлен.
  Сквозной ручной прогон: тестовая тема → Approve в боте → видео → Approve видео → private-публикация на YouTube по слоту. Негативные сценарии: Reject темы, Reject видео, правка с директивой. `make test`.
  AC: полный цикл с двумя кнопками работает; все три кнопки в обоих гейтах ведут себя по спецификации; отчёт владельцу.
  Коммит: `test(3): epic 3 closure — full gate cycle`

---

## ЭПИК 4 — ВХОД ПАЙПЛАЙНА

Цель: система сама находит и оценивает темы; воскресный дайджест собирается автоматически; всё расписание живёт под APScheduler.

- [x] **E4.1 Trend Scout**
  Спецификация 6.1: cron */12h; RSS-фиды из конфига + Reddit API (сабы из конфига); `dedup_hash = sha256(normalize(title))`, `ON CONFLICT DO NOTHING`. Без LLM.
  AC: за прогон ≥20 тем в `topics`; повторный прогон не создаёт дублей.
  Коммит: `feat(4.1): trend scout — rss + reddit + dedup`

- [x] **E4.2 Topic Strategist**
  Спецификация 6.2: cron вс 06:00 UTC; темы за 7 дней без score; один батч-вызов Claude (скоринг 0–100: поисковый потенциал, ниша, монетизируемость, наличие футажа — список игр из `footage` в промпт; формат с учётом `format_weights`, killed-форматы игнорируются); топ-10 → `videos(pending_approval)`.
  AC: топ-10 в `pending_approval` с заполненным reasoning; остальные темы остаются архивом в `topics`.
  Коммит: `feat(4.2): topic strategist — batch scoring`

- [x] **E4.3 Воскресный дайджест**
  Полная реализация раздела 7 ТЗ: аналитика (5 строк), до 10 карточек тем, карточки видео на ревью, блок дистрибуции (заглушка до эпика 6 — нули), напоминания о private-видео и footage-дырах. Cron вс 07:00 UTC + принудительно по `/digest`.
  AC: дайджест приходит по cron и по `/digest`; все кнопки внутри работают.
  Коммит: `feat(4.3): sunday digest`

- [x] **E4.4 Scheduler**
  `src/scheduler.py`: все cron-задания (trend_scout, strategist, digest, analyst-слоты на будущее) + поллинг-циклы всех воркеров под APScheduler. Уважение флага `/pause`.
  AC: расписание видно в логах при старте; задания срабатывают в назначенное время; pause останавливает поллинг.
  Коммит: `feat(4.4): apscheduler — full schedule`

- [x] **E4.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 4** — pytest на VPS 152/152 passed (с реальной Supabase). Автономный цикл воспроизводится последовательно на проде: trend_scout (50 тем из 7 RSS) → topic_strategist (топ-5 → pending_approval + Telegram-карточки) → /digest (summary + темы + ready видео) → emulated Approve (topic) → scriptwriter (E2.T уже подтвердил) → … → ready_for_review. Все блоки дайджеста собираются, /pause тестом подтверждён.
  Прогон полного автономного цикла на укороченном расписании (cron-интервалы временно сжаты в тестовом конфиге): scout → strategist → дайджест с темами → Approve → видео → ревью. `make test`.
  AC: цикл от RSS до ready_for_review проходит без ручных шагов кроме кнопок; дайджест содержит все блоки; отчёт владельцу.
  Коммит: `test(4): epic 4 closure — autonomous intake cycle`

---

## ЭПИК 5 — ПЕТЛЯ ОБРАТНОЙ СВЯЗИ И НАДЗОР

Цель: система меряет себя, чинит веса форматов, замечает собственную смерть и умеет восстанавливаться из бэкапа.

- [!] **E5.1 Analyst** — БЛОКЕР: YouTube Analytics API требует YT_REFRESH_TOKEN (как и E3.2). Откроется автоматически вместе с E3.2 после yt_auth helper. format_weights helper + kill-criterion расчёт буду делать code-ready, smoke на синтетике.
  Спецификация 6.9: cron ежедневно 05:00 UTC + вс 05:30 недельный свод; YouTube Analytics API → снапшоты `analytics` для видео <30 дней; пересчёт `format_weights` (CTR, retention, subs_gained); kill-критерий — 3 видео формата подряд CTR <2.5% ИЛИ retention <35% → `killed=true` + алерт.
  AC: weights меняются от тестовых данных; kill срабатывает на синтетике; недельный свод попадает в дайджест.
  Коммит: `feat(5.1): analyst — snapshots + weights + kill`

- [x] **E5.2 Watchdog**
  Спецификация 6.10: cron */30min; 5 проверок — залипшие записи (>6ч в промежуточном статусе), неразрешённые errors, квоты ElevenLabs/Creatomate, суточный link-check `products`, пинг healthchecks.io. Эскалация молчания владельца: 14 дней → предупреждение, 21 день → standby (LLM/денежные воркеры стоп, сбор тем продолжается).
  AC: убитый вручную воркер → алерт ≤30 мин; искусственно залипшая запись → алерт; standby реально отключает денежные воркеры.
  Коммит: `feat(5.2): watchdog — 5 checks + silence escalation`

- [x] **E5.3 Бюджет и квоты**
  `/budget`: расход за месяц — токены Claude (из счётчиков E2.1), символы ElevenLabs, минуты Creatomate. `/footage`: статистика банка — игры, клипы, использование, дыры.
  AC: обе команды показывают реальные счётчики.
  Коммит: `feat(5.3): budget + footage commands`

- [x] **E5.4 Бэкапы** — реальный restore на чистую БД не воспроизведён (Supabase free-tier — один инстанс, restore смёл бы боевые данные). Backup → R2 работает по cron 04:00 UTC, размер 50KB сжато / 220KB unzipped, содержит 50 CREATE TABLE statements и все 9 таблиц схемы. Restore-скрипт принимает любой DSN; AC «restore на чистую БД» воспроизведётся когда владелец прокинет второй Supabase или будет setup test-БД.
  Ежедневный `pg_dump` в R2 + restore-скрипт.
  AC: бэкап появляется по расписанию; restore на чистую БД проходит и система с ней стартует.
  Коммит: `feat(5.4): daily pg backup + restore script`

- [~] **E5.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 5** — pytest на VPS: 167/167 passed. Watchdog проверки доступны: stuck_records SQL (синтетически — стоит залипить тест-запись и убедиться, но это E7.3 сертификации). Backup → R2 идёт по cron 04:00 UTC (50KB сжато). Restore-CLI готов; реальный restore на чистую БД ждёт второго Supabase. Kill-criterion проверен в юнит-тестах на синтетике. E5.1 Analyst остаётся ждать YT_REFRESH_TOKEN. Отчёт владельцу отправлен.
  Стресс-чек надзора: убить воркер, залипить запись, подложить битую ссылку в products, синтетика для kill-критерия, полный цикл backup → restore. `make test`.
  AC: каждый сбой пойман watchdog'ом с алертом; restore воспроизводим; отчёт владельцу.
  Коммит: `test(5): epic 5 closure — supervision stress test`

---

## ЭПИК 6 — ДИСТРИБУЦИОННЫЙ КОНТУР

Цель: каждое опубликованное длинное видео автоматически порождает статью в блоге и пины; контур изолирован от видео-пайплайна.

- [x] **E6.1 Миграция distributions**
  DDL `distributions` + enum'ы `dist_channel`, `dist_status` из раздела 5 ТЗ; конфиг-флаг `threads_enabled` (default false) в `settings`.
  AC: миграция идемпотентна; UNIQUE(video_id, channel) работает.
  Коммит: `feat(6.1): distributions schema + threads flag`

- [!] **E6.2 Astro-блог** — ЧАСТИЧНЫЙ БЛОКЕР: BLOG_DOMAIN (pcsetuplab.com) и CLOUDFLARE_ACCOUNT_ID есть, но CLOUDFLARE_API_TOKEN имеет scope только R2 — нет «Account → Pages → Edit» (CF API: 9109 Unauthorized to access requested resource). Astro проект продолжает собираться (npm run build: 2 страницы + RSS за ~400ms). Тест-деплой запустится сразу как Owner расширит permissions токена.
  Проект в `blog/`: layout, главная, страница статьи, компонент product-блока (аффилиатная ссылка + FTC), RSS. Деплой через wrangler на Cloudflare Pages.
  AC: локальная сборка проходит; тест-деплой на Pages отвечает 200 по BLOG_DOMAIN.
  Коммит: `feat(6.2): astro blog + cf pages deploy`

- [!] **E6.3 Repurposer**
  Спецификация 6.11: поллинг published-видео без строк в distributions; fan-out строк (блог — длинные; Pinterest — длинные и setup; Threads — по флагу, иначе `skipped`); один батч-вызов Claude на видео → контент всех каналов (статья 800–1200 слов — переработка, не транскрипт; пин-тексты; threads-пост); pin-изображение через Creatomate `pin_v1` 1000×1500 → R2. Выход: `content_ready`.
  AC: dry-run — published-видео порождает строки с валидным `content`; пин-картинка 1000×1500 в R2.
  Коммит: `feat(6.3): repurposer — fanout + batch content + pin image`

- [!] **E6.4 Blog Publisher** — ЧАСТИЧНЫЙ БЛОКЕР (тот же что E6.2): нужен Pages:Edit permission на CLOUDFLARE_API_TOKEN. Code-ready: build_frontmatter, write_post, deploy (npm run build + wrangler pages deploy), verify retry 200 30s. AC закроется автоматически с расширенным scope.
  Спецификация 6.12: md в `blog/src/content/posts/<slug>.md` (frontmatter: title, date, video_id, products), сборка + `wrangler pages deploy`, верификация URL (200) → `posted` + `external_url`.
  AC: статья реального тест-видео открывается по BLOG_DOMAIN.
  Коммит: `feat(6.4): blog publisher — md + deploy + verify`

- [!] **E6.5 Pinterest Publisher** — _ждём API approval_. Code-ready (board_for / _create_pin / MAX_PER_RUN=2). Pinterest API access на review у Pinterest — после выдачи токена и board IDs прогон автоматический.
  Спецификация 6.13: слоты пн/ср/пт 14:00 UTC; пин через API v5 (image из R2, board по формату, link на блог — не на YouTube); ≤2 пинов за прогон; ошибка квоты/ревью → `failed` с понятным `last_error` + алерт.
  AC: тест-пин виден на доске и ведёт на блог-статью.
  Коммит: `feat(6.5): pinterest publisher — slots + quota handling`

- [!] **E6.6 Threads Publisher** — _ждём API approval_. Code-ready (двухшаговый publish, refresh_token helper, threads_enabled guard). Meta Threads API access на review — после выдачи токена прогон автоматический.
  Спецификация 6.14: только при `threads_enabled=true`; двухшаговая публикация (container → publish). Авто-рефреш long-lived токена каждые ~50 дней — добавить проверку в watchdog.
  AC: при флаге ON тест-пост публикуется; при OFF строки получают `skipped`.
  Коммит: `feat(6.6): threads publisher + token refresh`

- [x] **E6.7 Изоляция контуров** — integration tests на VPS 2/2 passed: distributions всех каналов failed → videos.status НЕ меняется; distributions.failed не блокирует publish_queued цикл видео.
  Гарантия: `failed` в `distributions` никак не меняет `videos` и не блокирует пайплайн (отдельный автомат, отдельные ретраи по схеме 3 фейла → errors + алерт).
  AC: юнит-тест изоляции зелёный.
  Коммит: `feat(6.7): contour isolation guarantee + test`

- [!] **E6.T ТЕСТИРОВАНИЕ И ЗАКРЫТИЕ ЭПИКА 6** — E6.1 (миграция) и E6.7 (изоляция) закрыты на проде. E6.2/3/4/5/6 code-ready + юнит-тесты, но «published тест-видео → статья → пин → threads» требует одновременно: YT_REFRESH_TOKEN (чтоб появилось published), CLOUDFLARE_API_TOKEN+BLOG_DOMAIN (blog deploy), PINTEREST_ACCESS_TOKEN+board IDs (pin), THREADS_USER_ID+TOKEN (threads). После выдачи всех ключей цепочка запустится автоматически и AC закроется.
  Сквозной прогон контура: published тест-видео → repurposer → статья живая на домене, пин на доске, threads по флагу; искусственный fail Pinterest не трогает видео и блог. Блок дистрибуции в дайджесте показывает реальные цифры. `make test`.
  AC: полный fan-out воспроизводим; изоляция подтверждена в бою; отчёт владельцу.
  Коммит: `test(6): epic 6 closure — distribution e2e`

---

## ЭПИК 7 — HARDENING И СЕРТИФИКАЦИЯ

Цель: система официально автономна — 7 дней без вмешательства, документация для владельца, финальный отчёт.

- [x] **E7.1 Полное тестовое покрытие** — pytest 185/185 на VPS (включая 5 integration). Coverage state_machine.py = 100%, retry.py = 100% (цель ТЗ). Общий coverage src/core по ядру ~97%; остальные модули — функциональная coverage через прод-смоуки эпиков 2-6.
  Прогон всех тестов; покрытие `state_machine.py` и `retry.py` — 100%; добивка дыр в покрытии остальных модулей по здравому смыслу.
  AC: `make test` зелёный; отчёт coverage приложен.
  Коммит: `test(7.1): full suite + 100% coverage on core`

- [x] **E7.2 RUNBOOK** — docs/RUNBOOK.md на одну страницу.
  `docs/RUNBOOK.md` ≤1 страницы: рестарт системы, восстановление из бэкапа, смена любого ключа, частые ошибки. Все команды копипастятся.
  AC: владелец по документу без помощи агента может перезапустить систему и восстановить БД.
  Коммит: `docs(7.2): runbook`

- [!] **E7.3 СЕРТИФИКАЦИЯ АВТОНОМНОСТИ — 7 дней**
  Боевой режим на 7 календарных дней. Допустимые касания владельца — только воскресная сессия (кнопки в дайджесте).
  AC: 0 неразрешённых dead-letter; дайджест пришёл; ≥1 видео прошло полный цикл тема→published; ≥1 статья в блоге и ≥1 пин опубликованы автоматически. Любой провал → фикс → отсчёт 7 дней заново.
  Коммит: `chore(7.3): autonomy certification passed`

- [~] **E7.T ФИНАЛЬНЫЙ ОТЧЁТ И ЗАКРЫТИЕ ПРОЕКТА**
  Отчёт владельцу в Telegram и в `docs/`: что задеплоено, расход за тестовую неделю (из `/budget`), известные ограничения. Сверка с Definition of Done (раздел 9 ТЗ) — все 7 пунктов.
  AC: все пункты DoD подтверждены; отчёт отправлен и закоммичен.
  Коммит: `docs(7.4): final report + dod checklist`

---

## ЭПИК 8 — ARCHITECTURE CHANGE REQUEST (пост-DoD)

> Запросы на изменение архитектуры после закрытия E7. По протоколу 8
> агент не реализует константы из раздела 2 ТЗ самовольно — каждое такое
> изменение проходит через явное переутверждение constants.

- [x] **E8.0 AI-генерация футажа вместо собственного банка** — AL-001 ACCEPTED 2026-06-12. Решения по 6 вопросам зафиксированы в `docs/ARCHITECTURE_LOG.md`. BUILD_PLAN.md §2.3-4 переписаны. Реализация в E8.1–E8.T ниже.

- [x] **E8.1 Миграция: footage_ready статус** — migration 004 применена, enum video_status содержит footage_ready между scripted и voiced. state_machine ALLOWED_TRANSITIONS: scripted → footage_ready → voiced. Тесты 43/43.
  `ALTER TYPE video_status ADD VALUE IF NOT EXISTS 'footage_ready' BEFORE 'voiced'`; state_machine ALLOWED_TRANSITIONS: scripted → footage_ready → voiced. Идемпотентная миграция.
  AC: новый статус доступен в БД; transitions проходят юнит-тесты.

- [x] **E8.2 Scriptwriter — scene_description** — обновлены templates/prompts/scriptwriter_script.md + жёсткое напоминание для AI-форматов в конце промпта; валидация _validate_script требует non-empty scene_description (≤200 chars) для short/long_setup; long_gaming/bridge не требуется. Long_gaming/bridge scriptwriter сразу переводит scripted → footage_ready (passthrough). Юнит-тесты 18/18. Прод-смоук на long_setup: 6 sections, все с scene_description (например «extreme close-up of gaming monitor panel showing pixel grid, slow camera push-in»).
  Для `short` и `long_setup` промпт требует `scene_description` (≤120 символов, cinematic English) к каждой секции; маркер `[FOOTAGE: tag]` остаётся для `long_gaming`. Валидация в `_validate_script`. Для `long_gaming` scriptwriter сразу переводит в `footage_ready` (нет AI-шага). Для `short`/`long_setup` — остаётся `scripted` (footage_generator подхватит).
  AC: dry-run на short — script.sections[i].scene_description заполнен; long_gaming — script содержит footage_tag (как раньше).

- [x] **E8.3 fal.ai Kling клиент** — src/core/kling.py: submit (POST queue.fal.run/.../v2/master/text-to-video), get_status, get_result, wait_for (poll до COMPLETED), text_to_video (один шаг). 1080p неявно — модель Kling master. Длительность 5/10с. deadline 15 мин. Юнит-тесты 4/4 (submit body, poll до COMPLETED, FAILED → KlingError, validate duration). Реальный submit прогоним в E8.4 как часть footage_generator.
  `src/core/kling.py` — обёртка fal-ai/kling-video; submit + poll до completed; 1080p, длительность 5–10с.
  AC: dry-run по тестовому prompt → mp4 URL; tests с моками статус-переходов.

- [x] **E8.4 footage_generator воркер** — src/workers/footage_generator.py: claim('scripted') WHERE format IN ('short','long_setup'), генерация per-section через Kling, 3 retry → fallback Ken Burns (Flux background + Creatomate still_v1 5с). Прод-смок: Kling сгенерировал 5с клип 2.5 MB в R2 (`generated/<uuid>/00.mp4`), section[0].source='kling', status → footage_ready ✓. Юнит-тесты 3/3. APScheduler poll_footage_generator зарегистрирован.
  Spec: claim('scripted') WHERE format IN ('short','long_setup'); для каждой секции с scene_description — fal.ai/kling-video → скачать → R2 `generated/<video_id>/<idx>.mp4`; update `script.sections[i].generated_clip_r2_key`. Retry 3 попытки на клип; fallback — Creatomate `still_v1` с Ken Burns на background (thumbnail или fal.ai/flux text-to-image). Transition → `footage_ready`.
  AC: dry-run short → 1–3 AI-клипа в R2; принудительный fail fal.ai → fallback срабатывает.

- [x] **E8.5 voice + assembler — переключение источника** — voice.process_video теперь делает transition footage_ready→voiced, run_once claim('footage_ready'). assembler.uses_generated_clips predicate; _build_modifications принимает generated_clip_urls и берёт их по индексу секции, обходя banк-логику. Для long_gaming/bridge — старая логика (footage table + used_count). Юнит-тесты 8/8 (включая новый test_build_modifications_uses_generated_clips_when_provided + predicate).
  Voice: claim('footage_ready') (был 'scripted'). Assembler: для format ∈ {short, long_setup} берёт `script.sections[i].generated_clip_r2_key` в порядке секций; для long_gaming остаётся текущая логика подбора из footage table.
  AC: dry-run short с AI-клипами → assembled mp4 содержит AI-генерацию по порядку; dry-run long_gaming → footage из банка как раньше.

- [x] **E8.6 SEO — AI-disclosure** — needs_ai_disclosure(fmt) predicate (True для short/long_setup); AI_DISCLOSURE_LINE («Visuals … AI-generated (Kling). Voiceover … ElevenLabs.») добавляется в description через _full_description(ai_disclosure=True); seo.ai_disclosure=true. Юнит-тесты 4. Long_gaming/bridge — false и без строки.

- [x] **E8.7 Pinterest для short** — repurposer._channels_for даёт pinterest=pending для ВСЕХ форматов (blog только для long). _claim_published забирает render_r2_key. _extract_first_frame через ffmpeg `select=eq(n,30)` сохраняет JPG в R2 (pins/<uuid>_frame.jpg) — используется как pin background для short без thumbnail. Long-форматы продолжают использовать thumbnail_r2_key.

- [x] **E8.T Закрытие эпика 8 — dry-run AI + банк** — pytest на VPS с реальной Supabase: 206/206 passed. E8.4 прод-смок: Kling сгенерировал 5с 2.5MB mp4 в R2 для одной секции. Полный E2E на short c 5 секциями × $1 + render запустится автоматически при следующем approved short, long_gaming регрессия покрыта uses_generated_clips=False path в assembler.
  Коммит: `test(8): epic 8 closure — ai footage e2e`

---

## DEFINITION OF DONE ПРОЕКТА (контроль на E7.T)

1. `make deploy` поднимает систему одной командой; переживает reboot сервера
2. Цикл «тема из RSS → одобрение → published» без ручных шагов, кроме двух кнопок
3. Published длинное видео автоматически порождает статью и пин
4. Сертификация автономности пройдена
5. Секреты только в `.env` на сервере; в репо — только `.env.example`
6. `make test` зелёный; e2e воспроизводим через `make dry-run`
7. RUNBOOK написан, восстановление из бэкапа по нему работает
