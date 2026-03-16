# RunTime Visuals — Online Tracker Bot

## Деплой на Railway (пошагово)

### 1. Залей папку на GitHub
Создай новый репозиторий на GitHub, закинь туда **только содержимое папки `RunTime-tgbot`**:
```
bot.py
requirements.txt
Procfile
nixpacks.toml
runtime.txt
```

### 2. Railway
1. Зайди на [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Выбери репозиторий
3. Зайди в **Variables** и добавь переменную:
   ```
   BOT_TOKEN = 1234567890:AAF...твой_токен...
   ```
4. Railway сам поставит `PORT` — ничего больше не нужно
5. Нажми **Deploy** — через ~1 минуту всё запустится

### 3. Получи публичный URL
После деплоя зайди в **Settings → Networking → Generate Domain**.  
Получишь URL вида: `https://runtime-bot-production.up.railway.app`

### 4. Вставь URL в клиент
В `OnlineTracker.java` замени:
```java
private static final String API_URL = "https://runtime-bot-production.up.railway.app";
```

---

## HTTP API

| Метод | URL | Тело | Ответ |
|-------|-----|------|-------|
| POST | `/heartbeat` | `{"uuid":"...","username":"..."}` | `{"ok":true,"online":5}` |
| GET  | `/online`    | —                                | `{"online":5}` |

## Telegram команды

| Команда | Описание |
|---------|----------|
| `/online` | Текущий онлайн |
| `/stats`  | Онлайн + пик за сессию |

---

## Локальный запуск (для теста)

```bash
pip install -r requirements.txt
set BOT_TOKEN=твой_токен
python bot.py
```
