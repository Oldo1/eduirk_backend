"""
ocr_engine.py — OCR-движок на базе Surya OCR

Surya — современная deep-learning OCR (2024-2025), основана на трансформере,
обучена на документах. Заметно точнее EasyOCR на русском, хорошо держит
таблицы, низкий контраст, наклон и рукописные пометки.

Детектор текста работает для любых языков; распознаватель — многоязычный
и автоматически определяет язык строки.

Ленивая инициализация: модели скачиваются при первом вызове (~1.3 ГБ,
кэшируются в LOCALAPPDATA/datalab/datalab/Cache/models/). При обрыве
скачивания (антивирус/firewall режут длинные соединения) достаточно
перезапустить — Surya докачает с места обрыва.

Зависимости:
    pip install surya-ocr pymupdf pillow
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

# Surya по умолчанию пытается запускать subprocess-воркеров. На Windows
# это часто падает молча (spawn-семантика + отсутствие __main__ guard в
# библиотечном коде). IN_STREAMLIT=true заставляет Surya работать в одном
# процессе. Batch-size контролируем отдельно — на CPU 4 страницы за раз
# оптимально по соотношению скорость/память.
os.environ.setdefault("IN_STREAMLIT", "true")
os.environ.setdefault("RECOGNITION_BATCH_SIZE", "8")
os.environ.setdefault("DETECTOR_BATCH_SIZE", "4")

from config import OCR_DPI

logger = logging.getLogger("ocr_engine")

_ocr_available:        Optional[bool] = None
_detection_predictor   = None   # surya.detection.DetectionPredictor
_recognition_predictor = None   # surya.recognition.RecognitionPredictor


def is_ocr_available() -> bool:
    """
    Ленивая инициализация предикторов Surya. Первый вызов скачивает модели
    (~1.3 ГБ) — может занять несколько минут. Последующие — мгновенно.
    Результат кэшируется.
    """
    global _ocr_available, _detection_predictor, _recognition_predictor
    if _ocr_available is not None:
        return _ocr_available

    try:
        import fitz                                       # noqa: F401
        from surya.foundation   import FoundationPredictor
        from surya.detection    import DetectionPredictor
        from surya.recognition  import RecognitionPredictor
    except ImportError as e:
        logger.warning(f"[ocr] Библиотека не установлена: {e}")
        logger.warning("[ocr] Для OCR: pip install surya-ocr pymupdf pillow")
        _ocr_available = False
        return False

    try:
        logger.info(
            f"[ocr] Загружаю Surya OCR (DPI: {OCR_DPI}). "
            f"Первый запуск может занять несколько минут — скачиваются модели (~1.3 ГБ). "
            f"При обрыве соединения — просто перезапустите, докачка продолжится."
        )

        foundation = FoundationPredictor()
        _recognition_predictor = RecognitionPredictor(foundation)
        _detection_predictor   = DetectionPredictor()

        logger.info("[ocr] Surya OCR готов.")
        _ocr_available = True
    except Exception as e:
        logger.warning(f"[ocr] Не удалось инициализировать Surya OCR: {e}")
        _ocr_available = False

    return _ocr_available


def _render_pdf_page_to_pil(pdf_bytes: bytes, page_index: int):
    """
    Рендерит страницу PDF в PIL.Image (RGB). Surya принимает PIL напрямую.
    """
    import fitz
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_index >= len(doc):
            logger.warning(
                f"[ocr] Индекс страницы {page_index} вне диапазона ({len(doc)} стр.)"
            )
            return None

        page   = doc[page_index]
        zoom   = OCR_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        pix    = page.get_pixmap(matrix=matrix, alpha=False)

        mode = "RGB" if pix.n >= 3 else "L"
        img  = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        if mode == "L":
            img = img.convert("RGB")
        return img
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Постобработка
# ─────────────────────────────────────────────────────────────────────────────

_NUMERO_RE = re.compile(r'\bN[gesop°²0o][.\s]*(?=\d)', flags=re.IGNORECASE)

_EMAIL_HINT_RE = re.compile(
    r'(?:E[-\s]?[mn]ail|e[-\s]?mail|почта)\s*[:\-]?\s*'
    r'([A-Za-z0-9_.\-]+)\s+([A-Za-z0-9_.\-]+\.[A-Za-z]{2,})',
    flags=re.IGNORECASE,
)


def _postprocess_text(text: str) -> str:
    text = _NUMERO_RE.sub('№ ', text)
    text = _EMAIL_HINT_RE.sub(lambda m: f"E-mail: {m.group(1)}@{m.group(2)}", text)
    text = re.sub(r' {2,}', ' ', text)
    return text


def _prediction_to_text(prediction) -> str:
    """
    Превращает один объект OCRResult (на одну страницу) в текст.
    """
    lines = [
        line.text.strip()
        for line in getattr(prediction, "text_lines", [])
        if getattr(line, "text", "").strip()
    ]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _postprocess_text(text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Публичный API
# ─────────────────────────────────────────────────────────────────────────────

def ocr_pdf_page(pdf_bytes: bytes, page_index: int) -> str:
    """
    Распознаёт текст на одной странице PDF. Обёртка над ocr_pdf_pages
    для обратной совместимости.
    """
    result = ocr_pdf_pages(pdf_bytes, [page_index])
    return result.get(page_index, "")


def ocr_pdf_pages(pdf_bytes: bytes, page_indices: list[int]) -> dict[int, str]:
    """
    Батчевая OCR сразу нескольких страниц PDF одним вызовом Surya.
    Заметно быстрее, чем вызывать ocr_pdf_page в цикле — Surya паралеллит
    детекцию/распознавание внутри одного батча.

    Args:
        pdf_bytes:    содержимое PDF
        page_indices: номера страниц (0-based), которые нужно распознать.
                      Обычно — только страницы-сканы, где нативный текст пуст.

    Returns:
        Словарь {page_index: text}. Отсутствующие / ошибочные страницы
        не попадают в результат.
    """
    if not page_indices or not is_ocr_available():
        return {}

    try:
        # 1. Рендерим все запрошенные страницы в PIL-изображения
        images: list = []
        valid_indices: list[int] = []
        for idx in page_indices:
            img = _render_pdf_page_to_pil(pdf_bytes, idx)
            if img is not None:
                images.append(img)
                valid_indices.append(idx)

        if not images:
            return {}

        # 2. Один батчевый вызов — Surya сама разобьёт по RECOGNITION_BATCH_SIZE
        logger.info(f"[ocr] Батч-распознавание: {len(images)} страниц за один вызов")
        predictions = _recognition_predictor(
            images,
            det_predictor=_detection_predictor,
        )

        # 3. Собираем результат
        out: dict[int, str] = {}
        for idx, pred in zip(valid_indices, predictions):
            text = _prediction_to_text(pred)
            if text:
                out[idx] = text

        return out

    except Exception as e:
        logger.error(
            f"[ocr] Ошибка батчевого распознавания (страниц: {len(page_indices)}): {e}",
            exc_info=True,
        )
        return {}
