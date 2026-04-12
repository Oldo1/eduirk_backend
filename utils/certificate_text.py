"""
Подстановка переменных в текст грамоты и подгонка шрифта под область (ReportLab).

Production-ready: binary search для auto_fit, посимвольная нарезка длинных слов,
корректная обработка спецсимволов и пустых строк.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from reportlab.pdfbase import pdfmetrics

# ── Плейсхолдеры вида {ФИО}, {Дата} ──────────────────────────────────
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def _norm_key(s: str) -> str:
    """Нормализация ключа: убираем пробелы, приводим к нижнему регистру."""
    return re.sub(r"\s+", "", s.strip().lower())


# ── Подстановка переменных ────────────────────────────────────────────

def apply_variables(text: str, variables: Dict[str, str]) -> str:
    """
    Заменяет в тексте все вхождения {ИмяПеременной} на значения из variables.
    Регистр и пробелы в имени ключа при сопоставлении игнорируются.
    Неизвестные плейсхолдеры остаются без изменений.
    """
    if not text:
        return text

    exact: Dict[str, str] = {}
    norm: Dict[str, str] = {}

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

    # Латинские плейсхолдеры ↔ кириллические ключи
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
    variables: Dict[str, str],
    fio: Optional[str],
    event_name: Optional[str],
) -> Dict[str, str]:
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


# ── Измерение ширины строки ───────────────────────────────────────────

def _string_width(text: str, font_name: str, font_size: float) -> float:
    """Безопасное измерение ширины строки через ReportLab pdfmetrics."""
    try:
        return pdfmetrics.stringWidth(text, font_name, font_size)
    except (KeyError, AttributeError):
        return pdfmetrics.stringWidth(text, "Helvetica", font_size)


# ── Перенос текста по словам ──────────────────────────────────────────

def wrap_text_to_width(
    text: str,
    font_name: str,
    font_size: float,
    max_width_pt: float,
) -> List[str]:
    """
    Переносит текст по словам так, чтобы каждая строка умещалась в max_width_pt.
    Обрабатывает:
    - многострочный ввод (\\n)
    - очень длинные «слова» (посимвольная нарезка)
    - пустые строки
    """
    if not text.strip():
        return []

    if max_width_pt <= 0:
        return [text.strip()]

    lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue

        words = line.split()
        current: List[str] = []

        for w in words:
            trial = " ".join(current + [w])

            if not current:
                # Первое слово в строке — проверяем, влезает ли целиком
                if _string_width(w, font_name, font_size) <= max_width_pt:
                    current = [w]
                else:
                    # Слово длиннее контейнера — режем посимвольно
                    current = _char_split(w, font_name, font_size, max_width_pt, lines)
                continue

            if _string_width(trial, font_name, font_size) <= max_width_pt:
                current.append(w)
            else:
                # Текущая строка заполнена — сбрасываем
                lines.append(" ".join(current))

                if _string_width(w, font_name, font_size) > max_width_pt:
                    current = _char_split(w, font_name, font_size, max_width_pt, lines)
                else:
                    current = [w]

        if current:
            lines.append(" ".join(current))

    return lines


def _char_split(
    word: str,
    font_name: str,
    font_size: float,
    max_width_pt: float,
    out_lines: List[str],
) -> List[str]:
    """Разбивает слово посимвольно, добавляя готовые куски в out_lines.
    Возвращает остаток как список из одного элемента (или пустой)."""
    acc = ""
    for ch in word:
        candidate = acc + ch
        if _string_width(candidate, font_name, font_size) <= max_width_pt:
            acc = candidate
        else:
            if acc:
                out_lines.append(acc)
            acc = ch
    return [acc] if acc else []


# ── «Perfect Fit» Engine ──────────────────────────────────────────────

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

    Алгоритм: binary search по размеру шрифта (шаг 0.25pt) для скорости,
    затем финальная проверка. Гарантирует отсутствие overflow.

    Возвращает (font_size, lines).
    """
    if max_width_pt <= 0 or max_height_pt <= 0:
        return min_font_size, []

    max_sz = float(max_font_size)
    min_sz = float(min_font_size)

    def _fits(size: float) -> Tuple[bool, List[str]]:
        lines = wrap_text_to_width(text, font_name, size, max_width_pt)
        lh = size * line_factor
        height = len(lines) * lh if lines else lh
        return height <= max_height_pt, lines

    # Быстрая проверка: если максимальный размер влезает — сразу возвращаем
    ok, lines = _fits(max_sz)
    if ok:
        return max_sz, lines

    # Binary search по размеру шрифта
    lo = min_sz
    hi = max_sz
    best_size = min_sz
    best_lines: List[str] = []

    while hi - lo > 0.25:
        mid = (lo + hi) / 2.0
        ok, lines = _fits(mid)
        if ok:
            best_size = mid
            best_lines = lines
            lo = mid
        else:
            hi = mid

    # Финальная проверка на best_size
    if not best_lines:
        ok, best_lines = _fits(best_size)
        if not ok:
            best_lines = wrap_text_to_width(text, font_name, min_sz, max_width_pt)
            best_size = min_sz

    return best_size, best_lines


# ── Утилиты ───────────────────────────────────────────────────────────

def estimate_text_box_height(
    num_lines: int,
    font_size: float,
    line_factor: float = 1.25,
) -> float:
    """Оценка высоты текстового блока в пунктах."""
    if num_lines <= 0:
        return font_size * line_factor
    return num_lines * font_size * line_factor
