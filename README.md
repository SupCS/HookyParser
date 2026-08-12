# Cinema Schedule Parser

Парсер расписаний Hooky Entertainment, ShowBiz Cinemas, Violet Crown и Times Square Grand Slam с изолированной историей снимков, Flask-интерфейсом и PostgreSQL на Railway. Локально приложение продолжает работать с SQLite.

## Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Чтобы рядом с фильмами отображалось текущее место в IMDb Popularity, добавьте
`OMDB_API_KEY` в окружение (ключ используется только для сопоставления названия
с IMDb ID). Рейтинг кэшируется в базе на 24 часа; период можно изменить через
`IMDB_POPULARITY_CACHE_HOURS`. Расписание продолжает работать при недоступности
OMDb или IMDb.

Откройте `http://127.0.0.1:5000`.

## Локальный сбор всех локаций

```powershell
python collect.py
```

Команда cron собирает все настроенные бренды и локации на сегодня и 30 следующих дней через публичный GraphQL INDY, сохраняет отдельную дневную запись для каждой даты и завершается. Данные разделены ключом бренда и не объединяются между сетями. Повторный сбор текущих и будущих дней перезаписывает их данные; прошедшие дни остаются неизменными. Горизонт cron можно изменить переменной `HOOKY_FUTURE_DAYS` (от 0 до 31, по умолчанию 30). В интерфейсе основная кнопка обновляет выбранную локацию, а компактная кнопка «Все» — все локации выбранного бренда; оба ручных действия всегда используют горизонт в 13 будущих дней. При отсутствии `DATABASE_URL` используется `hooky_history.sqlite3`.

## Деплой на Railway

### 1. PostgreSQL

В Railway Project Canvas выберите `+ New` → `Database` → `PostgreSQL`.

### 2. Web-сервис

Создайте сервис из этого GitHub-репозитория. `railway.toml` автоматически запускает:

```text
gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 90 --access-logfile - --error-logfile - app:app
```

В Variables добавьте reference variable:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
COLLECTOR_KEY=<длинная случайная строка>
```

Затем в Settings → Networking создайте публичный домен. Healthcheck доступен по `/health`.

### 3. Cron-сервис

Создайте второй сервис из того же GitHub-репозитория. В его Settings укажите:

```text
Start Command: python collect.py
Cron Schedule: 0 */12 * * *
```

Добавьте ему ту же reference variable:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Cron работает по UTC, запускается в 00:00 и 12:00 UTC, собирает все локации и завершает процесс. Для ручного полного снимка откройте Cron-сервис и выполните `Run Now`/ручной запуск deployment.

## Ручной сбор через API

Endpoint защищается переменной `COLLECTOR_KEY`:

```powershell
Invoke-RestMethod -Method Post -ContentType application/json `
  -Headers @{ "X-Collector-Key" = "YOUR_SECRET" } `
  -Uri https://your-domain.up.railway.app/api/collect `
  -Body '{}'
```

Кнопка в интерфейсе обновляет только выбранную локацию. Cron, `python collect.py` и защищённый `/api/collect` всегда собирают все локации по умолчанию.
