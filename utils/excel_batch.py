"""Excel helpers for certificate batch generation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import List, Tuple

import pandas as pd

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


@dataclass(frozen=True)
class ExcelRowsResult:
    headers: List[str]
    rows: List[dict[str, str]]
    fio_column: str | None
    row_count: int


def _normalize_header(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", "", str(value).strip().lower())


def _clean_header(value: object, index: int) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return f"Колонка {index + 1}"
    text = str(value).strip()
    return text or f"Колонка {index + 1}"


def _cell_to_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        try:
            number = float(text)
            if number.is_integer():
                return str(int(number))
        except ValueError:
            pass
    return text


def find_fio_column(df: pd.DataFrame) -> str:
    """Return the FIO column name or raise ValueError."""
    if df.empty or len(df.columns) == 0:
        raise ValueError("Файл не содержит данные или заголовки столбцов")

    for col in df.columns:
        if _normalize_header(col) in _FIO_HEADER_ALIASES:
            return col

    raise ValueError(
        "Не найден столбец с ФИО. Ожидается заголовок вроде «ФИО», «FIO» или «Участник»."
    )


def read_rows_from_excel(content: bytes) -> ExcelRowsResult:
    """Read the first Excel sheet as dynamic row variables."""
    try:
        raw_df = pd.read_excel(BytesIO(content), engine="openpyxl", dtype=object)
    except Exception as e:
        raise ValueError(
            "Не удалось прочитать Excel. Убедитесь, что файл в формате .xlsx и не повреждён."
        ) from e

    if raw_df.empty and len(raw_df.columns) == 0:
        raise ValueError("Файл не содержит данные или заголовки столбцов")

    headers = [_clean_header(col, i) for i, col in enumerate(raw_df.columns)]
    df = raw_df.copy()
    df.columns = headers

    fio_column = None
    for col in headers:
        if _normalize_header(col) in _FIO_HEADER_ALIASES:
            fio_column = col
            break

    rows: List[dict[str, str]] = []
    for _, raw_row in df.iterrows():
        row = {header: _cell_to_text(raw_row.get(header)) for header in headers}
        if any(row.values()):
            rows.append(row)

    return ExcelRowsResult(
        headers=headers,
        rows=rows,
        fio_column=fio_column,
        row_count=len(rows),
    )


def read_fio_list_from_excel(content: bytes) -> Tuple[List[str], str]:
    """Backward-compatible reader that returns only non-empty FIO values."""
    excel = read_rows_from_excel(content)
    if not excel.fio_column:
        raise ValueError(
            "Не найден столбец с ФИО. Ожидается заголовок вроде «ФИО», «FIO» или «Участник»."
        )

    names = [
        row.get(excel.fio_column, "").strip()
        for row in excel.rows
        if row.get(excel.fio_column, "").strip()
    ]
    return names, excel.fio_column


def sanitize_zip_entry_basename(name: str, max_len: int = 100) -> str:
    """Safe ZIP entry basename without path separators."""
    name = name.strip().replace("\n", " ").replace("\r", " ")
    for ch in '<>:"/\\|?*\x00':
        name = name.replace(ch, "_")
    name = name.strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "certificate"


def assign_unique_pdf_names(fio_list: List[str]) -> List[str]:
    """Return unique PDF names based on FIO values."""
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
