"""Russian FIO declension helpers for certificate variables."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DeclensionContext:
    case: str
    gender: str | None


_FIO_KEYS = ("ФИО", "фио", "fio", "FIO")
_DATIVE_KEYS = ("ФИО:дательный", "ФИО:datv", "фио:дательный", "fio:datv")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _has_word(text: str, pattern: str) -> bool:
    """Match Russian words without relying on ASCII-oriented word boundaries."""
    return re.search(rf"(^|[^а-яёa-z0-9_])(?:{pattern})($|[^а-яёa-z0-9_])", text, re.IGNORECASE) is not None


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


def _dative_patronymic(patronymic: str, gender: str | None) -> str:
    lower = patronymic.lower()
    if gender == "female":
        if lower.endswith("на"):
            return patronymic[:-1] + "е"
        return patronymic
    if lower.endswith("ич"):
        return patronymic + "у"
    return patronymic


def _decline_fallback(fio: str, case: str, gender: str | None) -> str:
    if case == "nominative":
        return fio
    parts = fio.split()
    if len(parts) < 2:
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
    if case == "nominative":
        return clean
    fallback = _decline_fallback(clean, case, gender)
    declined = _decline_with_petrovich(clean, case, gender)
    if _library_left_female_surname_unchanged(clean, declined, gender):
        return fallback
    return declined or fallback


def _get_fio_value(variables: Mapping[str, Any]) -> str:
    for key in _FIO_KEYS:
        value = variables.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key, value in variables.items():
        if str(key).strip().lower().replace(" ", "") == "фио" and str(value).strip():
            return str(value).strip()
    return ""


def prepare_certificate_variables(elements: list[Any], variables: Mapping[str, Any]) -> dict[str, str]:
    """Apply automatic FIO declension and keep legacy aliases available."""
    out = {str(k): "" if v is None else str(v) for k, v in dict(variables).items()}
    fio = _get_fio_value(out)
    if not fio:
        return out

    context = detect_certificate_context(elements)
    resolved = resolve_name_case_and_gender(context, fio)
    contextual_fio = decline_fio(fio, resolved.case, resolved.gender)
    dative_fio = decline_fio(fio, "dative", resolved.gender)

    if resolved.gender:
        out["__gender"] = resolved.gender
    for key in _FIO_KEYS:
        out[key] = contextual_fio
    for key in _DATIVE_KEYS:
        out[key] = dative_fio
    out.setdefault("ФИО:именительный", fio)
    out.setdefault("ФИО:nomn", fio)
    return out
