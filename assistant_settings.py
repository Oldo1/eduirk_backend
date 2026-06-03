from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock
from typing import Callable
import os

from sqlalchemy.orm import Session

from models import AssistantRuntimeSettings

AVAILABLE_GIGACHAT_MODELS = ("GigaChat", "GigaChat-Pro", "GigaChat-Max")
SettingsListener = Callable[["AssistantSettings", "AssistantSettings"], None]


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_model() -> str:
    value = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
    return value if value in AVAILABLE_GIGACHAT_MODELS else "GigaChat"


@dataclass(frozen=True)
class AssistantSettings:
    update_interval_hours: float
    gigachat_model: str
    question_max_length: int
    session_ttl_seconds: int
    history_max_messages: int
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    updated_at: datetime | None = None


DEFAULT_ASSISTANT_SETTINGS = AssistantSettings(
    update_interval_hours=_env_float("UPDATE_INTERVAL_HOURS", 24.0, minimum=0.01),
    gigachat_model=_env_model(),
    question_max_length=_env_int("ASSISTANT_QUESTION_MAX_LENGTH", 4000, minimum=1),
    session_ttl_seconds=_env_int("ASSISTANT_SESSION_TTL_SECONDS", 3 * 60 * 60),
    history_max_messages=_env_int("ASSISTANT_HISTORY_MAX_MESSAGES", 400),
    rate_limit_window_seconds=_env_int("ASSISTANT_RATE_LIMIT_WINDOW_SECONDS", 60),
    rate_limit_max_requests=_env_int("ASSISTANT_RATE_LIMIT_MAX_REQUESTS", 12),
)

_settings_lock = Lock()
_settings = DEFAULT_ASSISTANT_SETTINGS
_settings_loaded = False
_listeners: list[SettingsListener] = []


def _settings_from_row(row: AssistantRuntimeSettings) -> AssistantSettings:
    return AssistantSettings(
        update_interval_hours=row.update_interval_hours,
        gigachat_model=row.gigachat_model,
        question_max_length=row.question_max_length,
        session_ttl_seconds=row.session_ttl_seconds,
        history_max_messages=row.history_max_messages,
        rate_limit_window_seconds=row.rate_limit_window_seconds,
        rate_limit_max_requests=row.rate_limit_max_requests,
        updated_at=row.updated_at,
    )


def _apply_row_values(row: AssistantRuntimeSettings, settings: AssistantSettings) -> None:
    row.update_interval_hours = settings.update_interval_hours
    row.gigachat_model = settings.gigachat_model
    row.question_max_length = settings.question_max_length
    row.session_ttl_seconds = settings.session_ttl_seconds
    row.history_max_messages = settings.history_max_messages
    row.rate_limit_window_seconds = settings.rate_limit_window_seconds
    row.rate_limit_max_requests = settings.rate_limit_max_requests


def get_assistant_settings() -> AssistantSettings:
    with _settings_lock:
        return _settings


def load_assistant_settings(db: Session) -> AssistantSettings:
    global _settings, _settings_loaded

    with _settings_lock:
        if _settings_loaded:
            return _settings

    row = db.get(AssistantRuntimeSettings, 1)
    if row is None:
        row = AssistantRuntimeSettings(id=1)
        _apply_row_values(row, DEFAULT_ASSISTANT_SETTINGS)
        db.add(row)
        db.commit()
        db.refresh(row)

    loaded = _settings_from_row(row)
    with _settings_lock:
        _settings = loaded
        _settings_loaded = True
    return loaded


def save_assistant_settings(db: Session, settings: AssistantSettings) -> AssistantSettings:
    global _settings, _settings_loaded

    previous = load_assistant_settings(db)
    row = db.get(AssistantRuntimeSettings, 1)
    if row is None:
        row = AssistantRuntimeSettings(id=1)
        db.add(row)
    _apply_row_values(row, settings)
    db.commit()
    db.refresh(row)
    saved = _settings_from_row(row)

    with _settings_lock:
        _settings = saved
        _settings_loaded = True
        listeners = list(_listeners)

    for listener in listeners:
        listener(previous, saved)
    return saved


def update_assistant_settings(db: Session, **changes) -> AssistantSettings:
    current = load_assistant_settings(db)
    return save_assistant_settings(db, replace(current, updated_at=None, **changes))


def register_settings_listener(listener: SettingsListener) -> None:
    with _settings_lock:
        if listener not in _listeners:
            _listeners.append(listener)
