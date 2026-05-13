# Backend MKY / EduIrk

FastAPI backend для проекта MKY / EduIrk: API, авторизация, роли, статьи, разделы Дома учителя, запись ТПМПК, шаблоны и генерация грамот.

## Docker
Запустите Docker
Из корня backend-репозитория:

```bash
docker compose config
docker compose up --build
```

Docker поднимает PostgreSQL и backend. Перед стартом backend автоматически выполняет миграции Alembic.
PostgreSQL доступен внутри Docker-сети как `db:5432`; наружу публикуется только backend-порт `8000`, чтобы не конфликтовать с локальным PostgreSQL на `5432`.

Адреса:

- API: http://localhost:8000
- Swagger/OpenAPI: http://localhost:8000/docs

### Если PostgreSQL ругается на роль mky_user

PostgreSQL создаёт `POSTGRES_USER`, `POSTGRES_PASSWORD` и `POSTGRES_DB` только при первом создании volume. Если раньше проект запускался со старыми данными, старый volume может содержать другого пользователя.

Самый быстрый сброс dev-базы:

```bash
docker compose down -v
docker compose up --build
```

Текущий compose использует явный volume `eduirk_backend_mky_pgdata`, чтобы не цеплять старый `backend_pgdata`.

## Локальный Запуск

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Поднимите PostgreSQL с данными из `.env.example`:

```bash
docker run --name mky-postgres -e POSTGRES_USER=mky_user -e POSTGRES_PASSWORD=mky_password -e POSTGRES_DB=mky_db -p 5432:5432 -d postgres:16
```

Затем:

```powershell
alembic upgrade head
python create_admin.py
uvicorn main:app --reload
```

`create_admin.py` спросит пароль интерактивно, если `ADMIN_PASSWORD` не задан.

PowerShell:

```powershell
$env:ADMIN_PASSWORD="admin123"
python create_admin.py
```

CMD:

```cmd
set ADMIN_PASSWORD=admin123
python create_admin.py
```

Linux/macOS:

```bash
ADMIN_PASSWORD=admin123 python create_admin.py
```

## Тестовые Пользователи

При `ENABLE_DEV_TEST_USERS=true` backend автоматически создаёт dev-аккаунты:

- `admin@mky.test` / `admin123` / `admin`
- `methodist@mky.test` / `methodist123` / `methodist`
- `domu@mky.test` / `domu123` / `domu_editor`
- `operator@mky.test` / `operator123` / `operator`
- `user@mky.test` / `user123` / `user`

Если frontend пишет `Failed to fetch`, сначала проверьте, что backend отвечает на http://localhost:8000/docs.

## DATABASE_URL

Рабочий локальный пример из `.env.example`:

```env
DATABASE_URL=postgresql+psycopg2://mky_user:mky_password@localhost:5432/mky_db
```

Если `DATABASE_URL` не задан, backend собирает строку подключения из `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`. Также поддерживаются `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.

## Проверки

```bash
alembic upgrade head
pytest -q
```

Backend не требует RAG/Chroma/GigaChat/vector-зависимостей и не использует `ENABLE_RAG`.
