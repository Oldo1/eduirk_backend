# Backend MKY / EduIrk

FastAPI backend for the municipal education portal. The backend covers authentication, server-side roles, articles and news administration, Dom Uchitelya sections, TPMPK appointments and audit tools, certificate templates, and certificate generation.

This protected branch does not include heavy backend RAG/search/assistant code. It starts without Chroma, vector stores, embeddings, GigaChat, LangChain, or any special RAG toggle. The frontend keeps only a lightweight demo chatbot UI that does not call backend assistant endpoints.

## Main Features

- JWT authentication and role-based access control.
- Admin, methodist, Dom Uchitelya editor, TPMPK operator, and regular user roles.
- Article/news editor with server-side ownership and scope checks.
- TPMPK public appointment form with duplicate protection.
- TPMPK admin dashboard and action log.
- Certificate templates, template constructor API, and PDF generation.
- Local `/api/search/` page suggestion endpoint for site navigation.

## Environment

Create `.env` from `.env.example` and change placeholder secrets:

```bash
cp .env.example .env
```

Important variables:

- `DATABASE_URL` or `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`
- `SECRET_KEY`
- `PD_ENCRYPTION_KEY`
- `ENABLE_DEV_TEST_USERS=true` for local demo accounts
- `ADMIN_EMAIL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_ROLE`

Do not commit `.env`, local databases, dumps, logs, caches, generated certificates, or uploaded test files.

## Local Run

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python create_admin.py
uvicorn main:app --reload
```

Open API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

For local demonstration you can also use seeded test accounts by keeping `ENABLE_DEV_TEST_USERS=true` in `.env`.

## Test Users

When dev seed is enabled, these accounts are available:

- `admin@example.local` / `admin123` / `admin`
- `methodist@example.local` / `methodist123` / `methodist`
- `domu@example.local` / `domu123` / `domu_editor`
- `operator@example.local` / `operator123` / `operator`
- `user@example.local` / `user123` / `user`

## Tests

```bash
pytest -q
pytest -q tests/test_security_roles.py tests/test_tpmpk_api.py tests/test_dom_uchitelya_api.py
pytest -q tests/test_template_full_api.py tests/test_certificate_variables.py tests/test_auth_roles.py
```

## Docker

From this directory:

```bash
docker compose config
docker compose up --build
```

From the project root, use the root `docker-compose.yml` to start backend, frontend, and PostgreSQL together.

## Notes About RAG / Chatbot

Backend RAG/assistant is intentionally removed from this branch. There are no backend `/assistant`, `/api/assistant`, `/rag`, Chroma, vector store, embeddings, LangChain, or GigaChat requirements.

The frontend chatbot is a demonstration interface only. It answers with static navigation hints and clearly states that the intelligent assistant is not connected in this protected version.
