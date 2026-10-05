# Получение `YT_REFRESH_TOKEN` для канала

Refresh-токен — это «вечный пропуск» для воркера publisher. Получается
один раз на ноутбуке владельца (не на VPS, потому что нужен браузер).

## 1. Включить YouTube API в Google Cloud

1. Открой [console.cloud.google.com](https://console.cloud.google.com), создай проект (или выбери существующий).
2. APIs & Services → **Library** → найди и включи:
   - YouTube Data API v3
   - YouTube Analytics API
3. APIs & Services → **OAuth consent screen**:
   - User Type: **External**
   - Заполни обязательные поля (application name = «Content Factory», support email).
   - Scopes: добавь
     - `.../auth/youtube.upload`
     - `.../auth/youtube`
     - `.../auth/yt-analytics.readonly`
   - Test users: добавь свой Google-аккаунт владельца канала.

## 2. Создать OAuth-клиент

1. APIs & Services → **Credentials** → Create credentials → **OAuth client ID**.
2. Application type: **Desktop**.
3. Скачай `client_secret_*.json` (либо скопируй `client_id` и `client_secret`).
4. Положи значения в `.env`:

   ```env
   YT_CLIENT_ID=...
   YT_CLIENT_SECRET=...
   ```

## 3. Запустить helper

В корне репо (виртуальное окружение с зависимостями):

```bash
python -m src.cli.yt_auth
# или, если хочешь дать JSON прямо:
python -m src.cli.yt_auth --json ~/Downloads/client_secret.json
```

Откроется браузер, согласись с доступом. В терминале появится строка:

```
YT_REFRESH_TOKEN (вставь в .env):

1//0g...очень_длинная_строка...
```

Скопируй её в `.env`:

```env
YT_REFRESH_TOKEN=1//0g...
```

## 4. Если не пришёл `refresh_token`

Google отдаёт `refresh_token` только при **первом** согласии пользователя.
Если уже даёшь согласие повторно:

1. Открой [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
2. Найди приложение «Content Factory», нажми «Remove Access».
3. Повтори `python -m src.cli.yt_auth`.

## 5. Замена/протухание

`refresh_token` живёт пока:
- не отозван вручную в Google Account → Security,
- не сменился пароль аккаунта канала,
- не превышен квот на отдельную пару client_id ↔ user (раз в полгода
  может отвалиться — тогда повторить helper).

Watchdog (эпик 5) ругается, если YouTube API возвращает `invalid_grant`.
