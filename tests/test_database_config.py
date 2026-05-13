import pytest

from database import (
    DatabaseConfigurationError,
    format_database_connection_error,
    get_database_url,
    validate_database_url,
)


def test_get_database_url_uses_local_postgres_defaults(monkeypatch):
    for key in (
        "DATABASE_URL",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    ):
        monkeypatch.delenv(key, raising=False)

    assert (
        get_database_url()
        == "postgresql+psycopg2://mky_user:mky_password@localhost:5432/mky_db"
    )


def test_validate_database_url_rejects_nonbreaking_spaces():
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        validate_database_url(
            "postgresql+psycopg2://mky_user:mky_password@localhost:5432/mky_db\u00a0"
        )


def test_format_database_connection_error_decodes_windows_postgres_message():
    raw_message = (
        b'connection to server at "localhost" (127.0.0.1), port 5432 failed: '
        b'\xc2\xc0\xc6\xcd\xce:  \xef\xee\xeb\xfc\xe7\xee\xe2\xe0\xf2\xe5\xeb\xfc '
        b'"mky_user" \xed\xe5 \xef\xf0\xee\xf8\xb8\xeb \xef\xf0\xee\xe2\xe5\xf0\xea\xf3'
    )
    exc = UnicodeDecodeError("utf-8", raw_message, 67, 68, "invalid continuation byte")

    message = format_database_connection_error(
        exc,
        "postgresql+psycopg2://mky_user:mky_password@localhost:5432/mky_db",
    )

    assert "PostgreSQL connection failed" in message
    assert "postgresql+psycopg2://mky_user:***@localhost:5432/mky_db" in message
    assert "пользователь" in message
