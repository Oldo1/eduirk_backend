# Деплой на Render + Supabase

Бэкенд подготовлен для запуска на Render как Docker Web Service и подключения к PostgreSQL в Supabase.

## 1. Создать базу в Supabase

1. Открой https://supabase.com/dashboard и создай проект.
2. В проекте нажми **Connect** и скопируй PostgreSQL connection string.
3. Для Render лучше брать **Session pooler**, если прямой IPv6-доступ недоступен:

```text
postgresql+psycopg2://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Прямое подключение используй только если Render может достучаться до Supabase по IPv6 или в Supabase включен IPv4:

```text
postgresql+psycopg2://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require
```

Если в пароле базы есть спецсимволы, сначала URL-encode пароль, потом вставляй его в `DATABASE_URL`.

## 2. Создать backend на Render

1. Залей репозиторий в GitHub/GitLab.
2. Открой https://dashboard.render.com/ и создай новый **Blueprint** из репозитория.
3. Если Render спросит blueprint/root directory, укажи `backend`, чтобы он прочитал `backend/render.yaml`.
4. Если Render ищет `render.yaml` только в корне репозитория, перенеси blueprint в корень и замени Docker-пути на:

```yaml
dockerfilePath: ./backend/Dockerfile
dockerContext: ./backend
```

5. Заполни секретные переменные:

```text
DATABASE_URL=<строка подключения Supabase>
CORS_ALLOWED_ORIGINS=https://домен-фронтенда
GIGACHAT_CREDENTIALS=<опционально, нужно для живых ответов ассистента>
YC_KEY_ID=<опционально, нужно для документов из Yandex Object Storage>
YC_SECRET_KEY=<опционально, нужно для документов из Yandex Object Storage>
```

`SECRET_KEY` и `PD_ENCRYPTION_KEY` Render сгенерирует автоматически.

## 3. Проверка первого деплоя

После деплоя открой:

```text
https://<render-service>.onrender.com/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

Docker-образ перед запуском `uvicorn` выполняет `python -m alembic upgrade head`, поэтому свежая Supabase-база получит миграции автоматически.

Если в логах Render есть ошибка `connection to server at "localhost", port 5432 failed`, значит в сервисе не задан `DATABASE_URL` или Render не прочитал `render.yaml`. Открой **Service -> Environment** и добавь `DATABASE_URL` вручную со строкой Supabase и `?sslmode=require`.

## 4. Создать админа на production

В Render открой shell backend-сервиса и выполни:

```bash
ADMIN_EMAIL=admin@imcro.local ADMIN_PASSWORD='set-a-long-unique-password' python create_admin.py
```

После этого можно входить с этим email и паролем.

## Важно

- Файловая система Render непостоянная без persistent disk. Сгенерированные грамоты, загрузки статей, OCR-cache и Chroma-индекс лучше вынести в object storage или подключить платный Render Disk.
- В production держи `ENABLE_DEV_TEST_USERS=false`.
- Для Supabase не разгоняй пул соединений. Текущие значения `DB_POOL_SIZE=5` и `DB_MAX_OVERFLOW=5` дают максимум 10 соединений на один Render instance.
