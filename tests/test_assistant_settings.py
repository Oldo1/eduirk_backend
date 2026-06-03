from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import assistant_settings
import routers.assistant as assistant
from assistant_settings import AssistantSettings
from database import Base


def _settings(**changes) -> AssistantSettings:
    return AssistantSettings(
        update_interval_hours=changes.get("update_interval_hours", 24.0),
        gigachat_model=changes.get("gigachat_model", "GigaChat"),
        question_max_length=changes.get("question_max_length", 4000),
        session_ttl_seconds=changes.get("session_ttl_seconds", 10_800),
        history_max_messages=changes.get("history_max_messages", 400),
        rate_limit_window_seconds=changes.get("rate_limit_window_seconds", 60),
        rate_limit_max_requests=changes.get("rate_limit_max_requests", 12),
    )


@pytest.fixture()
def settings_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    monkeypatch.setattr(assistant_settings, "_settings", assistant_settings.DEFAULT_ASSISTANT_SETTINGS)
    monkeypatch.setattr(assistant_settings, "_settings_loaded", False)
    monkeypatch.setattr(assistant_settings, "_listeners", [])
    with TestingSession() as db:
        yield db


def test_runtime_settings_are_persisted(settings_db):
    loaded = assistant_settings.load_assistant_settings(settings_db)
    assert loaded.gigachat_model == assistant_settings.DEFAULT_ASSISTANT_SETTINGS.gigachat_model

    updated = assistant_settings.update_assistant_settings(
        settings_db,
        update_interval_hours=6,
        gigachat_model="GigaChat-Pro",
        question_max_length=1200,
        session_ttl_seconds=900,
        history_max_messages=80,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=5,
    )

    assert updated.gigachat_model == "GigaChat-Pro"
    assert updated.update_interval_hours == 6
    assert assistant_settings.get_assistant_settings().history_max_messages == 80


def test_question_validation_uses_runtime_limit(monkeypatch):
    monkeypatch.setattr(assistant, "get_assistant_settings", lambda: _settings(question_max_length=3))

    assert assistant._validated_question("123") == "123"
    with pytest.raises(HTTPException, match="Максимум: 3"):
        assistant._validated_question("1234")


def test_rate_limit_uses_runtime_values(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "get_assistant_settings",
        lambda: _settings(rate_limit_window_seconds=60, rate_limit_max_requests=2),
    )
    assistant._rate_limit_buckets.clear()
    request = Request({"type": "http", "client": ("127.0.0.1", 12345)})

    assistant._check_assistant_rate_limit(request, None)
    assistant._check_assistant_rate_limit(request, None)
    with pytest.raises(HTTPException) as exc:
        assistant._check_assistant_rate_limit(request, None)

    assert exc.value.status_code == 429


def test_settings_access_is_limited_to_chat_admin_roles():
    assert assistant._require_settings_admin(SimpleNamespace(role="admin")).role == "admin"
    assert assistant._require_settings_admin(SimpleNamespace(role="methodist")).role == "methodist"
    with pytest.raises(HTTPException) as exc:
        assistant._require_settings_admin(SimpleNamespace(role="user"))
    assert exc.value.status_code == 403
