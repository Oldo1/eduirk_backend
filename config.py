"""
config.py — единое место для всех настроек RAG-системы

Все остальные модули импортируют константы отсюда.
Переменные окружения имеют приоритет над значениями по умолчанию.
"""

from __future__ import annotations

import os

# ─────────────────────────────────────────────────────────────────────────────
#  Расписание обновлений
# ─────────────────────────────────────────────────────────────────────────────

UPDATE_INTERVAL_HOURS: float = float(os.environ.get("UPDATE_INTERVAL_HOURS", "24"))

# ─────────────────────────────────────────────────────────────────────────────
#  Краулер сайта
# ─────────────────────────────────────────────────────────────────────────────

SITE_START_URL:    str   = os.environ.get("SITE_START_URL", "https://mc.eduirk.ru/")
SITE_MAX_PAGES:    int   = int(os.environ.get("SITE_MAX_PAGES", "2000"))
SITE_CRAWL_DELAY:  float = float(os.environ.get("SITE_CRAWL_DELAY", "0.5"))
SITE_USER_AGENT:   str   = "RAG-Updater/1.0"
SITE_MIN_TEXT_LEN: int   = 50    # страницы короче этого — пропускаем

SITE_SKIP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "nav", "footer", "header", "aside", "noscript"}
)

# ─────────────────────────────────────────────────────────────────────────────
#  Yandex Cloud Object Storage
# ─────────────────────────────────────────────────────────────────────────────

YC_KEY_ID:    str = os.environ.get("YC_KEY_ID",    "")
YC_SECRET_KEY: str = os.environ.get("YC_SECRET_KEY", "")
YC_BUCKET:    str = os.environ.get("YC_BUCKET",    "eduirk")
YC_PREFIX:    str = os.environ.get("YC_PREFIX",    "")
YC_ENDPOINT:  str = "https://storage.yandexcloud.net"
YC_REGION:    str = "ru-central1"

SUPPORTED_DOC_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".doc"})

# ─────────────────────────────────────────────────────────────────────────────
#  OCR (Surya OCR)
# ─────────────────────────────────────────────────────────────────────────────
# Surya автоматически определяет язык — OCR_LANG оставлен для совместимости,
# но движком не используется.
OCR_LANG: str = os.environ.get("OCR_LANG", "rus+eng")
OCR_DPI:  int = int(os.environ.get("OCR_DPI", "192"))   # Surya ресайзит сама — 192 DPI хватает, выше только жрёт RAM на CPU

# Минимум символов на странице PDF — меньше считается сканом
PDF_MIN_PAGE_CHARS: int = 50

# ─────────────────────────────────────────────────────────────────────────────
#  Индексация (чанкинг)
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE:    int = int(os.environ.get("CHUNK_SIZE",    "300"))
CHUNK_OVERLAP: int = int(os.environ.get("CHUNK_OVERLAP", "50"))

# ─────────────────────────────────────────────────────────────────────────────
#  Хранилище состояния
# ─────────────────────────────────────────────────────────────────────────────

STATE_FILE: str = os.environ.get("STATE_FILE", "./update_state.json")
