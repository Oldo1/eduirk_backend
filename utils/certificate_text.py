"""
Подстановка переменных в текст грамоты и подгонка шрифта под область (ReportLab).
"""
from __future__ import annotations

import re
from typing import List, Tuple

from reportlab.pdfbase import pdfmetrics

# Плейсхолдеры вида {ФИО}, {Дата}
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", "", s.strip().lower())


def apply_variables(text: str, variables: dict[str, str]) -> str:
    """
    Заменяет в тексте все вхождения {ИмяПеременной} на значения из variables.
    Регистр и пробелы в имени ключа при сопоставлении игнорируются.
    Неизвестные плейсхолдеры остаются без изменений.
    """
    if not text:
        return text
    exact: dict[str, str] = {}
    norm: dict[str, str] = {}
    for raw_k, raw_v in variables.items():
        if raw_v is None:
            continue
        v = str(raw_v)
        k = str(raw_k).strip()
        if not k:
            continue
        exact[k] = v
        exact[k.lower()] = v
        norm[_norm_key(k)] = v

    # Латинские плейсхолдеры в шаблоне ({fio}) ↔ кириллические ключи в variables
    if "фио" in norm:
        norm["fio"] = norm["фио"]
    if "мероприятие" in norm:
        norm["event"] = norm["мероприятие"]

    def replace_one(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if inner in exact:
            return exact[inner]
        nk = _norm_key(inner)
        if nk in norm:
            return norm[nk]
        return m.group(0)

    return _PLACEHOLDER_RE.sub(replace_one, text)


def merge_legacy_variables(
    variables: dict[str, str],
    fio: str | None,
    event_name: str | None,
) -> dict[str, str]:
    """Добавляет классические ключи ФИО / мероприятия для пакетной генерации."""
    out = dict(variables)
    if fio is not None and fio.strip():
        fv = fio.strip()
        out.setdefault("ФИО", fv)
        out.setdefault("fio", fv)
    if event_name is not None and event_name.strip():
        ev = event_name.strip()
        out.setdefault("Мероприятие", ev)
        out.setdefault("мероприятие", ev)
    return out


def _string_width(text: str, font_name: str, font_size: float) -> float:
    try:
        return pdfmetrics.stringWidth(text, font_name, font_size)
    except (KeyError, AttributeError):
        return pdfmetrics.stringWidth(text, "Helvetica", font_size)


def wrap_text_to_width(
    text: str,
    font_name: str,
    font_size: float,
    max_width_pt: float,
) -> List[str]:
    """Переносит текст по словам так, чтобы каждая строка умещалась в max_width_pt."""
    if not text.strip():
        return []
    lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        words = line.split()
        current: List[str] = []
        for w in words:
            trial = (" ".join(current + [w])).strip()
            if not current:
                current = [w]
                continue
            if _string_width(trial, font_name, font_size) <= max_width_pt:
                current.append(w)
            else:
                if _string_width(w, font_name, font_size) > max_width_pt:
                    # очень длинное «слово» — режем посимвольно
                    chunk = " ".join(current)
                    if chunk:
                        lines.append(chunk)
                    current = []
                    acc = ""
                    for ch in w:
                        t2 = acc + ch
                        if _string_width(t2, font_name, font_size) <= max_width_pt:
                            acc = t2
                        else:
                            if acc:
                                lines.append(acc)
                            acc = ch
                    current = [acc] if acc else []
                else:
                    lines.append(" ".join(current))
                    current = [w]
        if current:
            lines.append(" ".join(current))
    return lines


def auto_fit_text(
    text: str,
    font_name: str,
    max_width_pt: float,
    max_height_pt: float,
    max_font_size: float,
    min_font_size: float = 6.0,
    line_factor: float = 1.25,
) -> Tuple[float, List[str]]:
    """
    Подбирает размер шрифта и список строк, чтобы блок текста поместился
    в прямоугольник max_width_pt × max_height_pt (в пунктах).

    Возвращает (font_size, lines). Если даже при min_font_size не влезает —
    возвращает минимальный размер и максимально возможный перенос.
    """
    if max_width_pt <= 0 or max_height_pt <= 0:
        return min_font_size, []

    size = float(max_font_size)
    min_sz = float(min_font_size)

    while size >= min_sz:
        lines = wrap_text_to_width(text, font_name, size, max_width_pt)
        lh = size * line_factor
        height = len(lines) * lh if lines else lh
        if height <= max_height_pt:
            return size, lines
        size -= 0.5

    lines = wrap_text_to_width(text, font_name, min_sz, max_width_pt)
    return min_sz, lines


def estimate_text_box_height(num_lines: int, font_size: float, line_factor: float = 1.25) -> float:
    if num_lines <= 0:
        return font_size * line_factor
    return num_lines * font_size * line_factor
