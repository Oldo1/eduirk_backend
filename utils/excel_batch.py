"""Чтение списка ФИО из Excel для пакетной генерации грамот."""
from __future__ import annotations

import re
from io import BytesIO
from typing import List, Tuple

import pandas as pd

# Синонимы заголовка столбца с ФИО (после нормализации)
_FIO_HEADER_ALIASES = frozenset(
    {
        "фио",
        "fio",
        "full_name",
        "fullname",
        "полноеимя",
        "полноефио",
        "фамилияимяотчество",
        "name",
        "участник",
    }
)


def _normalize_header(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", "", s)
    return s


def find_fio_column(df: pd.DataFrame) -> str:
    """Возвращает имя столбца с ФИО или бросает ValueError."""
    if df.empty or len(df.columns) == 0:
        raise ValueError("Файл не содержит данных или заголовков столбцов")

    for col in df.columns:
        if _normalize_header(col) in _FIO_HEADER_ALIASES:
            return col

    raise ValueError(
        "Не найден столбец с ФИО. Ожидается заголовок вроде «ФИО», «FIO» или «Участник»."
    )


def read_fio_list_from_excel(content: bytes) -> Tuple[List[str], str]:
    """
    Читает первый лист Excel, находит столбец ФИО, возвращает список непустых строк
    и имя использованного столбца.
    """
    try:
        df = pd.read_excel(BytesIO(content), engine="openpyxl")
    except Exception as e:
        raise ValueError(
            "Не удалось прочитать Excel. Убедитесь, что файл в формате .xlsx и не повреждён."
        ) from e

    col = find_fio_column(df)
    series = df[col]

    result: List[str] = []
    for raw in series:
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        text = str(raw).strip()
        if not text:
            continue
        # дубликаты подряд можно оставить — для грамот нужны все строки;
        # пустые уже отфильтрованы
        result.append(text)

    return result, col


def sanitize_zip_entry_basename(name: str, max_len: int = 100) -> str:
    """Безопасное имя файла для записи в ZIP (без путей)."""
    name = name.strip().replace("\n", " ").replace("\r", " ")
    for ch in '<>:"/\\|?*\x00':
        name = name.replace(ch, "_")
    name = name.strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "certificate"


def assign_unique_pdf_names(fio_list: List[str]) -> List[str]:
    """Имена вида ИвановИИ.pdf, при коллизии — ИвановИИ_2.pdf."""
    counts: dict[str, int] = {}
    out: List[str] = []
    for fio in fio_list:
        base = sanitize_zip_entry_basename(fio)
        n = counts.get(base, 0) + 1
        counts[base] = n
        if n == 1:
            out.append(f"{base}.pdf")
        else:
            out.append(f"{base}_{n}.pdf")
    return out
