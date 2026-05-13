# Backend MKY / EduIrk

Backend портала MKY / EduIrk написан на FastAPI и отвечает за API сайта, авторизацию, серверные роли, статьи и новости, разделы Дома учителя, запись на ТПМПК, журнал действий, шаблоны и генерацию грамот.

В этой ветке тяжёлый backend RAG/assistant полностью исключён из защищаемой версии. Backend запускается без Chroma, vector store, embeddings, LangChain, GigaChat и без переменной `ENABLE_RAG`. Демонстрационный чат-бот находится только во frontend и не обращается к backend assistant API.

## Возможности

- JWT-авторизация.
- Серверная проверка ролей и прав доступа.
- Роли `admin`, `methodist`, `domu_editor`, `operator`, `user`.
- Управление статьями, новостями и публикациями Дома учителя.
- Проверка владения и области публикации статей на backend.
- Публичная запись на ТПМПК/консультации с защитой от дублей.
- Административная панель ТПМПК, журнал действий и служебные API для оператора.
- Шаблоны грамот, конструктор шаблонов и генерация PDF.
- Лёгкий `/api/search/` для подсказок навигации по страницам сайта.

## Переменные окружения

Создайте `.env` из `.env.example`:

```bash
copy .env.example .env
```

Для Linux/macOS:

```bash
cp .env.example .env
```

Основные переменные:

- `DATABASE_URL` - полная строка подключения к PostgreSQL.
- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` - параметры БД, если не используется `DATABASE_URL`.
- `SECRET_KEY` - секрет JWT. В реальном окружении заменить на уникальное значение.
- `PD_ENCRYPTION_KEY` - ключ для защиты персональных данных. В реальном окружении заменить.
- `ENABLE_DEV_TEST_USERS=true` - включает локальные демонстрационные аккаунты.
- `ADMIN_EMAIL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_ROLE` - данные администратора.

В `.env.example` должны быть только безопасные placeholder-значения. Не коммитьте реальные `.env`, секреты, дампы БД, локальные базы, логи, кеши, сгенерированные грамоты и загруженные тестовые файлы.

## Локальный запуск

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python create_admin.py
uvicorn main:app --reload
```

Для Linux/macOS:

```bash
source venv/bin/activate
```

После запуска:

- API: [http://localhost:8000](http://localhost:8000)
- Swagger/OpenAPI: [http://localhost:8000/docs](http://localhost:8000/docs)

## Тестовые пользователи

Если включено `ENABLE_DEV_TEST_USERS=true`, при старте доступны демонстрационные пользователи:

- `admin@example.local` / `admin123` / `admin`
- `methodist@example.local` / `methodist123` / `methodist`
- `domu@example.local` / `domu123` / `domu_editor`
- `operator@example.local` / `operator123` / `operator`
- `user@example.local` / `user123` / `user`

## Тесты

Полная проверка:

```bash
pytest -q
```

Targeted-проверки по ключевым зонам:

```bash
pytest -q tests/test_security_roles.py tests/test_tpmpk_api.py tests/test_dom_uchitelya_api.py
pytest -q tests/test_template_full_api.py tests/test_certificate_variables.py tests/test_auth_roles.py
```

## Docker

Запуск backend с PostgreSQL из директории `backend`:

```bash
docker compose config
docker compose up --build
```

Запуск всего проекта из корня репозитория:

```bash
cd ..
docker compose config
docker compose up --build
```

Docker-конфигурация не содержит Chroma/vector/RAG volumes и не требует GigaChat-ключей.

## RAG и чат-бот

Backend RAG/assistant намеренно удалён из этой ветки. В backend нет маршрутов `/assistant`, `/api/assistant`, `/rag`, `/api/rag`, `/search/rag`, нет Chroma, vector store, embeddings, LangChain и GigaChat-зависимостей.

Frontend содержит только демонстрационный чат-бот. Он показывает быстрые подсказки и статические ответы для навигации по порталу, но не отправляет запросы на backend assistant/RAG endpoints.

## Проверка чистоты перед коммитом

```bash
git status --short
git diff --name-only
git diff --stat
git ls-files | grep -Ei "(\.env|dev\.db|dump|backup|__pycache__|\.pytest_cache|coverage|node_modules|dist|build|\.log|sqlite|generated|uploads|chroma|rag|gigachat|vector)"
```

В нормальном состоянии в git не должны попадать секреты, локальные базы, дампы, кеши, `__pycache__`, `.pytest_cache`, логи и тяжёлые артефакты.
