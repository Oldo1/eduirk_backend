#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  if [ -z "${DB_HOST:-}" ] || [ "${DB_HOST}" = "localhost" ] || [ "${DB_HOST}" = "127.0.0.1" ]; then
    echo "ERROR: DATABASE_URL is not set."
    echo "Set DATABASE_URL in Render Environment to the Supabase connection string."
    echo "Example: postgresql+psycopg2://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require"
    exit 1
  fi
fi

python -m alembic upgrade head
export RUN_STARTUP_SCHEMA_PATCHES="${RUN_STARTUP_SCHEMA_PATCHES:-0}"
export ENABLE_RAG_WARMUP="${ENABLE_RAG_WARMUP:-0}"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
