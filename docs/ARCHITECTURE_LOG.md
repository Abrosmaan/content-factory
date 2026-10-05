# Architecture Decisions Log

> Запросы на изменение constants раздела 2 ТЗ. Каждое изменение фиксируется
> здесь с явным разрешением/отказом владельца, чтобы агент мог его реализовать
> без нарушения протокола 8 («Архитектуру не менять. Константы раздела 2 ТЗ
> нерушимы. Нашёл проблему — стоп и предложение владельцу, не самовольная
> реализация»).

---

## AL-001 — AI-генерация футажа вместо собственного банка

**Статус**: ✅ ACCEPTED (2026-06-12)
**Дата запроса**: 2026-06-12

### Решения владельца по 6 вопросам

| # | Вопрос | Решение |
|---|---|---|
| 1 | Какие форматы | `short` и `long_setup` → AI-генерация. `long_gaming` → собственный банк (без изменений) |
| 2 | Модель / длительность / разрешение | `fal-ai/kling-video`, **5–10с** на клип, **1080p** |
| 3 | Месячный бюджет на fal.ai | $40/мес — start, мониторим |
| 4 | YouTube AI-disclosure | seo_packager автоматически — флаг в `seo.ai_disclosure` + строка в description при publish |
| 5 | `footage` table + `ingest_footage.py` | Остаются для `long_gaming`. Watchdog продолжает алертить `footage_gap` для long_gaming |
| 6 | Retry на сбой fal.ai | 3 попытки → fallback: статичное изображение (либо thumbnail-фон, либо fal.ai/flux text-to-image) + Ken Burns зум через Creatomate `still_v1` → 5–10с mp4 клип |

### Дополнительный запрос (выдан вместе с ответами)

Pinterest-пины должны генерироваться **для каждого видео** (включая short).
Сейчас репурпозер выставляет `pinterest=pending` только для long-форматов.
Расширяется в этом же эпике: short тоже получает Pinterest канал;
background для пина — `thumbnail_r2_key` (long) или первый кадр `render`
через ffmpeg (short, у которых нет отдельной превьюшки).

### Реализация — открыт эпик 8

См. `CONTENT_FACTORY_TASKS.md` → ЭПИК 8. Задачи E8.1–E8.T.
**Затронутые constants**:
- §2.3 «Фиксированная идентичность канала. Один voice ID, один набор
  Creatomate-шаблонов, **футаж только из собственного банка**.»
- §2.4 «**Запрет чужого футажа.** Assembler принимает только клипы из
  таблицы `footage` (собственный банк) и ассеты из лицензированных
  библиотек, загруженные владельцем в R2.»

### Запрос владельца

1. Scriptwriter добавляет поле `scene_description` к каждой секции
   (пример: `"close-up gaming mouse on desk, hand reaching for it,
   cinematic lighting"`).
2. Новый воркер `footage_generator` — принимает `scene_description`,
   генерирует видеоклип через fal.ai (`fal-ai/minimax-video` или
   `fal-ai/kling-video`), сохраняет в R2 с привязкой `job_id` +
   `section_index`.
3. Assembler берёт клипы из `footage_generator` строго по порядку
   секций — не из таблицы `footage`.

Новый порядок: `scriptwriter → footage_generator → voice → assembler →
thumbnail → seo_packager`.

### Уточнение текущей логики (для контекста)

Сейчас assembler **не** берёт футаж «рандомно из R2». Раздел 6.5 ТЗ
и реализация `src/workers/assembler.py::_pick_footage`:

```
SELECT … FROM footage
WHERE %s = ANY(tags)        -- обязательное совпадение по тегу из
                            -- [FOOTAGE: <tag>] маркера в скрипте
  AND (game_irrelevant OR game = %s)
ORDER BY used_count ASC, created_at
LIMIT 1
```

Подбор детерминированный: тег секции, игра темы, least-used приоритет.
Собственный банк наполняется `python -m src.cli.ingest_footage`,
который владелец гонит сам.

### Что меняет принятие AL-001

| Что | Сейчас (банк) | Если принять (AI) |
|---|---|---|
| Источник клипов | владелец сам загружает геймплей в R2 | fal.ai генерирует |
| Стоимость на видео (long, 4-6 секций) | $0 (банк уже залит) | ~$2–6 (minimax $0.5/сек × 5–10с на секцию) |
| Время сборки long | ~10 мин (Creatomate render) | +5–15 мин на N клипов в fal.ai последовательно |
| Качество геймплейного контента (CS2, etc.) | реальный записанный геймплей | AI-видео — НЕ выглядит как реальный геймплей. Подходит для B-roll / setup / "cinematic", не для FPS-action |
| Воспроизводимость | one-to-one клип используется N раз | каждый раз новая генерация (нет cache by default) |
| Юридические риски | владелец отвечает за собственный footage | AI-контент: ToS fal.ai + правила YouTube для AI-generated content (disclosure label с 2024) |

### Открытые вопросы (ответы нужны перед реализацией)

1. **Все форматы или только setup?** Geo: для `long_gaming` (CS2-positioning
   и т.п.) AI-видео не похоже на реальный геймплей и обманывает зрителя
   в обещании. Для `long_setup` («cinematic close-up of mouse») AI работает
   адекватно. Применяем глобально или только к format ∈ {long_setup, bridge}?
