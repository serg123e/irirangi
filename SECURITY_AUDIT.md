# Глубокий аудит безопасности Irirangi

**Дата:** 2026-02-19
**Область:** bot.py, Docker-инфраструктура, конфигурация сервисов
**Метод:** ручной анализ кода, моделирование угроз

---

## 1. Инъекции и обработка ввода

### 1.1 HIGH — Аргументы пользователя передаются напрямую в mpc

**Файл:** `app/bot.py:249-254`

```python
async def cmd(command, update, context):
    args = context.args          # ← прямой ввод пользователя из Telegram
    output = mpc_command(command, args)
```

`context.args` — это список строк от пользователя. Он передаётся в `mpc_port_command` и становится частью:
```python
cmd = ["mpc", "-h", host, "-p", port, command] + args
```

**Атака:** Пользователь с правами админа отправляет `/seek --help` или `/del --format "%file%"`. Поскольку аргументы добавляются после имени субкоманды mpc, они интерпретируются как аргументы субкоманды, а не глобальные флаги. Прямой RCE невозможен (используется list, не shell=True), но можно вызвать непредвиденное поведение mpc.

**Вектор для `/add`** (строка 195-198): `filename = " ".join(args)` → `mpc_command("add", [filename])`. Команда `mpc add` принимает путь относительно music_directory. Ввод `../../etc/mpd.conf` может попытаться добавить файл вне music_directory. MPD должен ограничивать это, но исторически в MPD были баги path traversal.

**Рекомендация:** Валидировать аргументы — для `/seek` принимать только `[+-]?\d+[%:]?[\d:]*`, для `/del` и `/move` — только `\d+`.

### 1.2 HIGH — Имя файла из yt-dlp не санитизируется

**Файл:** `app/bot.py:168-170`

```python
output = subprocess.check_output(cmd).decode().strip()
path, filename_ext = os.path.split(output)
mpc_add_file(filename_ext)     # ← несанитизированное имя
```

`sanitize_filename()` применяется к файлам из Telegram (строки 131, 138), но **не** к имени файла, возвращённому yt-dlp. Имя файла формируется из заголовка видео и может содержать спецсимволы. yt-dlp уже записал файл на диск с этим именем, но при передаче в `mpc add` спецсимволы могут сломать MPD.

**Рекомендация:** Применить `sanitize_filename()` к `filename_ext` на строке 170, либо использовать `--restrict-filenames` в yt-dlp.

### 1.3 MEDIUM — Deprecated regex escape

**Файл:** `app/bot.py:157`

```python
match = re.search("(?P<url>https?://[^\s]+)", text)
```

`\s` в обычной строке (не raw string `r"..."`) — deprecated с Python 3.12, будет ошибкой в будущих версиях.

**Рекомендация:** Заменить на `r"(?P<url>https?://[^\s]+)"`.

### 1.4 LOW — Логическая ошибка ветвления soundcloud/youtube

**Файл:** `app/bot.py:164`

```python
if "soundcloud" in url:
```

URL вида `https://www.youtube.com/watch?v=abc&soundcloud=1` попадёт в ветку SoundCloud (без `-f 140`). Не уязвимость, но ошибка логики.

**Рекомендация:** Проверять по `parsed.hostname` из `validate_url`, а не по вхождению строки.

---

## 2. Отказ в обслуживании (DoS)

### 2.1 HIGH — Нет лимитов на скачивание

**Файл:** `app/bot.py:165-168`

- `timeout 300s` — 5 минут на один скачиваний.
- Нет ограничения на размер файла.
- Нет ограничения на количество одновременных скачиваний.
- Нет ограничения на размер плейлиста.
- Нет проверки свободного дискового пространства.

**Атака:** Админ (или любой, если ADMIN_USERNAMES пуст) отправляет 20 ссылок на 2-часовые видео. 20 параллельных процессов yt-dlp + ffmpeg = исчерпание CPU, памяти и диска.

**Рекомендация:**
- Ограничить количество одновременных скачиваний (asyncio.Semaphore).
- Проверять свободное место перед скачиванием.
- Ограничить длительность трека через `--max-filesize` в yt-dlp.

