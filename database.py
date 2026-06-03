from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from dotenv import load_dotenv
import os

# Load local environment variables when a developer has a private .env file.
load_dotenv()


class DatabaseConfigurationError(RuntimeError):
    """Raised when database environment variables cannot be used safely."""


def _clean_env_value(name: str, value: str | None) -> str | None:
    if value is None:
        return None

    forbidden = {
        "\ufeff": "BOM",
        "\u00a0": "non-breaking space",
        "\u200b": "zero-width space",
        "\u200c": "zero-width non-joiner",
        "\u200d": "zero-width joiner",
    }
    for char, label in forbidden.items():
        if char in value:
            raise DatabaseConfigurationError(
                f"{name} contains an invisible character ({label}). "
                "Re-copy it from .env.example."
            )

    cleaned = value.strip().strip('"').strip("'")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DatabaseConfigurationError(
            f"{name} contains characters that cannot be encoded as UTF-8."
        ) from exc

    return cleaned


def _env(name: str, default: str | None = None, *aliases: str) -> str | None:
    for key in (name, *aliases):
        value = _clean_env_value(key, os.getenv(key))
        if value:
            return value
    return default


def validate_database_url(value: str | None) -> str:
    url = _clean_env_value("DATABASE_URL", value)
    if not url:
        raise DatabaseConfigurationError(
            "DATABASE_URL is empty. Copy backend/.env.example to backend/.env "
            "or set DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME."
        )
    if any(char.isspace() for char in url):
        raise DatabaseConfigurationError(
            "DATABASE_URL contains whitespace. Re-copy it from .env.example "
            "and keep the value on one line without trailing spaces."
        )

    try:
        parsed = make_url(url)
    except ArgumentError as exc:
        raise DatabaseConfigurationError(
            "DATABASE_URL is not a valid SQLAlchemy database URL. "
            "Example: postgresql+psycopg2://mky_user:mky_password@localhost:5432/mky_db"
        ) from exc

    if parsed.drivername.startswith("postgresql"):
        missing = []
        if not parsed.username:
            missing.append("username")
        if not parsed.host:
            missing.append("host")
        if not parsed.database:
            missing.append("database")
        if missing:
            raise DatabaseConfigurationError(
                "DATABASE_URL is missing PostgreSQL " + ", ".join(missing) + "."
            )
    elif not parsed.drivername.startswith("sqlite"):
        raise DatabaseConfigurationError(
            "DATABASE_URL must use postgresql/postgresql+psycopg2 or sqlite."
        )

    return url


def get_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return validate_database_url(explicit_url)

    db_user = _env("DB_USER", "mky_user", "POSTGRES_USER")
    db_password = _env("DB_PASSWORD", "mky_password", "POSTGRES_PASSWORD")
    db_host = _env("DB_HOST", "localhost")
    db_port = _env("DB_PORT", "5432")
    db_name = _env("DB_NAME", "mky_db", "POSTGRES_DB")
    return validate_database_url(
        f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )


def _mask_database_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<invalid DATABASE_URL>"


def _decode_libpq_message(exc: UnicodeDecodeError) -> str:
    raw = exc.object
    if isinstance(raw, (bytes, bytearray)):
        for encoding in ("cp1251", "cp866", "utf-8"):
            try:
                return bytes(raw).decode(encoding)
            except UnicodeDecodeError:
                continue
        return bytes(raw).decode("utf-8", errors="replace")
    return str(exc)


def format_database_connection_error(exc: BaseException, url: str | None = None) -> str:
    current_url = url or DATABASE_URL
    masked_url = _mask_database_url(current_url)

    if isinstance(exc, UnicodeDecodeError):
        details = _decode_libpq_message(exc).strip()
        return (
            "PostgreSQL connection failed, but psycopg2 could not decode the "
            "localized server message as UTF-8.\n"
            f"DATABASE_URL: {masked_url}\n"
            f"Decoded PostgreSQL message: {details}\n"
            "Most likely the PostgreSQL user/password/database in DATABASE_URL "
            "does not match the running local PostgreSQL instance. For a clean "
            "local setup use backend/.env.example and start PostgreSQL with "
            "mky_user / mky_password / mky_db."
        )

    return (
        "PostgreSQL connection failed.\n"
        f"DATABASE_URL: {masked_url}\n"
        f"Original error: {exc}\n"
        "Check that PostgreSQL is running and that DATABASE_URL points to the "
        "right user, password, host, port, and database."
    )


def raise_friendly_database_error(exc: BaseException) -> None:
    raise RuntimeError(format_database_connection_error(exc)) from exc


DATABASE_URL = get_database_url()

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