2. **Модель и качество**: minimax-video, kling-2.1, hailuo-02, runway, veo? Какая
   target длительность клипа (5с / 10с / больше)? Какая разрешение (720p / 1080p)?
3. **Бюджет в месяц**: при 2 длинных видео в неделю × 5 секций × ~$1/клип =
   ~$40/мес только на fal.ai. С шортсами и retries — выше. Приемлемо?
4. **YouTube disclosure**: YouTube с 2024 требует помечать видео с AI-generated
   или synthetic content. Нужно автоматически выставлять `selfDeclaredMadeForKids=false`
   + соответствующий флаг при upload (currently API не имеет прямого поля,
   делается в Studio). Кто помечает — мы или владелец вручную?
5. **Что с существующим CLI / банком?** `ingest_footage.py` + таблица `footage` +
   `used_count` логика, `footage_gap` алерты в watchdog/digest — оставляем
   как fallback (если AI-генерация упала) или удаляем?
6. **Цена ошибки на одно видео**: при сбое fal.ai на 3-й из 5 секций мы (а)
   ретраим только эту секцию (нужна частичная сборка), (б) пересобираем весь
   список scene_descriptions, (в) failed с алертом? Выбираем стратегию retry.

### Что произойдёт после явного «да» с ответами на (1)-(6)

* Создаётся эпик 8 в `CONTENT_FACTORY_TASKS.md` с задачами:
  E8.1 scriptwriter — поле scene_description в script jsonb (+ обновление промпта и валидации);
  E8.2 src/workers/footage_generator.py — claim('scripted_with_scenes') → fal.ai per-section → R2 с ключом `generated/<video_id>/<idx>.mp4` → status='footage_ready';
  E8.3 assembler v2 — claim('footage_ready') (вместо 'voiced'), берёт клипы по индексу;
  E8.4 state_machine — добавление промежуточного статуса;
  E8.5 миграция (новая таблица `generated_clips` или поле в `videos`);
  E8.6 dry-run + replacement для тех частей пайплайна, что используют `footage` table;
  E8.T — полный e2e с реальной AI-генерацией.
* Constants раздела 2 ТЗ (§3 и §4) переписываются с явным указанием:
  «фиксированная идентичность канала по voice/тон/template; футаж генерируется
  AI на лету по scene_description».
* `BUILD_PLAN.md` обновляется отдельным разделом «v2 footage generation»
  со ссылкой на этот лог.

### Моё рекомендованное решение (для информации, не вместо ответа владельца)

**Compromise**: оставить собственный банк primary для `short` и `long_gaming`
(где реальный геймплей критичен), переключить на AI только `long_setup` и
`bridge` (где «cinematic product close-up» уместен). Это закрывает основное
преимущество AI (новые сцены без записи) и не ломает идентичность канала
на геймплейных форматах. Это middle-ground; владелец вправе принять
полный switch — но я обязан указать риски.

---

## AL-002 — Reddit с API на RSS

**Статус**: ✅ ACCEPTED (2026-06-15)
**Затронутое**: §6.1 раздела «Спецификации воркеров» (trend_scout).
**ENV**: удалены `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` из `.env`,
`.env.example`, `src/core/config.py`. Удалена зависимость `praw==7.7.1` из
`requirements.txt`.

### Изменение

Trend Scout больше не использует Reddit Data API (PRAW + OAuth).
Сабреддиты парсятся как обычные RSS-ленты через публичные
`https://old.reddit.com/r/<sub>/.rss`. Авторизация не требуется.

Список из 14 сабов перенесён в `src/core/trend_sources.RSS_FEEDS` рядом
с news-фидами.

### Reddit rate-limit

Reddit агрессивно режет sequential RSS-запросы с одного IP (429 со
второго запроса на www.reddit.com, чуть мягче на old.reddit.com).
Mitigation в `trend_scout._http_get_with_backoff`:
* Reddit-запросы выполняются с базовой паузой 5с между фидами.
* На 429 — экспоненциальный backoff 5/15/30/60с с jitter, до 4 попыток.
* Перед обходом список фидов случайно перемешивается — это распределяет
  «успешные саб'ы» поверх прогонов и не оставляет одни и те же сабы
  систематически за бортом.
* В steady-state cron `*/12h` ожидаемый coverage Reddit-сабов 5-8 из 14
  за один прогон; полный coverage набирается за ~2-3 прогона в течение
  суток.

### Приоритизация в Strategist

Reddit-RSS не отдаёт upvote count. Reddit's алгоритм `hot` уже отбирает
посты с приблизительно >100 upvotes до попадания в фид (это и было
условие «приоритизируй посты с >100 upvotes»). В промпте strategist'а
(`templates/prompts/strategist.md`) появился раздел «Source-based prior»:
`reddit:*` кандидаты получают +10 к raw score перед format_weights
multiplier, кроме явно off-niche заголовков.

### Что не сломалось

* dedup_hash остался тем же (sha256 normalized title) → не появляются
  дубли между Reddit и news-фидами.
* Изоляция воркеров — поллинг, FOR UPDATE SKIP LOCKED, retry.run_with_retry —
  без изменений.

---

*Что ещё планируется логировать здесь: voice ID rotation; смена Creatomate
templates версии; добавление новых каналов дистрибуции, etc.*
