# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=5 \
    HF_HOME=/cache/huggingface \
    TRANSFORMERS_CACHE=/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/cache/huggingface

WORKDIR /app

# Системные зависимости:
#  - libgl1, libglib2.0-0  — нужны для PIL/opencv внутри Surya OCR
#  - poppler-utils         — для рендера PDF, на случай fallback'ов
#  - curl                  — для healthcheck
#  - libgomp1              — OpenMP runtime для torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        poppler-utils \
        curl \
    && rm -rf /var/lib/apt/lists/*

# torch CPU-версией ставим ПЕРВОЙ — иначе sentence-transformers/surya
# притянут CUDA-сборку (~3 ГБ лишних в образе)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Каталоги, которые код ожидает увидеть; они же будут точками монтирования
RUN mkdir -p /app/chroma_gigachat \
             /app/ocr_cache \
             /app/s3_extracted \
             /app/static/certificates/backgrounds \
             /app/static/certificates/facsimiles \
             /app/static/certificates/generated \
             /cache/huggingface

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8000/admin/update/status || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
