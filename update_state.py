"""
update_state.py — хранилище состояния индексированных документов

Сохраняет в JSON-файл:
  pages:   url     → md5(text)   — для HTML-страниц сайта
  s3_docs: s3_key  → etag        — для файлов в Yandex Cloud S3

Используется для определения: новый документ, изменился или удалён.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import STATE_FILE

logger = logging.getLogger("update_state")


class UpdateState:
    def __init__(self, path: str = STATE_FILE):
        self._path    = Path(path)
        self._pages:   dict[str, str] = {}  # url   → md5(text)
        self._s3_docs: dict[str, str] = {}  # s3key → etag
        self._load()

    # ── Загрузка / сохранение ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            logger.info("[state] Файл состояния не найден — начинаем с чистого листа")
            return
        try:
            data          = json.loads(self._path.read_text(encoding="utf-8"))
            self._pages   = data.get("pages",   {})
            self._s3_docs = data.get("s3_docs", {})
            logger.info(
                f"[state] Загружено: {len(self._pages)} страниц, "
                f"{len(self._s3_docs)} S3-документов"
            )
        except Exception as e:
            logger.warning(f"[state] Ошибка загрузки, начинаем с нуля: {e}")

    def clear(self) -> None:
        """Очищает всё состояние (используется при полной переиндексации)."""
        self._pages.clear()
        self._s3_docs.clear()
        logger.info("[state] Состояние очищено")

    def save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(
                    {
                        "pages":      self._pages,
                        "s3_docs":    self._s3_docs,
                        "updated_at": datetime.now().isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.debug(f"[state] Сохранено в {self._path}")
        except Exception as e:
            logger.error(f"[state] Ошибка сохранения: {e}")

    # ── Страницы сайта ────────────────────────────────────────────────────────

    def get_page_hash(self, url: str) -> Optional[str]:
        return self._pages.get(url)

    def set_page_hash(self, url: str, h: str) -> None:
        self._pages[url] = h

    def remove_page(self, url: str) -> None:
        self._pages.pop(url, None)

    def all_page_urls(self) -> set[str]:
        return set(self._pages.keys())

    # ── S3-документы ──────────────────────────────────────────────────────────

    def get_s3_etag(self, key: str) -> Optional[str]:
        return self._s3_docs.get(key)

    def set_s3_etag(self, key: str, etag: str) -> None:
        self._s3_docs[key] = etag

    def remove_s3(self, key: str) -> None:
        self._s3_docs.pop(key, None)

    def all_s3_keys(self) -> set[str]:
        return set(self._s3_docs.keys())

    # ── Утилита ───────────────────────────────────────────────────────────────

    @staticmethod
    def compute_hash(text: str) -> str:
        """MD5 от текста — для сравнения содержимого страниц сайта."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()