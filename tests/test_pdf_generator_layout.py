from types import SimpleNamespace

from utils.pdf_generator import _ordered_visible_elements, _text_style_flags


def test_ordered_visible_elements_uses_z_index_and_skips_hidden():
    elements = [
        SimpleNamespace(id="front", z_index=30, hidden=False),
        SimpleNamespace(id="hidden", z_index=20, hidden=True),
        SimpleNamespace(id="back", z_index=10, hidden=False),
        SimpleNamespace(id="middle", z_index=20, hidden=False),
    ]

    ordered = _ordered_visible_elements(elements)

    assert [item.id for item in ordered] == ["back", "middle", "front"]


def test_text_style_flags_detect_bold_italic_and_underline():
    element = SimpleNamespace(font_weight="700", italic=True, underline=True)

    flags = _text_style_flags(element)

    assert flags.bold is True
    assert flags.italic is True
    assert flags.underline is True
