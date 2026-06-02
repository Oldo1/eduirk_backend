"""
Генерация PDF грамоты: фон на весь лист, текст с полями и auto-fit, блок подписантов.

Координатная система ReportLab: (0, 0) — левый НИЖНИЙ угол страницы.
Все y-координаты считаются от низа: y_pt = page_h - y_from_top_pt.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from utils.certificate_text import apply_variables, auto_fit_text, wrap_text_to_width

logger = logging.getLogger(__name__)

MM_TO_PT = 2.83465
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

_SIGN_LEFT_FRAC = 0.38
_SIGN_MID_FRAC = 0.24
_SIGN_RIGHT_FRAC = 0.38

# Допустимое отклонение пропорций изображения от A4 для «точного» покрытия (без crop)
_A4_ASPECT = PAGE_H_MM / PAGE_W_MM  # ≈ 1.4142
_ASPECT_TOLERANCE = 0.04            # ±4% — считаем изображение «A4-совместимым»


@dataclass(frozen=True)
class TextStyleFlags:
    bold: bool
    italic: bool
    underline: bool
def register_fonts() -> bool:
    ok = False
    regular = os.path.join("static", "fonts", "DejaVuSans.ttf")
    if os.path.exists(regular):
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", regular))
            ok = True
        except Exception as e:
            logger.warning("Ошибка регистрации DejaVu: %s", e)
    bold = os.path.join("static", "fonts", "DejaVuSans-Bold.ttf")
    if os.path.exists(bold):
        try:
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
        except Exception as e:
            logger.warning("Ошибка регистрации DejaVu-Bold: %s", e)
    return ok


register_fonts()


def _font_family_from_filename(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"^\d{8}_\d{6}_", "", stem)
    family = re.sub(r"[_-]+", " ", stem).strip()
    return family or "Custom font"


def _registered_custom_font_name(family: str) -> str:
    return "CustomFont_" + re.sub(r"[^A-Za-z0-9_]+", "_", family).strip("_")


def _find_custom_font_path(family: str) -> Optional[str]:
    wanted = str(family or "").strip().lower()
    if not wanted or wanted in {"dejavu", "dejavu sans", "helvetica"}:
        return None
    upload_dir = os.path.join("static", "fonts", "custom")
    if not os.path.isdir(upload_dir):
        return None
    matches: list[str] = []
    for filename in os.listdir(upload_dir):
        if os.path.splitext(filename)[1].lower() not in {".ttf", ".otf"}:
            continue
        if _font_family_from_filename(filename).lower() == wanted:
            matches.append(os.path.join(upload_dir, filename))
    return sorted(matches)[-1] if matches else None


def _register_custom_font(family: str) -> Optional[str]:
    family = str(family or "").strip()
    if not family:
        return None
    font_name = _registered_custom_font_name(family)
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    path = _find_custom_font_path(family)
    if not path:
        return None
    try:
        pdfmetrics.registerFont(TTFont(font_name, path))
        return font_name
    except Exception as e:
        logger.warning("Ошибка регистрации пользовательского шрифта '%s': %s", family, e)
        return None


def _canvas_font_name() -> str:
    if "DejaVu" in pdfmetrics.getRegisteredFontNames():
        return "DejaVu"
    return "Helvetica"


def _get_font_for_weight(weight_str: Optional[str]) -> str:
    try:
        w = int(float(weight_str or 400))
    except (TypeError, ValueError):
        w = 400
    if w >= 600 and "DejaVu-Bold" in pdfmetrics.getRegisteredFontNames():
        return "DejaVu-Bold"
    return _canvas_font_name()


def _get_font_for_family_and_weight(family: Optional[str], weight_str: Optional[str]) -> str:
    family_clean = str(family or "").strip()
    custom_font = _register_custom_font(family_clean)
    if custom_font:
        return custom_font
    return _get_font_for_weight(weight_str)


def _parse_fill_color(hex_str: Optional[str]) -> Any:
    if not hex_str or not str(hex_str).strip():
        return colors.HexColor("#000000")
    s = str(hex_str).strip()
    if not s.startswith("#"):
        s = "#" + s
    try:
        return colors.HexColor(s)
    except Exception:
        return colors.HexColor("#1e293b")


def _resolve_static_path(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    path = url.lstrip("/")
    return path if os.path.exists(path) else None


def _element_type(el: Any) -> str:
    return str(getattr(el, "element_type", None) or getattr(el, "type", None) or "text").strip().lower() or "text"


def _ordered_visible_elements(elements: Sequence[Any]) -> list[Any]:
    def safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def sort_key(item: Any) -> tuple[int, int]:
        z_value = getattr(item, "z_index", None)
        if z_value is None:
            z_value = getattr(item, "zIndex", None)
        if z_value is None:
            z_value = safe_int(getattr(item, "id", 0), 0)
        return safe_int(z_value, 0), safe_int(getattr(item, "id", 0), 0)

    return sorted(
        [item for item in elements if not bool(getattr(item, "hidden", False))],
        key=sort_key,
    )


def _text_style_flags(el: Any) -> TextStyleFlags:
    try:
        weight = int(float(getattr(el, "font_weight", None) or getattr(el, "weight", None) or 400))
    except (TypeError, ValueError):
        weight = 400
    return TextStyleFlags(
        bold=weight >= 600,
        italic=bool(getattr(el, "italic", False)),
        underline=bool(getattr(el, "underline", False)),
    )


def _element_opacity(el: Any) -> float:
    try:
        value = float(getattr(el, "opacity", 1) if getattr(el, "opacity", None) is not None else 1)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, value))


def _set_canvas_alpha(c: canvas.Canvas, alpha: float) -> None:
    if alpha >= 1:
        return
    for method_name in ("setFillAlpha", "setStrokeAlpha"):
        method = getattr(c, method_name, None)
        if callable(method):
            try:
                method(alpha)
            except Exception:
                logger.debug("ReportLab alpha method %s is unavailable", method_name)


def _line_width_pt(el: Any) -> float:
    raw_height = getattr(el, "height_mm", None)
    if raw_height is None:
        raw_height = getattr(el, "max_height_mm", None)
    try:
        return max(0.5, float(raw_height or 0.35) * MM_TO_PT)
    except (TypeError, ValueError):
        return 1.0


def _box_size_mm(el: Any, default_w: float, default_h: float) -> tuple[float, float]:
    width = getattr(el, "width_mm", None)
    height = getattr(el, "height_mm", None)
    if width is None:
        width = getattr(el, "max_width_mm", None)
    if height is None:
        height = getattr(el, "max_height_mm", None)
    try:
        width_value = max(0.1, float(width if width is not None else default_w))
    except (TypeError, ValueError):
        width_value = default_w
    try:
        height_value = max(0.1, float(height if height is not None else default_h))
    except (TypeError, ValueError):
        height_value = default_h
    return width_value, height_value


def draw_background_cover(
    c: canvas.Canvas,
    bg_path: str,
    page_w: float,
    page_h: float,
    bg_reader: Optional[Any] = None,
) -> None:
    """
    Full-bleed отрисовка фона: изображение покрывает весь лист без белых полей.

    Алгоритм:
    1. Сначала заливаем весь Canvas белым (страховка от субпиксельных щелей).
    2. Если пропорции изображения близки к A4 (±4%) — растягиваем точно в (0,0)→(page_w, page_h).
    3. Иначе — cover-масштабирование с центрированием (обрезка краёв).

    Координаты ReportLab: (0,0) = левый нижний угол.
    """
    # Страховочная белая заливка — убирает любые субпиксельные щели
    c.saveState()
    c.setFillColor(colors.white)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    c.restoreState()

    try:
        ir = bg_reader or ImageReader(bg_path)
        iw, ih = ir.getSize()
        if iw <= 0 or ih <= 0:
            logger.warning("Фон '%s': нулевые размеры изображения, пропускаем.", bg_path)
            return

        img_aspect = ih / iw
        page_aspect = page_h / page_w

        if abs(img_aspect - page_aspect) / page_aspect <= _ASPECT_TOLERANCE:
            # Пропорции близки к A4 — точное покрытие без crop
            c.drawImage(ir, 0, 0, width=page_w, height=page_h,
                        preserveAspectRatio=False, mask="auto")
        else:
            # Cover-масштабирование: заполняем весь лист, обрезая лишнее
            scale = max(page_w / iw, page_h / ih)
            dw, dh = iw * scale, ih * scale
            x0 = (page_w - dw) / 2
            y0 = (page_h - dh) / 2
            c.drawImage(ir, x0, y0, width=dw, height=dh,
                        preserveAspectRatio=False, mask="auto")

    except FileNotFoundError:
        logger.error("Файл фона не найден: '%s'", bg_path)
    except Exception as e:
        logger.error("Ошибка отрисовки фона '%s': %s", bg_path, e)


def _margins_mm(template: Any) -> Tuple[float, float, float, float]:
    ml = float(getattr(template, "margin_left_mm", 12) or 12)
    mr = float(getattr(template, "margin_right_mm", 12) or 12)
    mt = float(getattr(template, "margin_top_mm", 12) or 12)
    mb = float(getattr(template, "margin_bottom_mm", 12) or 12)
    ml = max(0.0, min(ml, 100.0))
    mr = max(0.0, min(mr, 100.0))
    mt = max(0.0, min(mt, 140.0))
    mb = max(0.0, min(mb, 140.0))
    if ml + mr >= PAGE_W_MM - 5:
        ml, mr = 12.0, 12.0
    if mt + mb >= PAGE_H_MM - 5:
        mt, mb = 12.0, 12.0
    return ml, mr, mt, mb


def _clamp_xy_mm(
    x_mm: float, y_mm: float, ml: float, mr: float, mt: float, mb: float
) -> Tuple[float, float]:
    pad = 0.25
    xl = ml + pad
    xr = PAGE_W_MM - mr - pad
    yt = mt + pad
    yb = PAGE_H_MM - mb - pad
    return min(max(x_mm, xl), xr), min(max(y_mm, yt), yb)


def _default_max_width_mm(x_mm: float, align: str, ml: float, mr: float) -> float:
    inner_l = ml
    inner_r = PAGE_W_MM - mr
    pad = 2.0
    if align == "center":
        return max(12.0, 2 * min(x_mm - inner_l - pad, inner_r - x_mm - pad))
    if align == "left":
        return max(12.0, inner_r - x_mm - pad)
    if align == "right":
        return max(12.0, x_mm - inner_l - pad)
    return max(12.0, inner_r - inner_l - 2 * pad)


def _max_text_height_mm(y_mm_from_top: float, mb: float, font_size: int) -> float:
    """Высота области вниз от якоря до нижнего поля (мм)."""
    safe_bottom_y = PAGE_H_MM - mb
    avail = safe_bottom_y - y_mm_from_top - 2.0
    cap = max(font_size, 8) * 0.4 * 14
    return max(14.0, min(220.0, avail, cap))


def _parse_element_color(el: Any) -> Any:
    """Читает цвет текстового элемента (поле color или text_color), fallback — чёрный."""
    raw = getattr(el, "color", None) or getattr(el, "text_color", None)
    if raw:
        return _parse_fill_color(str(raw))
    return colors.black


def _draw_styled_string(
    c: canvas.Canvas,
    text: str,
    x_pt: float,
    y_pt: float,
    font_name: str,
    font_size: float,
    align: str,
    style: TextStyleFlags,
) -> None:
    width = pdfmetrics.stringWidth(text, font_name, font_size)
    if align == "center":
        draw_x = x_pt - width / 2
    elif align == "right":
        draw_x = x_pt - width
    else:
        draw_x = x_pt

    offsets = [(0.0, 0.0)]
    if style.bold:
        offsets.extend([(0.32, 0.0), (0.0, 0.24)])

    for ox, oy in offsets:
        if style.italic:
            c.saveState()
            c.translate(draw_x + ox, y_pt + oy)
            c.skew(10, 0)
            c.drawString(0, 0, text)
            c.restoreState()
        else:
            c.drawString(draw_x + ox, y_pt + oy, text)

    if style.underline:
        underline_y = y_pt - max(1.0, font_size * 0.12)
        c.setLineWidth(max(0.45, font_size * 0.055))
        c.line(draw_x, underline_y, draw_x + width, underline_y)


def _new_layout_anchor(el: Any) -> bool:
    return str(getattr(el, "anchor", "") or "").strip().lower() == "center"


def draw_text_elements(
    c: canvas.Canvas,
    elements: Sequence[Any],
    variables: dict[str, str],
    page_h: float,
    font_name: str,
    template: Any,
) -> None:
    """
    Отрисовка текстовых блоков с auto-fit шрифта и поддержкой цвета.

    Координатная модель:
    - anchor="center" (элементы из конструктора): x_mm/y_mm — ЦЕНТР блока.
      Frontend рендерит через CSS: left: X%; top: Y%; transform: translate(-50%, -50%).
      PDF должен воспроизвести ту же логику.
    - Без anchor (legacy): y_mm — верхний край текста (от верха страницы).
    """
    ml, mr, mt, mb = _margins_mm(template)

    for el in _ordered_visible_elements(elements):
        if _element_type(el) not in {"text", "variable"}:
            continue
        raw = el.text or ""
        text = apply_variables(raw, variables)
        if not str(text).strip():
            continue

        is_center_anchor = _new_layout_anchor(el)
        align = getattr(el, "align", "center") or "center"
        x_mm = float(el.x_mm)
        y_mm = float(el.y_mm)

        # Для center-anchor элементов НЕ зажимаем координаты — frontend уже это делает.
        # Повторное зажатие смещает элементы от их реальной позиции.
        if not is_center_anchor:
            x_mm, y_mm = _clamp_xy_mm(x_mm, y_mm, ml, mr, mt, mb)

        x_pt = x_mm * MM_TO_PT

        # Ширина блока: если фронт задал явно — используем как есть,
        # не ограничивая _default_max_width_mm (фронт сам контролирует).
        max_w_mm = getattr(el, "width_mm", None)
        if max_w_mm is None:
            max_w_mm = getattr(el, "max_width_mm", None)
        if max_w_mm is None:
            max_w_mm = _default_max_width_mm(x_mm, align, ml, mr)
        else:
            max_w_mm = float(max_w_mm)
            if not is_center_anchor:
                max_w_mm = min(max_w_mm, _default_max_width_mm(x_mm, align, ml, mr))
        max_w_pt = float(max_w_mm) * MM_TO_PT

        fs = int(el.font_size or 24)
        max_h_mm = getattr(el, "height_mm", None)
        if max_h_mm is None:
            max_h_mm = getattr(el, "max_height_mm", None)
        if max_h_mm is None:
            max_h_mm = _max_text_height_mm(y_mm, mb, fs)
        else:
            max_h_mm = float(max_h_mm)
            if not is_center_anchor:
                max_h_mm = min(max_h_mm, _max_text_height_mm(y_mm, mb, fs))
        max_h_pt = float(max_h_mm) * MM_TO_PT

        base_size = float(el.font_size or 24)

        element_font = _get_font_for_family_and_weight(
            getattr(el, "font_family", None),
            getattr(el, "font_weight", "400"),
        )

        try:
            size, lines = auto_fit_text(
                text,
                element_font,
                max_w_pt,
                max_h_pt,
                max_font_size=base_size,
                min_font_size=6.0,
                line_factor=float(getattr(el, "line_height", None) or 1.25),
            )
        except Exception as e:
            logger.error("auto_fit_text ошибка для элемента id=%s: %s", getattr(el, "id", "?"), e)
            size, lines = base_size, [text]

        c.saveState()
        c.setFont(element_font, size)
        c.setFillColor(_parse_element_color(el))
        c.setStrokeColor(_parse_element_color(el))
        _set_canvas_alpha(c, _element_opacity(el))
        line_factor = float(getattr(el, "line_height", None) or 1.25)
        lh = size * line_factor

        if is_center_anchor:
            # y_mm — вертикальный ЦЕНТР текстового блока (как в CSS translate(-50%,-50%)).
            # Вычисляем фактическую высоту текстового блока после auto-fit.
            num_lines = len(lines) if lines else 1
            block_height_pt = num_lines * lh
            # Центр блока в координатах ReportLab (от низа страницы)
            center_y_pt = page_h - y_mm * MM_TO_PT
            # Верхний край блока → первая baseline (сдвиг на ascent ≈ 0.82 * size)
            y_top = center_y_pt + block_height_pt / 2 - size * 0.82
        else:
            y_top = page_h - y_mm * MM_TO_PT

        style = _text_style_flags(el)

        for i, line in enumerate(lines):
            y_line = y_top - i * lh
            _draw_styled_string(c, line, x_pt, y_line, element_font, size, align, style)
        c.restoreState()

    # Сбрасываем цвет на чёрный после всех элементов
    c.setFillColor(colors.black)


def draw_line_element(c: canvas.Canvas, el: Any, page_h: float) -> None:
    width_mm, _height_mm = _box_size_mm(el, 50.0, 0.35)
    x_mm = float(getattr(el, "x_mm", 105.0) or 105.0)
    y_mm = float(getattr(el, "y_mm", 148.5) or 148.5)
    x_center = x_mm * MM_TO_PT
    y_center = page_h - y_mm * MM_TO_PT
    half_w = width_mm * MM_TO_PT / 2

    c.saveState()
    c.setStrokeColor(_parse_element_color(el))
    _set_canvas_alpha(c, _element_opacity(el))
    c.setLineWidth(_line_width_pt(el))
    c.line(x_center - half_w, y_center, x_center + half_w, y_center)
    c.restoreState()


def draw_image_element(c: canvas.Canvas, el: Any, page_h: float) -> None:
    source_url = getattr(el, "source_url", None) or getattr(el, "image_url", None) or getattr(el, "url", None)
    path = _resolve_static_path(source_url)
    if not path:
        return

    width_mm, height_mm = _box_size_mm(el, 40.0, 25.0)
    x_mm = float(getattr(el, "x_mm", 105.0) or 105.0)
    y_mm = float(getattr(el, "y_mm", 148.5) or 148.5)
    box_w = width_mm * MM_TO_PT
    box_h = height_mm * MM_TO_PT
    box_x = x_mm * MM_TO_PT - box_w / 2
    box_y = page_h - (y_mm + height_mm / 2) * MM_TO_PT

    try:
        ir = ImageReader(path)
        iw, ih = ir.getSize()
        if iw <= 0 or ih <= 0:
            return
        scale = min(box_w / iw, box_h / ih)
        draw_w = iw * scale
        draw_h = ih * scale
        draw_x = box_x + (box_w - draw_w) / 2
        draw_y = box_y + (box_h - draw_h) / 2

        c.saveState()
        _set_canvas_alpha(c, _element_opacity(el))
        c.drawImage(ir, draw_x, draw_y, width=draw_w, height=draw_h, preserveAspectRatio=False, mask="auto")
        c.restoreState()
    except Exception as exc:
        logger.error("Ошибка отрисовки изображения элемента id=%s: %s", getattr(el, "id", "?"), exc)


def draw_canvas_elements(
    c: canvas.Canvas,
    elements: Sequence[Any],
    variables: dict[str, str],
    page_h: float,
    font_name: str,
    template: Any,
) -> None:
    for el in _ordered_visible_elements(elements):
        kind = _element_type(el)
        if kind in {"text", "variable"}:
            draw_text_elements(c, [el], variables, page_h, font_name, template)
        elif kind in {"image", "seal", "stamp", "signature", "facsimile"}:
            draw_image_element(c, el, page_h)
        elif kind == "line":
            draw_line_element(c, el, page_h)


def _compute_signers_anchor_y_mm(
    template: Any,
    num_signers: int,
    row_h_mm: float,
    mb: float,
) -> float:
    """
    Вычисляет Y-координату (от верха листа, мм) первой строки подписантов.
    """
    return float(getattr(template, "signers_y_mm", 250.0) or 250.0)


def draw_signers_block(
    c: canvas.Canvas,
    template: Any,
    signers: Sequence[Any],
    page_w: float,
    page_h: float,
) -> None:
    """
    Отрисовка блока подписантов (1–3 строки).

    Структура каждой строки:
      [Должность (38%, right-align)] | [Факсимиле PNG (24%, center)] | [ФИО (38%, left-align)]

    Факсимиле: прозрачный фон (mask='auto'), вписывается в ячейку с сохранением пропорций.
    Позиционирование: от верха листа (y_mm) или от низа (signers_anchor_bottom_mm).
    """
    if not signers:
        return

    # Ограничиваем 3 подписантами (enterprise-требование)
    sorted_signers = sorted(signers, key=lambda s: (s.order, s.id))[:3]
    num = len(sorted_signers)

    ml, mr, mt, mb = _margins_mm(template)
    block_x_mm = float(getattr(template, "signers_block_x_mm", 105.0) or 105.0)
    band_mm = float(getattr(template, "signers_band_width_mm", 168.0) or 168.0)
    row_h_mm = float(getattr(template, "signers_row_height_mm", 32.0) or 32.0)

    # Динамическое позиционирование якоря
    anchor_y_mm = _compute_signers_anchor_y_mm(template, num, row_h_mm, mb)

    block_x_mm, anchor_y_mm = _clamp_xy_mm(block_x_mm, anchor_y_mm, ml, mr, mt, mb)
    band_mm = min(
        band_mm,
        PAGE_W_MM - ml - mr - 2,
        2 * min(block_x_mm - ml, PAGE_W_MM - mr - block_x_mm),
    )

    base_sign_font = float(getattr(template, "signers_font_size", 10.0) or 10.0)
    base_sign_font = max(5.0, min(72.0, base_sign_font))
    weight_str = getattr(template, "signers_font_weight", "400")
    signer_font = _get_font_for_family_and_weight(
        getattr(template, "signers_font_family", None),
        weight_str,
    )
    fill = _parse_fill_color(getattr(template, "signers_text_color", None))
    # Раздельные цвета для должности и ФИО (fallback к общему цвету)
    pos_color_raw = getattr(template, "signers_position_color", None)
    name_color_raw = getattr(template, "signers_name_color", None)
    fill_position = _parse_fill_color(pos_color_raw) if pos_color_raw else fill
    fill_name = _parse_fill_color(name_color_raw) if name_color_raw else fill

    band_w_pt = band_mm * MM_TO_PT
    left_w = band_w_pt * _SIGN_LEFT_FRAC
    mid_w = band_w_pt * _SIGN_MID_FRAC
    right_w = band_w_pt * _SIGN_RIGHT_FRAC
    x_left_edge = block_x_mm * MM_TO_PT - band_w_pt / 2
    pad = 4.0

    for idx, signer in enumerate(sorted_signers):
        off = float(getattr(signer, "offset_y_mm", 0) or 0)
        y_top_mm = anchor_y_mm + idx * row_h_mm + off

        # Защита от выхода за нижнее поле
        max_y_mm = PAGE_H_MM - mb - 2.0
        if y_top_mm > max_y_mm:
            logger.warning(
                "Подписант %d: y_top_mm=%.1f выходит за нижнее поле (%.1f мм), пропускаем.",
                idx + 1, y_top_mm, max_y_mm,
            )
            continue

        _, y_top_mm = _clamp_xy_mm(block_x_mm, y_top_mm, ml, mr, mt, mb)
        # (0,0) = левый нижний угол → y от низа
        row_top_pt = page_h - y_top_mm * MM_TO_PT
        row_h_pt = row_h_mm * MM_TO_PT

        pos_text = (signer.position or "").strip()
        name_text = (signer.full_name or "").strip()

        c.setFillColor(fill_position)

        # ── Должность (левая колонка, выравнивание по левому краю) ──
        if pos_text:
            pw_pt = left_w - 2 * pad
            try:
                lines = wrap_text_to_width(pos_text, signer_font, base_sign_font, pw_pt)
                sz = base_sign_font
            except Exception as e:
                logger.error("auto_fit_text (position) ошибка: %s", e)
                sz, lines = base_sign_font, [pos_text]
            c.setFont(signer_font, sz)
            lh = sz * 1.2
            y0 = row_top_pt - lh
            x_start = x_left_edge + pad
            for i, ln in enumerate(lines[:6]):
                c.drawString(x_start, y0 - i * lh, ln)

        c.setFillColor(fill_name)

        # ── ФИО (правая колонка, выравнивание по правому краю) ──
        if name_text:
            rw_pt = right_w - 2 * pad
            try:
                lines = wrap_text_to_width(name_text, signer_font, base_sign_font, rw_pt)
                sz = base_sign_font
            except Exception as e:
                logger.error("auto_fit_text (full_name) ошибка: %s", e)
                sz, lines = base_sign_font, [name_text]
            c.setFont(signer_font, sz)
            lh = sz * 1.2
            y0 = row_top_pt - lh
            x_right = x_left_edge + left_w + mid_w + right_w - pad
            for i, ln in enumerate(lines[:6]):
                c.drawRightString(x_right, y0 - i * lh, ln)

        # ── Факсимиле (центральная колонка, прозрачный фон) ──
        fac_path = _resolve_static_path(getattr(signer, "facsimile_url", None))
        if fac_path:
            try:
                ir = ImageReader(fac_path)
                iw, ih = ir.getSize()
                if iw <= 0 or ih <= 0:
                    raise ValueError("Нулевые размеры факсимиле")

                box_w = mid_w - 2 * pad
                box_h = row_h_pt * 0.92

                # Вписываем с сохранением пропорций
                scale = min(box_w / iw, box_h / ih)
                dw, dh = iw * scale, ih * scale

                # Применяем пользовательский масштаб
                fac_sc = float(getattr(signer, "facsimile_scale", 1.0) or 1.0)
                fac_sc = max(0.2, min(3.0, fac_sc))
                dw *= fac_sc
                dh *= fac_sc

                # Если после масштабирования вышли за бокс — подрезаем
                if dw > box_w or dh > box_h:
                    r2 = min(box_w / max(dw, 0.001), box_h / max(dh, 0.001))
                    dw *= r2
                    dh *= r2

                # Центрируем в ячейке
                cx = x_left_edge + left_w + mid_w / 2
                ox = float(getattr(signer, "facsimile_offset_x_mm", 0) or 0) * MM_TO_PT
                oy = float(getattr(signer, "facsimile_offset_y_mm", 0) or 0) * MM_TO_PT
                ix = cx - dw / 2 + ox
                # iy: центр по вертикали в строке (от низа страницы)
                iy = row_top_pt - row_h_pt + (row_h_pt - dh) / 2 - oy

                # mask="auto" — прозрачный фон PNG
                c.drawImage(ir, ix, iy, width=dw, height=dh, mask="auto")

            except FileNotFoundError:
                logger.warning("Факсимиле не найдено: '%s' (подписант %d)", fac_path, idx + 1)
            except Exception as e:
                logger.error("Ошибка отрисовки факсимиле подписанта %d: %s", idx + 1, e)

    c.setFillColor(colors.black)


def generate_certificate_pdf(
    template: Any,
    elements: Sequence[Any],
    variables: dict[str, str],
    signers: Optional[Sequence[Any]] = None,
    font_name: Optional[str] = None,
    bg_reader: Optional[Any] = None,
) -> BytesIO:
    """
    Генерирует PDF-грамоту в памяти (BytesIO).

    Порядок отрисовки:
    1. Full-bleed фон (0,0) → (page_w, page_h)
    2. Текстовые блоки с auto-fit и поддержкой цвета
    3. Блок подписантов (до 3 строк)

    Все ошибки оборачиваются в try-except с логированием.
    """
    buffer = BytesIO()
    page_w, page_h = A4  # (595.27, 841.89) pt
    c = canvas.Canvas(buffer, pagesize=A4)
    resolved_font_name = font_name or _canvas_font_name()

    try:
        bg = _resolve_static_path(getattr(template, "background_url", None))
        if bg:
            draw_background_cover(c, bg, page_w, page_h, bg_reader=bg_reader)
        else:
            logger.info("Шаблон id=%s: фон не задан, используется белый лист.", getattr(template, "id", "?"))

        draw_canvas_elements(c, elements, variables, page_h, resolved_font_name, template)

        if signers:
            draw_signers_block(c, template, signers, page_w, page_h)

    except Exception as e:
        logger.exception("Критическая ошибка генерации PDF (template_id=%s): %s", getattr(template, "id", "?"), e)
        raise

    c.save()
    buffer.seek(0)
    return buffer