### 2.2 HIGH — ffmpeg без таймаута

**Файл:** `app/bot.py:141-143`

```python
cmd = ["ffmpeg", "-i", tmp_path, "-af", "...", "/voice/" + filename]
output = subprocess.check_output(cmd)
```

ffmpeg запущен без `timeout`. Крупное или повреждённое голосовое сообщение может заблокировать процесс на неопределённое время. А поскольку это синхронный вызов в async-контексте — блокируется весь event loop бота.

**Рекомендация:** Обернуть в `timeout`, аналогично yt-dlp: `["timeout", "60s", "ffmpeg", ...]`.

### 2.3 MEDIUM — Блокирующие вызовы в event loop

**Файл:** `app/bot.py:79,84,97,143,168`

Все `subprocess.check_output()` и `time.sleep()` блокируют asyncio event loop. Во время скачивания бот не обрабатывает другие сообщения.

**Рекомендация:** Использовать `asyncio.create_subprocess_exec` или `loop.run_in_executor()`.

### 2.4 MEDIUM — Нет лимитов ресурсов в Docker

**Файл:** `docker-compose.yml`

Контейнеры не имеют ограничений `mem_limit`, `cpus`, `pids_limit`. Fork-бомба или утечка памяти в одном контейнере может убить хост.

---

## 3. Аутентификация и авторизация

### 3.1 MEDIUM — Icecast admin-панель открыта наружу

**Файлы:** `icecast.xml:18-23`, `docker-compose.yml:9`

Порт 8000 проброшен на хост. Панель администратора Icecast (`/admin/`) доступна по сети. Пароли — пример из шаблона. Если при деплое пароли не заменены, любой может:
- Просматривать/убивать слушателей
- Перемещать точки монтирования
- Просматривать конфигурацию через `/admin/stats`

**Рекомендация:** Добавить `<listen-socket>` с привязкой к `127.0.0.1` для admin, или запретить доступ к `/admin/` через reverse proxy.

### 3.2 LOW — Icecast status page публичен

**Файл:** `icecast.xml:33`

```xml
<alias source="/" destination="/status.xsl"/>
```

Корень Icecast показывает status page с количеством слушателей, mount points, метаданными треков. Информационная утечка.

---

## 4. Инфраструктура и цепочка поставок

### 4.1 HIGH — Python 3.9 достиг EOL

**Файл:** `Dockerfile.py.bot:1`

```dockerfile
FROM python:3.9-alpine
```

Python 3.9 EOL — октябрь 2025. Нет патчей безопасности. CVE в стандартной библиотеке (urllib, ssl, http) остаются незакрытыми.

**Рекомендация:** Обновить до `python:3.12-alpine` или `python:3.13-alpine`.

### 4.2 MEDIUM — Непиннированные зависимости

**Файл:** `requirements.txt`

```
python-telegram-bot
yt-dlp
```

Без версий. Мажорное обновление может сломать API или внести уязвимость. Supply-chain атака через PyPI (typosquatting, account takeover) будет автоматически подтянута.

**Рекомендация:** Зафиксировать версии. Использовать хеши: `pip install --require-hashes`.

### 4.3 MEDIUM — mp3gain из HTTP edge/testing

**Файл:** `Dockerfile.py.bot:20`

```dockerfile
RUN apk add mp3gain --repository=http://dl-cdn.alpinelinux.org/alpine/edge/testing/
```

- **HTTP** (не HTTPS) — MITM-атака при сборке образа.
- **edge/testing** — нестабильный репозиторий без полного аудита.

Alpine использует подпись пакетов, что снижает риск MITM, но не устраняет его полностью (атака на время до верификации, подмена индекса).

**Рекомендация:** Использовать `https://`. Рассмотреть замену mp3gain (он не используется в bot.py — возможно мёртвая зависимость).

### 4.4 MEDIUM — Непиннированные базовые образы

