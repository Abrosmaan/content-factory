# RUNBOOK

Все команды на VPS `root@$SSH_HOST` (см. `.env`). Локально команды
`make ...` подразумевают репозиторий с заполненным `.env`.

## Рестарт сервиса

```bash
# на VPS
systemctl restart content-factory
systemctl --no-pager status content-factory | head -15
```

После reboot сервера сервис стартует автоматически (systemd unit
`Restart=always`, `WantedBy=multi-user.target`).

## Деплой новой версии

С локального репо:

```bash
make deploy   # git push → rsync кода + .env → docker compose build → restart unit
```

Если поменялся `Dockerfile` или новые пакеты — пересборка занимает 2-3 мин.

### Auto-push на GitHub

После клонирования один раз поставь git hook:

```bash
make hooks-install
```

С этого момента **каждый `git commit` автоматически делает `git push origin HEAD`**.
Отключить разово: `CF_NO_AUTOPUSH=1 git commit -m "wip"`. `make deploy` всё
равно пушит перед rsync (отключаемо `CF_NO_PREDEPLOY_PUSH=1`).

`make push` — ручной fallback (`git push origin HEAD`).

## Бэкап и восстановление

Backup идёт **ежедневно в 04:00 UTC** автоматически (`backup_daily` job).
Можно прогнать вручную:

```bash
# на VPS
docker compose exec -T app python -m src.cli.backup --keep 30
# → backups/YYYY-MM-DD.sql.gz в R2 (bucket renders)
```

Восстановить **на чистую БД** (НЕ перетирать боевую без понимания):

```bash
docker compose exec -T app python -m src.cli.restore \
  --latest --dsn 'postgresql://postgres:...@TARGET_DB:5432/postgres'
# или конкретный ключ:
docker compose exec -T app python -m src.cli.restore \
  --key backups/2026-06-12.sql.gz --dsn '...'
```

После restore — заменить `SUPABASE_DB_URL` в `.env` на новый DSN,
`make deploy`, проверить `/status` в боте.

## Смена ключа (любого ENV)

1. Локально: правишь `.env`.
2. `make deploy` — `.env` копируется на VPS, сервис рестартится автоматически
   (deploy.sh: `scp .env`, потом `systemctl restart`).
3. Проверь логи: `ssh ... 'docker logs content-factory --tail 30'`.

## Команды бота

* `/status` — сводка пайплайна: видео по статусам, последние ошибки.
* `/pause` / `/resume` — глобальная пауза (флаг в `settings`). Все
  cron-jobs и поллинги уважают.
* `/digest` — принудительно собрать воскресный дайджест.
* `/budget` — расход за месяц (Claude tokens / ElevenLabs / Creatomate).
* `/footage` — статистика банка футажа + последние footage_gap-ошибки.
* `/audit` — текущее значение `api_audit_passed`; `/audit on|off` — переключить.

## Частые ошибки

| Симптом | Что делать |
|---|---|
| `make deploy` падает «SSH_HOST missing» | Проверь `.env` (нужны SSH_HOST/USER/PRIVATE_KEY_PATH). |
| `db.<project>.supabase.co: NXDOMAIN` локально | Supabase free-tier direct-connection теперь только IPv6. У большинства локальных сетей нет v6. На VPS работает (host network). |
| `pull access denied` в systemd | Косметика: `docker compose pull` падает для локально-собранного образа. Не блокер — main ExecStart всё равно запускает контейнер. |
| Бот не реагирует на /команды | `ssh ... 'docker logs content-factory --tail 50'`. Если «Run polling for bot @Content_factory_PC_bot» не печатается — `systemctl restart content-factory`. |
| footage_gap алерт | Заведи клипы под нужный (game, tag) — `python -m src.cli.ingest_footage --dir ./folder --csv ./tags.csv`. |
| ElevenLabs 401 missing_permissions | API key без `user_read` scope — quota check best-effort. Расширь scope в дашборде ElevenLabs или жди заметку в watchdog. |
| Видео залипло (status_log молчит >6ч) | Watchdog сам пошлёт alert. Ручная диагностика: `/status`, посмотреть `errors` в Supabase. |
| Лимит Creatomate / fal.ai | Алерт в Telegram. Жди ресет квоты или повышай тариф. |

## Куда смотреть в коде

| Что | Где |
|---|---|
| Состояния пайплайна | `src/core/state_machine.py` (ALLOWED_TRANSITIONS) |
| Cron / поллинги | `src/scheduler.py` |
| Промпты Claude | `templates/prompts/*.md` |
| Шаблоны Creatomate | `templates/creatomate/*.json` |
| Команды бота | `src/bot/handlers.py`, `src/bot/review.py` |
| Дайджест | `src/bot/digest.py` |
| Воркеры | `src/workers/*.py` |

## Контакты

Bot: `@Content_factory_PC_bot`. Owner chat id указан в `.env`
(`TELEGRAM_OWNER_CHAT_ID`).
