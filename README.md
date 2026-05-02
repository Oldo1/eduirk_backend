# Проект MKY - EduIrk (Backend)

Единая информационная система для образовательных организаций г. Иркутска.

## Обзор проекта
Проект состоит из трех основных частей:
1. **Frontend**: React-приложение (Vite)
2. **Backend**: API на FastAPI
3. **Database**: PostgreSQL

---

## 🚀 Быстрый запуск (Через Docker)
Самый простой способ запустить весь проект одной командой.

**Требования:** Установленный Docker Desktop.

1. Откройте терминал в папке `backend` (или корне проекта, где лежит файл `docker-compose.yml`).
2. Выполните команду:
   ```bash
   docker compose up -d --build
   ```
3. Дождитесь загрузки контейнеров.
4. Откройте ссылки в браузере:
   - **Frontend:** [http://localhost:5173](http://localhost:5173)
   - **Backend API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

*Чтобы остановить проект, выполните:*
```bash
docker compose down
```

---

## 💻 Локальный запуск для разработки (Без Docker)
Используйте этот метод, если вы хотите вносить изменения в код и сразу видеть результат (hot-reload).

**Требования:**
- **Node.js** (v18+)
- **Python** (3.10+)
- Установленный и запущенный сервер **PostgreSQL** (или запущенный контейнер с базой данных).

### Шаг 1: Запуск базы данных
Даже при локальной разработке удобнее всего поднять БД через Docker:
```bash
cd backend
docker compose up -d db
```
*База данных будет доступна на порту `5433`.*

### Шаг 2: Настройка и запуск Backend
1. Перейдите в папку бэкенда:
   ```bash
   cd backend
   ```
2. Создайте виртуальное окружение и активируйте его:
   ```bash
   # Для Windows
   python -m venv venv
   venv\Scripts\activate
   
   # Для macOS/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
4. Убедитесь, что в файле `backend/.env` прописан правильный путь к БД (уже должен быть):
   ```env
   DATABASE_URL=postgresql://postgres:admin@localhost:5433/eduirk_db
   ```
5. Примените миграции базы данных (если необходимо):
   ```bash
   alembic upgrade head
   ```
6. Запустите сервер:
   ```bash
   uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```
*API будет доступно по адресу: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)*

### Шаг 3: Настройка и запуск Frontend
Откройте новый терминал и выполните:
1. Перейдите в папку фронтенда:
   ```bash
   cd ../frontend
   ```
2. Установите зависимости:
   ```bash
   npm install
   ```
3. Запустите dev-сервер:
   ```bash
   npm run dev
   ```
*Сайт будет доступен по адресу: [http://localhost:5173](http://localhost:5173) (или 5174, если порт занят).*

---

## 🛠 Структура проекта
- `main.py` — Точка входа в приложение
- `models.py` — Модели базы данных (SQLAlchemy)
- `routers/` — Роутеры API
- `alembic/` — Миграции базы данных

## 📝 Разработка и отправка изменений (Git)
Для внесения изменений следуйте этим шагам:
1. Убедитесь, что вы на правильной ветке:
   ```bash
   git checkout rudak-backend
   ```
2. Внесите изменения и убедитесь, что код работает.
3. Проверьте статус файлов:
   ```bash
   git status
   ```
4. Закоммитьте и запушьте:
   ```bash
   git add .
   git commit -m "docs: обновлена инструкция по локальному запуску"
   git push origin HEAD
   ```

---
© 2026 MKY Team
