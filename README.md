# Проект MKY - EduIrk (Backend)

Бэкенд-часть информационной системы для образовательных организаций г. Иркутска. Построен на FastAPI.

## Предварительные требования

Перед запуском убедитесь, что у вас установлены:
- **Python 3.10+**
- **Docker Desktop** (для PostgreSQL)
- **Node.js** (для фронтенда)

## Пошаговая инструкция запуска

Для полноценной работы системы необходимо запустить все компоненты в следующем порядке:

### 1. Фронтенд
```bash
cd frontend
npm run dev
```

### 2. База данных
```bash
cd backend
docker compose up -d db
```

### 3. Бэкенд
```bash
cd backend
set DATABASE_URL=postgresql://postgres:admin@localhost:5433/eduirk_db
set ENABLE_RAG=false
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## Как запускать всё одновременно
Рекомендуется открыть три отдельных окна терминала для каждого компонента.

## Как остановить
- Для фронтенда и бэкенда: нажмите `Ctrl+C` в терминале.
- Для базы данных:
```bash
cd backend
docker compose down
```

## Структура проекта
- `main.py` — Точка входа в приложение
- `models.py` — Модели базы данных (SQLAlchemy)
- `routers/` — Роутеры API
- `alembic/` — Миграции базы данных

## Как внести изменения и запушить
1. Переключитесь на ветку:
   ```bash
   git checkout rudak-backend
   ```
2. Внесите изменения.
3. Закоммитьте:
   ```bash
   git add .
   git commit -m "docs: add detailed README with launch instructions for team"
   ```
4. Запушьте:
   ```bash
   git push origin HEAD
   ```

---
© 2026 MKY Team
