# Проект MKY - EduIrk

Единая информационная система для образовательных организаций г. Иркутска.

---

## 🚀 Быстрый запуск (Через Docker)
Самый простой способ запустить весь проект одной командой.

**Требования:** Установленный Docker Desktop.

### Стандартный запуск
1. Откройте терминал в корневой папке проекта.
2. Выполните команду:
   ```bash
   docker compose up -d --build
   ```

### ⚡ Запуск БЕЗ RAG (Ускоренный)
Если вам не нужен ИИ-помощник и вы хотите запустить проект быстрее:
1. Откройте `docker-compose.yml`.
2. В разделе `backend` -> `environment` установите `ENABLE_RAG: "false"`.
3. Запустите:
   ```bash
   docker compose up -d --build
   ```
*Это пропустит загрузку тяжелых ML-библиотек и моделей.*

---

## 💻 Локальный запуск (Без Docker)

### 🐍 Backend (FastAPI)
1. Перейдите в папку: `cd backend`
2. Создайте и активируйте окружение:
   ```bash
   python -m venv venv
   # Windows: venv\Scripts\activate
   # Linux/macOS: source venv/bin/activate
   ```
3. Установите зависимости: `pip install -r requirements.txt`
4. **Запуск без RAG (Рекомендуется для скорости):**
   - **Windows:** `set ENABLE_RAG=false && uvicorn main:app --reload`
   - **Linux/macOS:** `ENABLE_RAG=false uvicorn main:app --reload`

### ⚛️ Frontend (React/Vite)
1. Перейдите в папку: `cd frontend`
2. Установите зависимости: `npm install`
3. Запустите: `npm run dev`

---

## 🛠 Краткая справка
- **Frontend:** [http://localhost:5173](http://localhost:5173)
- **API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Отключение RAG** значительно ускоряет установку зависимостей и время старта бэкенда.

© 2026 MKY Team
