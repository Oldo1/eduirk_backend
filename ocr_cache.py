"""
ocr_cache.py — файловый кэш результатов извлечения текста из документов.

Ключ — SHA-256 от байтов файла. Значение — извлечённый текст.
Кэш кладётся в ./ocr_cache/<sha>.txt.

Зачем:
    Surya OCR на CPU — 3-10 сек на страницу. При повторной переиндексации
    одних и тех же документов это дикая трата времени. Кэш делает второй
    и последующие запуски мгновенными.

    Ключ по хешу содержимого, а не по имени: если файл реально изменился —
    хеш другой, OCR запустится заново; если имя поменяли а содержимое нет —
    берём из кэша.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ocr_cache")

CACHE_DIR = Path("./ocr_cache")


def _cache_path(file_bytes: bytes) -> Path:
    sha = hashlib.sha256(file_bytes).hexdigest()
    return CACHE_DIR / f"{sha}.txt"


def get_cached(file_bytes: bytes) -> Optional[str]:
    """Возвращает закэшированный текст или None."""
    path = _cache_path(file_bytes)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        logger.info(f"[cache] Hit: {path.name[:12]}…  ({len(text)} симв.)")
        return text
    except Exception as e:
        logger.warning(f"[cache] Не удалось прочитать {path}: {e}")
        return None


def save_cached(file_bytes: bytes, text: str) -> None:
    """Сохраняет текст в кэш. Ошибки записи не пробрасываем."""
    if not text or not text.strip():
        return   # пустой текст не кэшируем — повторим попытку в будущем
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(file_bytes)
        path.write_text(text, encoding="utf-8")
        logger.info(f"[cache] Saved: {path.name[:12]}…  ({len(text)} симв.)")
    except Exception as e:
        logger.warning(f"[cache] Не удалось записать кэш: {e}")
