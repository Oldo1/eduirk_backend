"""Russian FIO declension helpers for certificate variables."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from utils.certificate_text import case_key_candidates, normalize_case_name, split_placeholder_key


@dataclass(frozen=True)
class DeclensionContext:
    case: str
    gender: str | None


_FIO_KEYS = ("ФИО", "фио", "fio", "FIO")
_DATIVE_KEYS = ("ФИО:дательный", "ФИО:datv", "фио:дательный", "fio:datv")
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
_CASE_RU = {
    "nominative": "именительный",
    "genitive": "родительный",
    "dative": "дательный",
    "accusative": "винительный",
    "instrumental": "творительный",
    "prepositional": "предложный",
}
_PYMORPHY_CASES = {
    "genitive": "gent",
    "dative": "datv",
    "accusative": "accs",
    "instrumental": "ablt",
    "prepositional": "loct",
}
_WORD_RE = re.compile(r"^([^A-Za-zА-Яа-яЁё-]*)([A-Za-zА-Яа-яЁё-]+)(.*)$")
_ADJECTIVE_ENDINGS = (
    "ая",
    "яя",
    "ый",
    "ий",
    "ой",
    "ое",
    "ее",
    "ые",
    "ие",
)
_MORPH_ANALYZER: Any | None = None
_MORPH_UNAVAILABLE = False


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _has_word(text: str, pattern: str) -> bool:
    """Match Russian words without relying on ASCII-oriented word boundaries."""
    return re.search(rf"(^|[^а-яёa-z0-9_])(?:{pattern})($|[^а-яёa-z0-9_])", text, re.IGNORECASE) is not None


def _has_cyrillic(text: str) -> bool:
    return re.search(r"[А-Яа-яЁё]", text) is not None


def _split_word_token(token: str) -> tuple[str, str, str]:
    match = _WORD_RE.match(token)
    if not match:
        return token, "", ""
    return match.group(1), match.group(2), match.group(3)


def _is_abbreviation_core(core: str) -> bool:
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", core)
    return len(letters) > 1 and letters.upper() == letters and letters.lower() != letters


def _is_declension_candidate(token: str) -> bool:
    _prefix, core, _suffix = _split_word_token(token)
    return bool(core and _has_cyrillic(core) and not _is_abbreviation_core(core))


def _word_case_like(original: str, declined: str, lowercase: bool) -> str:
    if lowercase:
        return declined.lower()
    if original.isupper():
        return declined.upper()
    if original[:1].isupper():
        return declined[:1].upper() + declined[1:]
    return declined


def _lower_token_preserving_abbreviations(token: str) -> str:
    prefix, core, suffix = _split_word_token(token)
    if not core:
        return token
    if _is_abbreviation_core(core):
        return token
    return f"{prefix}{core.lower()}{suffix}"


def _get_morph_analyzer() -> Any | None:
    global _MORPH_ANALYZER, _MORPH_UNAVAILABLE
    if _MORPH_ANALYZER is not None:
        return _MORPH_ANALYZER
    if _MORPH_UNAVAILABLE:
        return None
    try:
        import pymorphy3  # type: ignore

        _MORPH_ANALYZER = pymorphy3.MorphAnalyzer()
        return _MORPH_ANALYZER
    except Exception:
        _MORPH_UNAVAILABLE = True
        return None


def detect_certificate_context(elements: list[Any]) -> str:
    """Build context from the first one or two sentences in top-to-bottom elements."""
    chunks: list[str] = []
    for el in sorted(elements, key=lambda item: float(getattr(item, "y_mm", 0) or 0)):
        text = re.sub(r"\{[^}]+\}", " ", str(getattr(el, "text", "") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            chunks.append(text)
        joined = " ".join(chunks)
        sentences = re.split(r"(?<=[.!?])\s+", joined)
        if len([s for s in sentences if s.strip()]) >= 2:
            return " ".join(sentences[:2]).strip()
        if len(joined) >= 220:
            return joined[:220]
    return " ".join(chunks)


def _detect_gender_from_fio(fio: str) -> str | None:
    parts = [p for p in fio.split() if p]
    patronymic = parts[2].lower() if len(parts) >= 3 else ""
    first = parts[1].lower() if len(parts) >= 2 else (parts[0].lower() if parts else "")
    surname = parts[0].lower() if parts else ""

    if patronymic.endswith(("ич", "оглы")):
        return "male"
    if patronymic.endswith(("на", "кызы")):
        return "female"
    if surname.endswith(("ова", "ева", "ина", "ая")):
        return "female"
    if first.endswith(("а", "я")):
        return "female"
    if surname.endswith(("ов", "ев", "ин", "ын", "ский", "цкий")):
        return "male"
    return None


def _normalize_gender(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"female", "f", "ж", "жен", "женский", "девочка"}:
        return "female"
    if normalized in {"male", "m", "м", "муж", "мужской", "мальчик"}:
        return "male"
    return None


def resolve_name_case_and_gender(context_text: str, fio: str) -> DeclensionContext:
    """Resolve the target case and gender from certificate wording."""
    text = _norm(context_text)
    gender: str | None = None
    if _has_word(text, r"награжд[её]н"):
        gender = "male"
    if _has_word(text, r"награждена"):
        gender = "female"
    if gender is None:
        gender = _detect_gender_from_fio(fio)

    if _has_word(text, r"вручается|выдан[ао]?|присуждается|предоставляется|адресуется"):
        case = "dative"
    else:
        # Backward-compatible default: "Награждается Иванов..." stays nominative.
        case = "nominative"

    return DeclensionContext(case=case, gender=gender)


def _petrovich_gender(gender: str | None) -> str:
    if gender == "female":
        return "female"
    return "male"


def _decline_with_petrovich(fio: str, case: str, gender: str | None) -> str | None:
    if case == "nominative":
        return fio

    parts = fio.split()
    if len(parts) < 2:
        return None

    # pytrovich package API.
    try:
        from petrovich.main import Petrovich  # type: ignore
        from petrovich.enums import Case, Gender, NamePart  # type: ignore

        petrovich = Petrovich()
        target_case = getattr(Case, "DATIVE")
        target_gender = Gender.FEMALE if gender == "female" else Gender.MALE
        declined = [
            petrovich.lastname(parts[0], target_case, target_gender),
            petrovich.firstname(parts[1], target_case, target_gender),
        ]
        if len(parts) >= 3:
            try:
                declined.append(petrovich.middlename(parts[2], target_case, target_gender))
            except AttributeError:
                declined.append(
                    petrovich.make(NamePart.MIDDLENAME, target_gender, target_case, parts[2])
                )
        return " ".join(declined + parts[3:])
    except Exception:
        pass

    # Petrovich package API variants.
    try:
        from Petrovich import Petrovich  # type: ignore

        petrovich = Petrovich()
        gender_value = _petrovich_gender(gender)
        declined = [
            petrovich.lastname(parts[0], "dative", gender_value),
            petrovich.firstname(parts[1], "dative", gender_value),
        ]
        if len(parts) >= 3:
            declined.append(petrovich.middlename(parts[2], "dative", gender_value))
        return " ".join(declined + parts[3:])
    except Exception:
        return None


def _dative_lastname(lastname: str, gender: str | None) -> str:
    lower = lastname.lower()
    if gender == "female":
        if lower.endswith(("ов", "ев", "ин", "ын")):
            return lastname + "ой"
        if lower.endswith(("ский", "цкий")):
            return lastname[:-2] + "ой"
        if lower.endswith(("ова", "ева", "ина")):
            return lastname[:-1] + "ой"
        if lower.endswith("ая"):
            return lastname[:-2] + "ой"
        if lower.endswith("яя"):
            return lastname[:-2] + "ей"
        return lastname
    if lower.endswith(("ов", "ев", "ин", "ын")):
        return lastname + "у"
    if lower.endswith(("ский", "цкий")):
        return lastname[:-2] + "ому"
    return lastname


def _genitive_lastname(lastname: str, gender: str | None) -> str:
    lower = lastname.lower()
    if gender == "female":
        if lower.endswith(("ов", "ев", "ин", "ын")):
            return lastname + "ой"
        if lower.endswith(("ова", "ева", "ина")):
            return lastname[:-1] + "ой"
        if lower.endswith(("ая", "яя")):
            return lastname[:-2] + ("ей" if lower.endswith("яя") else "ой")
        return lastname
    if lower.endswith(("ов", "ев", "ин", "ын")):
        return lastname + "а"
    if lower.endswith(("ский", "цкий")):
        return lastname[:-2] + "ого"
    return lastname


def _dative_firstname(firstname: str, gender: str | None) -> str:
    lower = firstname.lower()
    if gender == "female":
        if lower.endswith("ия"):
            return firstname[:-1] + "и"
        if lower.endswith("а"):
            return firstname[:-1] + "е"
        if lower.endswith("я"):
            return firstname[:-1] + "е"
        return firstname
    if lower.endswith(("й", "ь")):
        return firstname[:-1] + "ю"
    if lower.endswith("а"):
        return firstname[:-1] + "е"
    return firstname + "у"


def _genitive_firstname(firstname: str, gender: str | None) -> str:
    lower = firstname.lower()
    if gender == "female":
        if lower.endswith("ия"):
            return firstname[:-1] + "и"
        if lower.endswith("а"):
            return firstname[:-1] + ("и" if lower[-2:-1] in {"г", "к", "х", "ж", "ч", "ш", "щ"} else "ы")
        if lower.endswith("я"):
            return firstname[:-1] + "и"
        return firstname
    if lower.endswith(("й", "ь")):
        return firstname[:-1] + "я"
    if lower.endswith("а"):
        return firstname[:-1] + "ы"
    return firstname + "а"


def _dative_patronymic(patronymic: str, gender: str | None) -> str:
    lower = patronymic.lower()
    if gender == "female":
        if lower.endswith("на"):
            return patronymic[:-1] + "е"
        return patronymic
    if lower.endswith("ич"):
        return patronymic + "у"
    return patronymic


def _genitive_patronymic(patronymic: str, gender: str | None) -> str:
    lower = patronymic.lower()
    if gender == "female":
        if lower.endswith("на"):
            return patronymic[:-1] + "ы"
        return patronymic
    if lower.endswith("ич"):
        return patronymic + "а"
    return patronymic


def _decline_fallback(fio: str, case: str, gender: str | None) -> str:
    if case == "nominative":
        return fio
    parts = fio.split()
    if len(parts) < 2:
        return fio

    if case == "genitive":
        declined = [_genitive_lastname(parts[0], gender), _genitive_firstname(parts[1], gender)]
        if len(parts) >= 3:
            declined.append(_genitive_patronymic(parts[2], gender))
        return " ".join(declined + parts[3:])

    if case != "dative":
        return fio

    declined = [_dative_lastname(parts[0], gender), _dative_firstname(parts[1], gender)]
    if len(parts) >= 3:
        declined.append(_dative_patronymic(parts[2], gender))
    return " ".join(declined + parts[3:])


def _is_masculine_surname_form(lastname: str) -> bool:
    lower = lastname.lower()
    return lower.endswith(("ов", "ев", "ин", "ын", "ский", "цкий"))


def _library_left_female_surname_unchanged(original: str, declined: str | None, gender: str | None) -> bool:
    if gender != "female" or not declined:
        return False
    original_parts = original.split()
    declined_parts = declined.split()
    if not original_parts or not declined_parts:
        return False
    return _is_masculine_surname_form(original_parts[0]) and original_parts[0] == declined_parts[0]


def decline_fio(fio: str, case: str, gender: str | None = None) -> str:
    """Return declined FIO, falling back to the original value on uncertainty."""
    clean = re.sub(r"\s+", " ", fio or "").strip()
    if not clean:
        return clean
    case = normalize_case_name(case) or "nominative"
    if case == "nominative":
        return clean
    fallback = _decline_fallback(clean, case, gender)
    if case != "dative":
        return fallback
    declined = _decline_with_petrovich(clean, case, gender)
    if _library_left_female_surname_unchanged(clean, declined, gender):
        return fallback
    return declined or fallback


def decline_organization(value: str, case: str) -> tuple[str, str | None]:
    """MVP declension for common school organization names."""
    clean = re.sub(r"\s+", " ", value or "").strip()
    canonical_case = normalize_case_name(case) or "nominative"
    if not clean or canonical_case in {"", "nominative"}:
        return clean, None
    if canonical_case != "genitive":
        return clean, f"Для организации «{clean}» пока поддержан только родительный падеж."

    patterns = [
        (r"^Школа(\s+№\s*\d+.*)?$", "Школы"),
        (r"^Лицей(\s+№\s*\d+.*)?$", "Лицея"),
        (r"^Гимназия(\s+№\s*\d+.*)?$", "Гимназии"),
    ]
    for pattern, declined_head in patterns:
        match = re.match(pattern, clean, re.IGNORECASE)
        if match:
            return f"{declined_head}{match.group(1) or ''}", None
    return clean, f"Не удалось автоматически склонить организацию «{clean}». Используйте override-колонку."


def _inflect_core_with_pymorphy(core: str, case: str, lowercase: bool) -> str | None:
    morph = _get_morph_analyzer()
    tag = _PYMORPHY_CASES.get(normalize_case_name(case))
    if morph is None or tag is None:
        return None

    if "-" in core:
        parts = core.split("-")
        declined_last = _inflect_core_with_pymorphy(parts[-1], case, lowercase=True)
        if not declined_last:
            return None
        declined = "-".join([part.lower() for part in parts[:-1]] + [declined_last])
        return _word_case_like(core, declined, lowercase)

    parsed = morph.parse(core.lower())
    if not parsed:
        return None
    best = parsed[0]
    if best.tag.POS not in {"NOUN", "ADJF", "PRTF", "NUMR"}:
        return None
    inflected = best.inflect({tag})
    if inflected is None:
        return None
    return _word_case_like(core, inflected.word, lowercase)


def _decline_noun_core_fallback(core: str, case: str, lowercase: bool) -> str:
    if "-" in core:
        parts = core.split("-")
        parts[-1] = _decline_noun_core_fallback(parts[-1], case, lowercase=True)
        declined = "-".join(part.lower() for part in parts)
        return _word_case_like(core, declined, lowercase)

    lower = core.lower()
    canonical = normalize_case_name(case)
    declined = lower

    if canonical == "prepositional":
        if lower.endswith("ия"):
            declined = lower[:-1] + "и"
        elif lower.endswith("а"):
            declined = lower[:-1] + "е"
        elif lower.endswith("я"):
            declined = lower[:-1] + "е"
        elif lower.endswith("ий"):
            declined = lower[:-2] + "ии"
        elif lower.endswith("й"):
            declined = lower[:-1] + "е"
        elif lower.endswith("ь"):
            declined = lower[:-1] + "е"
        elif re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
            declined = lower + "е"
    elif canonical == "genitive":
        if lower.endswith("ия"):
            declined = lower[:-1] + "и"
        elif lower.endswith("а"):
            stem = lower[:-1]
            declined = stem + ("и" if stem.endswith(("г", "к", "х", "ж", "ч", "ш", "щ")) else "ы")
        elif lower.endswith("я"):
            declined = lower[:-1] + "и"
        elif lower.endswith("ий"):
            declined = lower[:-2] + "ия"
        elif lower.endswith(("й", "ь")):
            declined = lower[:-1] + "я"
        elif re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
            declined = lower + "а"
    elif canonical == "dative":
        if lower.endswith("ия"):
            declined = lower[:-1] + "и"
        elif lower.endswith(("а", "я")):
            declined = lower[:-1] + "е"
        elif lower.endswith(("й", "ь")):
            declined = lower[:-1] + "ю"
        elif re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
            declined = lower + "у"

    return _word_case_like(core, declined, lowercase)


def _decline_adjective_core_fallback(core: str, case: str, lowercase: bool) -> str:
    if "-" in core:
        parts = core.split("-")
        parts[-1] = _decline_adjective_core_fallback(parts[-1], case, lowercase=True)
        declined = "-".join(part.lower() for part in parts)
        return _word_case_like(core, declined, lowercase)

    lower = core.lower()
    canonical = normalize_case_name(case)
    declined = lower

    if canonical in {"genitive", "prepositional"}:
        if lower.endswith("ая"):
            declined = lower[:-2] + "ой"
        elif lower.endswith("яя"):
            declined = lower[:-2] + "ей"
        elif lower.endswith(("ый", "ой")):
            declined = lower[:-2] + "ом"
        elif lower.endswith("ий"):
            declined = lower[:-2] + "ем"
        elif lower.endswith("ое"):
            declined = lower[:-2] + "ом"
        elif lower.endswith("ее"):
            declined = lower[:-2] + "ем"

    return _word_case_like(core, declined, lowercase)


def _decline_token(token: str, case: str, *, adjective: bool, lowercase: bool) -> str:
    prefix, core, suffix = _split_word_token(token)
    if not core or _is_abbreviation_core(core) or not _has_cyrillic(core):
        return _lower_token_preserving_abbreviations(token) if lowercase else token

    declined = _inflect_core_with_pymorphy(core, case, lowercase)
    if declined is None:
        if adjective:
            declined = _decline_adjective_core_fallback(core, case, lowercase)
        else:
            declined = _decline_noun_core_fallback(core, case, lowercase)
    return f"{prefix}{declined}{suffix}"


def _looks_like_adjective_token(token: str) -> bool:
    _prefix, core, _suffix = _split_word_token(token)
    if not core or not _has_cyrillic(core):
        return False
    last_part = core.lower().split("-")[-1]
    return last_part.endswith(_ADJECTIVE_ENDINGS)


def _find_phrase_head_index(tokens: list[str]) -> int | None:
    candidates = [index for index, token in enumerate(tokens) if _is_declension_candidate(token)]
    if not candidates:
        return None
    for index in candidates:
        if _looks_like_adjective_token(tokens[index]) and any(item > index for item in candidates):
            continue
        return index
    return candidates[0]


def decline_variable_value(value: str, case: str, *, lowercase: bool = False) -> str:
    """Decline the main word of an arbitrary Russian variable value conservatively."""
    clean = re.sub(r"\s+", " ", value or "").strip()
    canonical = normalize_case_name(case) or "nominative"
    if not clean or canonical in {"", "nominative"}:
        return clean.lower() if lowercase else clean

    tokens = clean.split(" ")
    head_index = _find_phrase_head_index(tokens)
    if head_index is None:
        return " ".join(_lower_token_preserving_abbreviations(token) for token in tokens) if lowercase else clean

    declined_tokens: list[str] = []
    for index, token in enumerate(tokens):
        if index == head_index:
            declined_tokens.append(_decline_token(token, canonical, adjective=False, lowercase=lowercase))
            continue
        if index < head_index and _looks_like_adjective_token(token):
            declined_tokens.append(_decline_token(token, canonical, adjective=True, lowercase=lowercase))
            continue
        declined_tokens.append(_lower_token_preserving_abbreviations(token) if lowercase else token)
    return " ".join(declined_tokens)


def _get_fio_value(variables: Mapping[str, Any]) -> str:
    for key in _FIO_KEYS:
        value = variables.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key, value in variables.items():
        if str(key).strip().lower().replace(" ", "") == "фио" and str(value).strip():
            return str(value).strip()
    return ""


def _get_value_by_name(variables: Mapping[str, Any], name: str) -> str:
    name_norm = str(name or "").strip().lower().replace(" ", "")
    for key, value in variables.items():
        if str(key).strip().lower().replace(" ", "") == name_norm and str(value).strip():
            return str(value).strip()
    return ""


def _explicit_grammar_specs(elements: list[Any]) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for el in elements:
        for match in _PLACEHOLDER_RE.finditer(str(getattr(el, "text", "") or "")):
            base_name, case_name = split_placeholder_key(match.group(1))
            if not base_name or not case_name:
                continue
            key = (base_name, case_name)
            if key in seen:
                continue
            seen.add(key)
            specs.append(key)
    return specs


def _override_value(variables: Mapping[str, Any], name: str, case_name: str) -> str:
    for candidate in case_key_candidates(name, case_name):
        value = _get_value_by_name(variables, candidate)
        if value:
            return value
    return ""


def _set_case_values(out: dict[str, str], name: str, case_name: str, value: str) -> None:
    canonical = normalize_case_name(case_name)
    for candidate in case_key_candidates(name, canonical):
        out[candidate] = value
    ru = _CASE_RU.get(canonical)
    if ru:
        out[f"{name}:{ru}"] = value
        out[f"{name}_{ru}"] = value


def _set_value_by_name(out: dict[str, str], name: str, value: str) -> None:
    name_norm = str(name or "").strip().lower().replace(" ", "")
    found = False
    for key in list(out.keys()):
        if str(key).strip().lower().replace(" ", "") == name_norm:
            out[key] = value
            found = True
    if not found:
        out[name] = value


def _is_gender_variant_placeholder(inner: str) -> bool:
    return re.match(r"^(?:род|пол|gender)\s*:", str(inner or "").strip(), re.IGNORECASE) is not None


def _infer_placeholder_case(text: str, start: int) -> str:
    before = re.sub(r"\s+", " ", text[:start])[-120:]
    if re.search(r"(^|[^а-яёa-z0-9_])(?:в|на)\s*$", before, re.IGNORECASE):
        return "prepositional"
    if re.search(
        r"(^|[^а-яёa-z0-9_])(?:участник|участница|победитель|приз[её]р|лауреат|ученик\|ученица|ученик|ученица)\s*$",
        before,
        re.IGNORECASE,
    ):
        return "genitive"
    return ""


def _contextual_grammar_specs(elements: list[Any]) -> list[tuple[str, str]]:
    specs_by_name: dict[str, tuple[str, str]] = {}
    conflicts: set[str] = set()

    for el in elements:
        text = str(getattr(el, "text", "") or "")
        for match in _PLACEHOLDER_RE.finditer(text):
            inner = match.group(1).strip()
            if _is_gender_variant_placeholder(inner):
                continue
            base_name, explicit_case = split_placeholder_key(inner)
            if not base_name or explicit_case:
                continue
            inferred_case = _infer_placeholder_case(text, match.start())
            if not inferred_case:
                continue
            name_norm = base_name.strip().lower().replace(" ", "")
            if name_norm in conflicts:
                continue
            existing = specs_by_name.get(name_norm)
            if existing and existing[1] != inferred_case:
                conflicts.add(name_norm)
                specs_by_name.pop(name_norm, None)
                continue
            specs_by_name[name_norm] = (base_name, inferred_case)

    return list(specs_by_name.values())


def prepare_certificate_variables_with_warnings(elements: list[Any], variables: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Apply explicit grammar variants, legacy FIO aliases, and collect MVP warnings."""
    out = {str(k): "" if v is None else str(v) for k, v in dict(variables).items()}
    fio = _get_fio_value(out)
    warnings: list[str] = []

    gender = _normalize_gender(out.get("Пол") or out.get("пол"))
    if gender is None and fio:
        gender = _detect_gender_from_fio(fio)

    if gender:
        out["__gender"] = gender

    for name, case_name in _explicit_grammar_specs(elements):
        canonical = normalize_case_name(case_name)
        if not canonical:
            continue
        override = _override_value(out, name, canonical)
        if override:
            _set_case_values(out, name, canonical, override)
            continue

        name_norm = name.strip().lower().replace(" ", "")
        source = _get_value_by_name(out, name)
        if name_norm in {"фио", "fio"}:
            source = fio or source
            declined = decline_fio(source, canonical, gender) if source else ""
            _set_case_values(out, name, canonical, declined)
            continue

        if source:
            _set_case_values(out, name, canonical, decline_variable_value(source, canonical))

    for name, case_name in _contextual_grammar_specs(elements):
        canonical = normalize_case_name(case_name)
        if not canonical:
            continue
        name_norm = name.strip().lower().replace(" ", "")
        if name_norm in {"фио", "fio"}:
            continue
        source = _get_value_by_name(out, name)
        if not source:
            continue
        override = _override_value(out, name, canonical)
        declined = override or decline_variable_value(source, canonical, lowercase=True)
        _set_case_values(out, name, canonical, declined)
        _set_value_by_name(out, name, declined)

    if not fio:
        return out, warnings

    context = detect_certificate_context(elements)
    resolved = resolve_name_case_and_gender(context, fio)
    contextual_fio = decline_fio(fio, resolved.case, resolved.gender)
    dative_fio = _override_value(out, "ФИО", "dative") or decline_fio(fio, "dative", resolved.gender or gender)
    genitive_fio = _override_value(out, "ФИО", "genitive") or decline_fio(fio, "genitive", resolved.gender or gender)

    if resolved.gender:
        out["__gender"] = resolved.gender
    for key in _FIO_KEYS:
        out[key] = contextual_fio
    for key in _DATIVE_KEYS:
        out[key] = dative_fio
    _set_case_values(out, "ФИО", "dative", dative_fio)
    _set_case_values(out, "фио", "dative", dative_fio)
    _set_case_values(out, "ФИО", "genitive", genitive_fio)
    _set_case_values(out, "фио", "genitive", genitive_fio)
    out.setdefault("ФИО:именительный", fio)
    out.setdefault("ФИО:nomn", fio)
    return out, warnings


def prepare_certificate_variables(elements: list[Any], variables: Mapping[str, Any]) -> dict[str, str]:
    """Apply certificate variable processing and keep legacy aliases available."""
    prepared, _warnings = prepare_certificate_variables_with_warnings(elements, variables)
    return prepared
