# Backend MKY / EduIrk

FastAPI backend для проекта MKY / EduIrk: API, авторизация, роли, статьи, разделы Дома учителя, запись ТПМПК, шаблоны и генерация грамот.

## Docker

Из корня backend-репозитория:

```bash
docker compose config
docker compose up --build
```

Docker поднимает PostgreSQL и backend. Перед стартом backend автоматически выполняет миграции Alembic.

Адреса:

- API: http://localhost:8000
- Swagger/OpenAPI: http://localhost:8000/docs

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
