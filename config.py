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
GIGACHAT_CREDENTIALS: str = os.environ.get("GIGACHAT_CREDENTIALS", "")

# ─────────────────────────────────────────────────────────────────────────────
#  Краулер сайта
# ─────────────────────────────────────────────────────────────────────────────

SITE_START_URL:    str   = os.environ.get("SITE_START_URL", "https://mc.eduirk.ru/")
SITE_MAX_PAGES:    int   = int(os.environ.get("SITE_MAX_PAGES", "2000"))
SITE_CRAWL_DELAY:  float = float(os.environ.get("SITE_CRAWL_DELAY", "0.5"))
SITE_USER_AGENT:   str   = "RAG-Updater/1.0"
SITE_MIN_TEXT_LEN: int   = 50    # страницы короче этого — пропускаем
SITE_CACHE_FILE:   str   = os.environ.get(
    "SITE_CACHE_FILE",
    "./chroma_gigachat/site_pages_cache.json",
)

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
S3_FILE_CACHE_DIR: str = os.environ.get(
    "S3_FILE_CACHE_DIR",
    "./s3_extracted/.cache/s3_documents",
)


def _env_set(name: str, default: str) -> frozenset[str]:
    return frozenset(
        item.strip().lower()
        for item in os.environ.get(name, default).split(",")
        if item.strip()
    )


def _env_tuple(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        item.strip().lower()
        for item in os.environ.get(name, default).split(",")
        if item.strip()
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Доступ к чат-боту
# ─────────────────────────────────────────────────────────────────────────────

ASSISTANT_EMPLOYEE_ROLE_NAMES: frozenset[str] = _env_set(
    "ASSISTANT_EMPLOYEE_ROLE_NAMES",
    "admin,administrator,employee,staff,manager,moderator,editor,"
    "админ,администратор,сотрудник,работник,модератор,редактор",
)

ASSISTANT_INTERNAL_S3_PREFIXES: tuple[str, ...] = _env_tuple(
    "ASSISTANT_INTERNAL_S3_PREFIXES",
    "internal/,private/,staff/,employee/,employees/,служебные/,внутренние/",
)

ASSISTANT_INTERNAL_S3_KEYWORDS: tuple[str, ...] = _env_tuple(
    "ASSISTANT_INTERNAL_S3_KEYWORDS",
    "internal,private,confidential,staff,employee,служебн,внутренн,конфиденц",
)

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
