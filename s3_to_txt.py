"""
s3_to_txt.py — скачивает файлы из Yandex Cloud и сохраняет извлечённый текст в .txt

Использует готовые модули:
    s3_loader.py     — листинг и скачивание из Yandex Cloud S3
    doc_extractor.py — извлечение текста из PDF / DOCX / DOC (с OCR)
    config.py        — настройки (ключи, бакет и т.д.)

Запуск:
    python s3_to_txt.py

Результат сохраняется в папку ./s3_extracted/
"""

from __future__ import annotations

import logging
from pathlib import Path

from s3_loader import list_documents, download_file, public_url
from doc_extractor import extract_text

# ─────────────────────────────────────────────────────────────────────────────
#  Настройки
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_DIR    = Path("./s3_extracted")   # папка для сохранения .txt файлов
MIN_TEXT_LEN  = 50                       # пропускать файлы с текстом короче этого

# ─────────────────────────────────────────────────────────────────────────────
#  Логирование
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("s3_to_txt")


# ─────────────────────────────────────────────────────────────────────────────
#  Основная логика
# ─────────────────────────────────────────────────────────────────────────────

def process_all() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Получаем список файлов из бакета (без скачивания)
    docs = list_documents()
    if not docs:
        logger.warning("Файлы в бакете не найдены. Проверьте настройки в config.py")
        return

    logger.info(f"Найдено файлов: {len(docs)}")

    ok = skipped = errors = 0

    for key, etag in docs.items():
        filename = Path(key).name
        out_path = OUTPUT_DIR / (Path(filename).stem + ".txt")

        logger.info(f"Обрабатываю: {filename}")

        # 2. Скачиваем файл
        file_bytes = download_file(key)
        if not file_bytes:
            logger.error(f"  ✗ Не удалось скачать: {filename}")
            errors += 1
            continue

        # 3. Извлекаем текст (PDF с OCR / DOCX / DOC)
        text = extract_text(file_bytes, filename)

        if len(text.strip()) < MIN_TEXT_LEN:
            logger.warning(f"  ⚠ Пустой текст после извлечения: {filename}")
            skipped += 1
            continue

        # 4. Сохраняем в .txt
        out_path.write_text(
            f"Источник:  {public_url(key)}\n"
            f"Файл:      {filename}\n"
            f"ETag:      {etag}\n"
            f"{'=' * 60}\n\n"
            f"{text}\n",
            encoding="utf-8",
        )

        logger.info(f"  ✓ Сохранено: {out_path}  ({len(text)} симв.)")
        ok += 1

    # Итог
    print()
    logger.info(f"{'═' * 50}")
    logger.info(f"Готово! Обработано: {ok}, пропущено: {skipped}, ошибок: {errors}")
    logger.info(f"Файлы сохранены в: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    process_all()