**Файлы:** `Dockerfile.icecast:1`, `Dockerfile.music.mpd:1`, `Dockerfile.voice.mpd:1`

```dockerfile
FROM alpine:latest
```

Сборка нерепродуцируема. Обновление Alpine может сломать пакеты.

**Рекомендация:** Пиннировать: `alpine:3.19` или конкретный SHA.

### 4.5 LOW — mp3gain не используется

**Файл:** `Dockerfile.py.bot:20`

`mp3gain` установлен, но нигде в коде не вызывается. Лишний attack surface.

**Рекомендация:** Удалить, если не используется.

---

## 5. Сетевая безопасность

### 5.1 MEDIUM — Нет TLS

- Icecast слушает на HTTP 8000 (аудиостриминг без шифрования).
- MPD source-пароли передаются в plaintext между контейнерами.
- Telegram Bot API использует HTTPS (это ok).

**Рекомендация:** Поставить reverse proxy (nginx/caddy) с TLS перед Icecast.

### 5.2 LOW — MPD слушает на 0.0.0.0

**Файлы:** `mpd.music.conf:6`, `mpd.voice.conf:6`

```
bind_to_address "0.0.0.0"
```

MPD слушает на всех интерфейсах внутри контейнера. Порты не проброшены на хост (хорошо), но другие контейнеры в Docker-сети могут подключиться к MPD напрямую.

---

## 6. Обработка ошибок и утечка информации

### 6.1 MEDIUM — mpc output отправляется пользователю без фильтрации

**Файл:** `app/bot.py:253-254`

```python
output = mpc_command(command, args)
await context.bot.send_message(chat_id=chat_id, text=output)
```

Вывод mpc (включая ошибки) отправляется в Telegram без фильтрации. При ошибке MPD может вернуть internal paths, версию софта, конфигурацию.

### 6.2 LOW — sys.stdout.encoding может быть None

**Файл:** `app/bot.py:79`

```python
output = subprocess.check_output(cmd).decode(sys.stdout.encoding)
```

В контейнере `sys.stdout.encoding` может быть `None` (при перенаправлении stdout). Вызовет `TypeError`.

**Рекомендация:** Заменить на `.decode("utf-8")`.

---

## Матрица рисков

| # | Уровень | Находка | Эксплуатируемость |
|---|---------|---------|-------------------|
| 1.1 | HIGH | Аргументы пользователя в mpc без валидации | Требует права админа |
| 1.2 | HIGH | Имя файла из yt-dlp не санитизируется | Требует права админа |
| 2.1 | HIGH | Нет лимитов на скачивание (DoS) | Требует права админа |
| 2.2 | HIGH | ffmpeg без таймаута | Требует права админа |
| 4.1 | HIGH | Python 3.9 EOL — нет патчей безопасности | Пассивная угроза |
| 1.3 | MEDIUM | Deprecated regex `\s` | Нет прямой угрозы |
| 2.3 | MEDIUM | Блокирующие subprocess в event loop | Усиливает DoS |
| 2.4 | MEDIUM | Нет лимитов ресурсов Docker | Требует доступ к боту |
| 3.1 | MEDIUM | Icecast admin-панель открыта | Требует сетевой доступ |
| 4.2 | MEDIUM | Непиннированные pip-зависимости | Supply chain |
| 4.3 | MEDIUM | mp3gain из HTTP edge/testing | Supply chain, MITM |
| 4.4 | MEDIUM | Непиннированные alpine:latest | Supply chain |
| 5.1 | MEDIUM | Нет TLS на Icecast | Сетевой перехват |
| 6.1 | MEDIUM | mpc output без фильтрации | Info leak |
| 1.4 | LOW | Ошибка ветвления soundcloud/youtube | Нет прямой угрозы |
| 3.2 | LOW | Icecast status page публичен | Info leak |
| 4.5 | LOW | mp3gain установлен, но не используется | Лишний attack surface |
| 5.2 | LOW | MPD на 0.0.0.0 внутри контейнера | Внутри Docker сети |
| 6.2 | LOW | sys.stdout.encoding может быть None | Crash |
