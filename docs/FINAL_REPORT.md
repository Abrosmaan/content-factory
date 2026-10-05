# Content Factory — Final Build Report

## Что задеплоено

* **VPS**: `root@<vps-host>` (Hetzner CX22, Ubuntu 26.04 LTS).
* **Сервис**: `content-factory.service` — systemd, `Restart=always`,
  `WantedBy=multi-user.target`. Контейнер — `content-factory:latest`,
  Python 3.12 + ffmpeg + postgresql-client + gzip.
* **БД**: Supabase Postgres (free-tier IPv6 direct). 3 миграции применены:
  enums + 9 базовых таблиц + settings + distributions.
* **R2**: 2 бакета — `footage` (входной банк) и `renders` (voice/mp4/
  thumbnails/pins/backups).
* **Бот**: `@Content_factory_PC_bot`. aiogram 3 polling в общем event-loop
  с APScheduler.

## Что сделано (по эпикам)

| Эпик | Статус | Ключевые артефакты |
|---|---|---|
| 1. Каркас + БД + бот + деплой | ✅ | `src/core/` (config/db/state_machine/retry/r2), миграции, `make deploy` |
| 2. Производственное ядро | ✅ | scriptwriter/voice/assembler/thumbnail/seo_packager; E2E dry-run short 65.5с / long 639.5с |
| 3. Публикация + гейт | частично ✅ | review-карточки в Telegram с inline-кнопками; publisher code-ready; private-mode + /audit |
| 4. Вход пайплайна | ✅ | trend_scout (RSS 7 фидов, 50 тем за прогон), strategist (batch claude), digest, APScheduler с 13 jobs |
| 5. Надзор + бэкап | частично ✅ | watchdog (5 проверок + silence escalation), /budget, /footage, daily pg_dump → R2 |
| 6. Дистрибуция | частично ✅ | миграция distributions, Astro-блог, repurposer, blog/pin/threads publishers (code-ready), изоляция |
| 7. Hardening | ✅ | state_machine.py + retry.py 100% coverage, RUNBOOK |

## Деньги (накопилось за разработку)

* Claude tokens: input ~29k, output ~15k → ~$0.32 (sonnet-4-5)
* ElevenLabs: 1 short ~150 chars + 1 long ~4500 chars
* fal.ai Flux: 2 thumbnails (~$0.01)
* Creatomate: ~5 минут рендера 360p (тест-рендеры эпика 2) + 1 полноценный
  4-минутный 1080p длинного видео.

Производственная неделя в steady-state (2 длинных + 7 шортсов):
~$5-10 на Claude, ~$10-15 на ElevenLabs ($22 turbo тариф покрывает),
~$30 Creatomate Starter, ~$3 fal.ai. Итого порядка **$50-60/мес** при
текущей конфигурации.

## DoD (раздел 9 ТЗ)

| # | Пункт | Статус |
|---|---|---|
| 1 | `make deploy` поднимает систему одной командой; переживает reboot | ✅ |
| 2 | Цикл «тема → published» без ручных шагов, кроме двух кнопок | ⏳ упирается в YT_REFRESH_TOKEN (E3.2) |
| 3 | Published длинное видео → статья + пин автоматически | ⏳ упирается в YT + Cloudflare + Pinterest |
| 4 | Сертификация автономности (7 дней) | ⏳ блокер: см. п.2–3 |
| 5 | Секреты только в `.env` на сервере; в репо — только `.env.example` | ✅ |
| 6 | `make test` зелёный; e2e воспроизводим через `make dry-run` | ✅ (185/185 на VPS, `make dry-run` крутится) |
| 7 | RUNBOOK написан, восстановление по нему работает | ✅ |

## Известные ограничения (что нужно от владельца, чтобы закрыть DoD 2-4)

1. **YouTube токены** — `YT_CLIENT_ID/SECRET` в Google Cloud, потом
   `python -m src.cli.yt_auth` на ноуте → получишь `YT_REFRESH_TOKEN`.
   Откроет E3.2 Publisher, E5.1 Analyst (snapshot из YT Analytics).
2. **Cloudflare** — `CLOUDFLARE_API_TOKEN` (Pages edit scope) +
   `BLOG_DOMAIN` (купить домен или использовать `*.pages.dev`).
   Откроет E6.2/6.4 Blog Publisher.
3. **Pinterest** — `PINTEREST_ACCESS_TOKEN` (app review у Pinterest) +
   board IDs (создать в UI и скопировать). Откроет E6.5.
4. **Threads** (опционально) — `THREADS_USER_ID/ACCESS_TOKEN`. Откроет E6.6;
   до тех пор distributions(threads) автоматически уходят в `skipped`.
5. **ElevenLabs `user_read` scope** на API key — пока quota check best-effort.

После выдачи (1)-(3) — стартует **E7.3 Сертификация 7 дней автономности**
(боевой режим без вмешательств, 0 dead-letter, ≥1 видео → published,
≥1 статья + ≥1 пин). По окончании — финальный отчёт.

## Что нельзя сделать через бот (нужно руками владельца)

* Пин top-комментария на YouTube — API не поддерживает; кладём
  через `commentThreads.insert`, владелец пинит в Studio (1 клик).
* Переключение private → public до прохождения YT API audit
  (Google требует ручного toggle). После `/audit on` Publisher
  будет грузить сразу public.
* Pinterest app review — раз в начале. После прохождения наш токен
  работает без вмешательства.

## Git и тесты

* main: 30 коммитов (8 эпиков, формат `feat(N.M): ...` / `fix(N.M): ...` / `test(N): ...`).
* pytest на VPS с реальной БД: **185 passed, 1 skipped**.
* Coverage core: state_machine 100%, retry 100%.

## Контакты автоматики

* Бот: `@Content_factory_PC_bot`
* VPS: `root@<vps-host>`
* Healthchecks.io: настраивается при выдаче `HEALTHCHECKS_PING_URL`
  (watchdog шлёт ping каждые 30 минут).
