# Музыкальный бот для Discord (Python / discord.py)

Поддерживает:
- **YouTube** — по ссылке или просто по названию (`!play never gonna give you up`)
- **SoundCloud** — по прямой ссылке на трек
- **Прямые ссылки на аудио** (mp3/wav и т.д.) и вложенные в сообщение аудиофайлы
- **Spotify** — ссылки на трек / плейлист / альбом (бот берёт название и исполнителя и ищет то же самое на YouTube — сам Spotify не отдаёт аудио через API, это ограничение самого Spotify, а не бота)

## 1. Установка

### 1.1 Python-зависимости
```bash
pip install -r requirements.txt
```

### 1.2 FFmpeg (обязательно, без него звука не будет)
- **Windows**: скачай сборку с https://www.gyan.dev/ffmpeg/builds/, распакуй и добавь папку `bin` в PATH
- **macOS**: `brew install ffmpeg`
- **Linux (Debian/Ubuntu)**: `sudo apt install ffmpeg`

Проверка: команда `ffmpeg -version` в терминале должна что-то выводить.

## 2. Создание бота в Discord

1. Зайди на https://discord.com/developers/applications → **New Application**
2. Вкладка **Bot** → **Add Bot**
3. Включи **MESSAGE CONTENT INTENT** (обязательно, иначе бот не увидит команды)
4. Скопируй **токен** бота (кнопка Reset Token / Copy)
5. Вкладка **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot permissions: `Send Messages`, `Connect`, `Speak`, `Read Message History`
   - Открой сгенерированную ссылку и добавь бота на свой сервер

## 3. (Опционально) Настройка Spotify

Нужно только для того, чтобы бот понимал ссылки open.spotify.com:
1. Зайди на https://developer.spotify.com/dashboard → **Create app**
2. Возьми **Client ID** и **Client Secret**

## 4. Токены и запуск

Проще всего через переменные окружения:

```bash
export DISCORD_TOKEN="токен_твоего_бота"
export SPOTIFY_CLIENT_ID="твой_client_id"        # опционально
export SPOTIFY_CLIENT_SECRET="твой_client_secret" # опционально

python bot.py
```

(На Windows в PowerShell: `$env:DISCORD_TOKEN="..."`)

Либо просто впиши токены прямо в начало `bot.py` вместо заглушек — но так их легко случайно закоммитить в публичный репозиторий, поэтому лучше через переменные окружения.

## 5. Команды

| Команда | Действие |
|---|---|
| `!play <текст/ссылка>` (или `!p`) | Найти и добавить трек в очередь |
| `!queue` (или `!q`) | Показать очередь |
| `!nowplaying` (или `!np`) | Что играет сейчас |
| `!skip` (или `!s`) | Пропустить трек |
| `!pause` / `!resume` | Пауза / продолжить |
| `!loop` | Вкл/выкл повтор текущего трека |
| `!volume <0-150>` | Громкость в % |
| `!stop` | Остановить и очистить очередь |
| `!leave` (или `!dc`) | Выйти из голосового канала |

## 6. Частые проблемы

- **Бот молчит, ошибок нет** → не установлен/не в PATH ffmpeg
- **"discord.py has no attribute ..."** → стоит старая версия, обнови: `pip install -U discord.py[voice]`
- **Команды не срабатывают** → не включён MESSAGE CONTENT INTENT в настройках приложения (см. пункт 2.3)
- **YouTube иногда отдаёт ошибку 403 / "Sign in to confirm you're not a bot"** → это ограничение самого YouTube на стороне yt-dlp, помогает обновление yt-dlp до последней версии (`pip install -U yt-dlp`)

## 7. Деплой на Railway через GitHub

### 7.1 Заливаем код в GitHub

```bash
cd music-bot
git init
git add .
git commit -m "первый коммит музыкального бота"
git branch -M main
git remote add origin https://github.com/ТВОЙ_ЮЗЕРНЕЙМ/НАЗВАНИЕ_РЕПО.git
git push -u origin main
```

(Или просто создай пустой репозиторий на github.com и залей файлы через веб-интерфейс — drag & drop тоже подойдёт.)

Файл `.env` с токенами в репозиторий **не коммить** — токены задаются через переменные окружения Railway (см. ниже), не через файл.

### 7.2 Создаём проект на Railway

1. Зайди на https://railway.app → **New Project** → **Deploy from GitHub repo**
2. Выбери свой репозиторий с ботом (может понадобиться авторизовать Railway в GitHub)
3. Railway сам увидит `requirements.txt` и `nixpacks.toml` и соберёт окружение — `nixpacks.toml` уже включает установку **ffmpeg**, так что вручную ставить его не нужно

### 7.3 Переменные окружения

В проекте Railway: вкладка **Variables** → добавь:
- `DISCORD_TOKEN` = токен твоего бота
- `SPOTIFY_CLIENT_ID` = (опционально)
- `SPOTIFY_CLIENT_SECRET` = (опционально)

### 7.4 Тип сервиса

Так как бот не слушает HTTP-порт, а просто держит соединение с Discord, в настройках сервиса (**Settings**) поставь тип **Worker** (не Web) — тогда Railway не будет ждать от него ответа на HTTP и не будет ругаться в логах на отсутствие открытого порта. Команда запуска уже прописана в `Procfile`/`nixpacks.toml` (`python bot.py`), менять не нужно.

### 7.5 Обновление бота

Дальше просто:
```bash
git add .
git commit -m "обновление"
git push
```
Railway сам подхватит пуш в `main` и передеплоит бота.

## 8. Хостинг 24/7 (альтернативы)

Кроме Railway, можно закинуть бота на VPS (Timeweb, Hetzner, Selectel и т.п.) или Fly.io — везде важно не забыть поставить ffmpeg в окружение.
