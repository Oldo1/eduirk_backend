from fastapi import APIRouter, Depends, HTTPException, Query, Request, Path as ApiPath
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Any
from auth import get_current_user, get_optional_current_user
from database import SessionLocal, get_db
from models import (
    Appointment,
    AssistantChatMessage,
    AssistantChatSession,
    TPMPKAppointment,
    TPMPKWorkingDay,
    User,
    UserRole,
)
from assistant_access import access_scope_for_role, scoped_session_id
from assistant_settings import (
    AVAILABLE_GIGACHAT_MODELS,
    AssistantSettings,
    get_assistant_settings,
    load_assistant_settings,
    register_settings_listener,
    update_assistant_settings,
)
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
import json
import logging
import os
import re
import uuid

logger = logging.getLogger("assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])
ASSISTANT_QUESTION_HARD_MAX_LENGTH = 100_000
ASSISTANT_SESSION_ID_MAX_LENGTH = max(1, int(os.getenv("ASSISTANT_SESSION_ID_MAX_LENGTH", "120")))
ASSISTANT_HISTORY_LIMIT_MAX = max(1, int(os.getenv("ASSISTANT_HISTORY_LIMIT_MAX", "200")))
ASSISTANT_HISTORY_DEFAULT_LIMIT = min(100, ASSISTANT_HISTORY_LIMIT_MAX)
ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS", "300"))
ASSISTANT_MAX_SESSIONS = int(os.getenv("ASSISTANT_MAX_SESSIONS", "200"))
ASSISTANT_RATE_LIMIT_MAX_ENTRIES = int(os.getenv("ASSISTANT_RATE_LIMIT_MAX_ENTRIES", "1000"))
WARMUP_SESSION_ID = "__warmup__"
ASSISTANT_MISSING_CREDENTIALS_MESSAGE = (
    "Ассистент не настроен: отсутствует ключ GigaChat. "
    "Обратитесь к администратору."
)
ASSISTANT_INVALID_CREDENTIALS_MESSAGE = (
    "Ассистент не настроен: ключ GigaChat отсутствует или некорректен. "
    "Обратитесь к администратору."
)
ASSISTANT_STARTING_MESSAGE = (
    "Ассистент ещё запускается и прогревает базу знаний. "
    "Попробуйте ещё раз через несколько минут."
)
KNOWLEDGE_BASE_INDEX_MISSING_MESSAGE = (
    "База знаний не готова: индекс не найден. "
    "Запустите обновление или полную переиндексацию базы знаний."
)
KNOWLEDGE_BASE_EMPTY_MESSAGE = (
    "База знаний не готова: индекс пуст. "
    "Запустите обновление или полную переиндексацию базы знаний."
)
KNOWLEDGE_BASE_INDEX_ERROR_MESSAGE = (
    "База знаний не готова: не удалось прочитать индекс. "
    "Проверьте Chroma-хранилище и запустите переиндексацию."
)
SESSION_ID_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё._:@-]+$")
APPOINTMENT_QUESTION_RE = re.compile(
    r"(запис|при[её]м|слот|заявк)",
    re.IGNORECASE,
)
PERSONAL_APPOINTMENT_RE = re.compile(
    r"(моя|мо[йею]|у\s+меня|меня|я\s+запис|последн|ближайш|следующ|когда)",
    re.IGNORECASE,
)
_status_lock = Lock()
_warmup_started_at: str | None = None
_warmup_completed_at: str | None = None
_assistant_last_error: str | None = None
_assistant_last_request_error: str | None = None
_assistant_last_request_error_at: str | None = None
_evicted_sessions_total = 0
_rate_limit_lock = Lock()
_rate_limit_buckets: dict[str, list[float]] = {}
_rate_limit_rejections = 0
_metrics_lock = Lock()
_requests_total = 0
_requests_successful = 0
_requests_failed = 0
_request_duration_total = 0.0
_last_request_at: str | None = None
_last_request_duration_seconds: float | None = None
_max_request_duration_seconds: float | None = None

# ── Schemas ───────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASSISTANT_QUESTION_HARD_MAX_LENGTH)
    session_id: str = Field("default", min_length=1, max_length=ASSISTANT_SESSION_ID_MAX_LENGTH)


class AssistantStatusResponse(BaseModel):
    status:              str
    ready:               bool
    vectorstore_ready:   bool
    reranker_ready:      bool
    embeddings_ready:    bool
    knowledge_base_ready: bool
    knowledge_base_status: str
    knowledge_base_message: str | None = None
    assistant_message:   str | None = None
    vector_count:        int | None = None
    sessions:            int
    warmup_started_at:   str | None = None
    warmup_completed_at: str | None = None
    last_error:          str | None = None
    last_request_error:  str | None = None
    last_request_error_at: str | None = None
    session_ttl_seconds: int
    max_sessions:        int
    evicted_sessions:    int
    question_max_length: int
    session_id_max_length: int
    history_limit_max:   int
    gigachat_timeout_seconds: float
    gigachat_max_retries: int
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    rate_limit_active_buckets: int
    rate_limit_rejections: int
    requests_total: int
    requests_successful: int
    requests_failed: int
    average_request_duration_seconds: float | None
    last_request_duration_seconds: float | None
    max_request_duration_seconds: float | None
    last_request_at: str | None


class AssistantAnswerQualityRequest(BaseModel):
    score: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=10)


class AssistantSettingsUpdateRequest(BaseModel):
    update_interval_hours: float = Field(..., gt=0, le=24 * 30)
    gigachat_model: str
    question_max_length: int = Field(..., ge=1, le=ASSISTANT_QUESTION_HARD_MAX_LENGTH)
    session_ttl_seconds: int = Field(..., ge=0, le=365 * 24 * 60 * 60)
    history_max_messages: int = Field(..., ge=0, le=100_000)
    rate_limit_window_seconds: int = Field(..., ge=0, le=24 * 60 * 60)
    rate_limit_max_requests: int = Field(..., ge=0, le=100_000)

    @field_validator("gigachat_model")
    @classmethod
    def _validate_gigachat_model(cls, value: str) -> str:
        clean_value = value.strip()
        if clean_value not in AVAILABLE_GIGACHAT_MODELS:
            raise ValueError("Выберите поддерживаемую модель GigaChat.")
        return clean_value


class AssistantSettingsResponse(AssistantSettingsUpdateRequest):
    available_gigachat_models: list[str]
    updated_at: datetime | None = None


class AssistantNotReadyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

# ── Конфигурация ──────────────────────────────────────────────────────────────

class LazyRAGConfig:
    def __init__(self) -> None:
        self._value: Any | None = None

    def get(self) -> Any:
        if self._value is None:
            from rag_pipeline import RAGConfig

            settings = get_assistant_settings()
            self._value = RAGConfig(
                scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
                model=settings.gigachat_model,
                persist_dir="./chroma_gigachat",
                collection_name=os.getenv("CHROMA_COLLECTION_NAME", "eduirk"),
                top_k=5,
                fetch_k=30,
                memory_turns=5,
            )
        return self._value

    def reset(self) -> None:
        self._value = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.get(), name)


cfg = LazyRAGConfig()


def _missing_gigachat_credentials_message() -> str | None:
    if not os.getenv("GIGACHAT_CREDENTIALS", "").strip():
        return ASSISTANT_MISSING_CREDENTIALS_MESSAGE
    return None


def _is_gigachat_credentials_error(exc: Exception) -> bool:
    text = str(exc)
    return isinstance(exc, ValueError) and (
        "[ключ]" in text or "GIGACHAT_CREDENTIALS" in text
    )


def assistant_startup_error_message(exc: Exception) -> str:
    if isinstance(exc, AssistantNotReadyError):
        return exc.message
    if _is_gigachat_credentials_error(exc):
        return ASSISTANT_INVALID_CREDENTIALS_MESSAGE
    if exc.__class__.__name__ == "KnowledgeBaseNotReadyError":
        return str(exc)
    return "Ошибка прогрева ассистента. Подробности в логах backend."


def _index_storage_exists() -> bool:
    persist_dir = os.path.abspath(cfg.persist_dir)
    return os.path.isfile(os.path.join(persist_dir, "chroma.sqlite3"))

_EMBEDDINGS: Any | None = None
_embeddings_ready = False
_embeddings_lock = Lock()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_assistant_warmup_started() -> None:
    global _warmup_started_at, _warmup_completed_at, _assistant_last_error
    with _status_lock:
        _warmup_started_at = _now_utc_iso()
        _warmup_completed_at = None
        _assistant_last_error = None


def mark_assistant_warmup_completed() -> None:
    global _warmup_completed_at, _assistant_last_error
    with _status_lock:
        _warmup_completed_at = _now_utc_iso()
        _assistant_last_error = None


def set_assistant_last_error(message: str | None) -> None:
    global _assistant_last_error
    with _status_lock:
        _assistant_last_error = message


def set_assistant_last_request_error(message: str | None) -> None:
    global _assistant_last_request_error, _assistant_last_request_error_at
    with _status_lock:
        _assistant_last_request_error = message
        _assistant_last_request_error_at = _now_utc_iso() if message else None


def _mark_embeddings_ready() -> None:
    global _embeddings_ready
    with _status_lock:
        _embeddings_ready = True


def get_embeddings() -> Any:
    global _EMBEDDINGS
    if _EMBEDDINGS is None:
        with _embeddings_lock:
            if _EMBEDDINGS is None:
                from langchain_huggingface import HuggingFaceEmbeddings

                logger.info("[assistant] Loading embeddings model")
                _EMBEDDINGS = HuggingFaceEmbeddings(
                    model_name="intfloat/multilingual-e5-large",
                    model_kwargs={"device": "cpu"},
                    encode_kwargs={"normalize_embeddings": True},
                )
    return _EMBEDDINGS


def warmup_embeddings() -> None:
    embeddings = get_embeddings()
    embeddings.embed_query("warmup")
    _mark_embeddings_ready()
    logger.info("[assistant] Embeddings model ready")


class LazyEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = get_embeddings().embed_documents(texts)
        _mark_embeddings_ready()
        return result

    def embed_query(self, text: str) -> list[float]:
        result = get_embeddings().embed_query(text)
        _mark_embeddings_ready()
        return result


EMBEDDINGS = LazyEmbeddings()

# ── Глобальные объекты ────────────────────────────────────────────────────────
# Один vectorstore на весь процесс — все сессии и updater используют его

_vectorstore: Any | None = None
_vectorstore_lock = Lock()
_sessions: dict[str, Any] = {}
_sessions_lock = Lock()
_session_locks: dict[str, Lock] = {}
_session_accessed_at: dict[str, float] = {}
_last_session_cleanup_at = 0.0


def _get_session_lock(session_id: str) -> Lock:
    with _sessions_lock:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = Lock()
            _session_locks[session_id] = lock
        return lock


def _session_items_snapshot() -> list[tuple[str, Any]]:
    with _sessions_lock:
        return list(_sessions.items())


def _sessions_count() -> int:
    with _sessions_lock:
        return len(_sessions)


def _touch_session_unlocked(session_id: str, now: float | None = None) -> None:
    _session_accessed_at[session_id] = now if now is not None else monotonic()


def cleanup_idle_sessions(force: bool = False) -> int:
    global _last_session_cleanup_at, _evicted_sessions_total

    session_ttl_seconds = get_assistant_settings().session_ttl_seconds
    if session_ttl_seconds <= 0 and ASSISTANT_MAX_SESSIONS <= 0:
        return 0

    now = monotonic()
    with _sessions_lock:
        if (
            not force
            and ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS > 0
            and now - _last_session_cleanup_at < ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS
        ):
            return 0
        _last_session_cleanup_at = now

        user_session_ids = [
            session_id
            for session_id in _sessions
            if session_id != WARMUP_SESSION_ID
        ]
        expired = [
            session_id
            for session_id in user_session_ids
            if session_ttl_seconds > 0
            and now - _session_accessed_at.get(session_id, now) >= session_ttl_seconds
        ]

        overflow: list[str] = []
        if ASSISTANT_MAX_SESSIONS > 0 and len(user_session_ids) > ASSISTANT_MAX_SESSIONS:
            by_lru = sorted(user_session_ids, key=lambda sid: _session_accessed_at.get(sid, 0.0))
            overflow = by_lru[: len(user_session_ids) - ASSISTANT_MAX_SESSIONS]

        candidates = list(dict.fromkeys(expired + overflow))

    evicted = 0
    for session_id in candidates:
        session_lock = _get_session_lock(session_id)
        if not session_lock.acquire(blocking=False):
            continue
        try:
            with _sessions_lock:
                if session_id == WARMUP_SESSION_ID or session_id not in _sessions:
                    continue
                last_access = _session_accessed_at.get(session_id, now)
                expired_now = (
                    session_ttl_seconds > 0
                    and now - last_access >= session_ttl_seconds
                )
                overflow_now = session_id in overflow
                if not expired_now and not overflow_now:
                    continue

                _sessions.pop(session_id, None)
                _session_accessed_at.pop(session_id, None)

            evicted += 1
        finally:
            session_lock.release()

    if evicted:
        with _status_lock:
            _evicted_sessions_total += evicted
        logger.info(f"[assistant] Evicted idle in-memory sessions: {evicted}")

    return evicted


def _rate_limit_enabled() -> bool:
    settings = get_assistant_settings()
    return settings.rate_limit_window_seconds > 0 and settings.rate_limit_max_requests > 0


def _rate_limit_key(request: Request, user: User | None) -> str:
    if user is not None:
        return f"user:{user.id}"
    host = request.client.host if request.client else "unknown"
    return f"anonymous:{host}"


def _cleanup_rate_limit_buckets_unlocked(now: float, settings: AssistantSettings | None = None) -> None:
    settings = settings or get_assistant_settings()
    if settings.rate_limit_window_seconds <= 0 or settings.rate_limit_max_requests <= 0:
        _rate_limit_buckets.clear()
        return

    cutoff = now - settings.rate_limit_window_seconds
    for key, bucket in list(_rate_limit_buckets.items()):
        active = [ts for ts in bucket if ts > cutoff]
        if active:
            _rate_limit_buckets[key] = active
        else:
            _rate_limit_buckets.pop(key, None)

    if ASSISTANT_RATE_LIMIT_MAX_ENTRIES <= 0:
        return
    overflow = len(_rate_limit_buckets) - ASSISTANT_RATE_LIMIT_MAX_ENTRIES
    if overflow <= 0:
        return
    oldest_keys = sorted(
        _rate_limit_buckets,
        key=lambda item: _rate_limit_buckets[item][-1] if _rate_limit_buckets[item] else 0.0,
    )
    for key in oldest_keys[:overflow]:
        _rate_limit_buckets.pop(key, None)


def _check_assistant_rate_limit(request: Request, user: User | None) -> None:
    global _rate_limit_rejections

    settings = get_assistant_settings()
    if settings.rate_limit_window_seconds <= 0 or settings.rate_limit_max_requests <= 0:
        return

    now = monotonic()
    key = _rate_limit_key(request, user)
    cutoff = now - settings.rate_limit_window_seconds
    with _rate_limit_lock:
        bucket = [ts for ts in _rate_limit_buckets.get(key, []) if ts > cutoff]
        if len(bucket) >= settings.rate_limit_max_requests:
            retry_after = max(1, int(bucket[0] + settings.rate_limit_window_seconds - now) + 1)
            _rate_limit_buckets[key] = bucket
            _rate_limit_rejections += 1
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов к ассистенту. Попробуйте через {retry_after} сек.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        _rate_limit_buckets[key] = bucket
        _cleanup_rate_limit_buckets_unlocked(now, settings)


def _rate_limit_stats() -> tuple[int, int]:
    now = monotonic()
    with _rate_limit_lock:
        _cleanup_rate_limit_buckets_unlocked(now)
        return len(_rate_limit_buckets), _rate_limit_rejections


def _record_assistant_request(duration_seconds: float, successful: bool) -> None:
    global _requests_total, _requests_successful, _requests_failed
    global _request_duration_total, _last_request_at
    global _last_request_duration_seconds, _max_request_duration_seconds

    duration_seconds = max(0.0, duration_seconds)
    with _metrics_lock:
        _requests_total += 1
        if successful:
            _requests_successful += 1
        else:
            _requests_failed += 1
        _request_duration_total += duration_seconds
        _last_request_at = _now_utc_iso()
        _last_request_duration_seconds = duration_seconds
        if (
            _max_request_duration_seconds is None
            or duration_seconds > _max_request_duration_seconds
        ):
            _max_request_duration_seconds = duration_seconds

    logger.info(
        "[assistant] ask %s in %.2fs",
        "ok" if successful else "failed",
        duration_seconds,
    )


def _request_metrics_snapshot() -> dict:
    with _metrics_lock:
        average_duration = (
            _request_duration_total / _requests_total
            if _requests_total
            else None
        )
        return {
            "requests_total": _requests_total,
            "requests_successful": _requests_successful,
            "requests_failed": _requests_failed,
            "average_request_duration_seconds": average_duration,
            "last_request_duration_seconds": _last_request_duration_seconds,
            "max_request_duration_seconds": _max_request_duration_seconds,
            "last_request_at": _last_request_at,
        }


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def get_vectorstore() -> Any:
    """Возвращает единый Chroma-объект, создаёт при первом вызове."""
    global _vectorstore
    if _vectorstore is None:
        with _vectorstore_lock:
            if _vectorstore is None:
                from langchain_chroma import Chroma
                from assistant_access import ensure_access_level_metadata

                _vectorstore = Chroma(
                    collection_name=cfg.collection_name,
                    persist_directory=cfg.persist_dir,
                    embedding_function=EMBEDDINGS,
                )
                ensure_access_level_metadata(_vectorstore)
                logger.info(
                    f"[assistant] Vectorstore инициализирован. "
                    f"Векторов: {_vectorstore._collection.count()}"
                )
    return _vectorstore


def init_rag() -> None:
    """Вызывается при старте приложения из lifespan."""
    credentials_message = _missing_gigachat_credentials_message()
    if credentials_message:
        raise AssistantNotReadyError("configuration_error", credentials_message)
    if not _index_storage_exists():
        raise AssistantNotReadyError("knowledge_base_not_ready", KNOWLEDGE_BASE_INDEX_MISSING_MESSAGE)

    get_vectorstore()
    vector_count = _safe_vector_count()
    if vector_count is None:
        raise AssistantNotReadyError("knowledge_base_not_ready", KNOWLEDGE_BASE_INDEX_ERROR_MESSAGE)
    if vector_count <= 0:
        raise AssistantNotReadyError("knowledge_base_not_ready", KNOWLEDGE_BASE_EMPTY_MESSAGE)

    # Прогреваем RAG и reranker на старте, чтобы первый пользовательский
    # запрос не ждал загрузку модели и не срывался на frontend timeout.
    get_rag(WARMUP_SESSION_ID)
    logger.info(f"[assistant] RAG готов. Векторов в базе: {vector_count}")


def _make_rag(session_id: str) -> Any:
    """Создаёт новую RAG-сессию, привязанную к общему vectorstore."""
    from rag_pipeline import RAGSystem

    rag = RAGSystem(cfg.get())
    rag.set_vectorstore(get_vectorstore())
    return rag


def _get_or_create_rag_locked(session_id: str) -> Any:
    now = monotonic()
    with _sessions_lock:
        rag = _sessions.get(session_id)
        if rag is not None:
            _touch_session_unlocked(session_id, now)
    if rag is not None:
        return rag

    rag = _make_rag(session_id)
    with _sessions_lock:
        existing = _sessions.get(session_id)
        if existing is not None:
            _touch_session_unlocked(session_id, now)
            return existing
        _sessions[session_id] = rag
        _touch_session_unlocked(session_id, now)
    return rag


def get_rag(session_id: str) -> Any:
    session_lock = _get_session_lock(session_id)
    with session_lock:
        return _get_or_create_rag_locked(session_id)


def _is_reranker_ready() -> bool:
    with _sessions_lock:
        warmup_rag = _sessions.get(WARMUP_SESSION_ID)
    return bool(warmup_rag and getattr(warmup_rag, "_reranker", None) is not None)


def _safe_vector_count() -> int | None:
    if _vectorstore is None:
        return None
    try:
        return int(_vectorstore._collection.count())
    except Exception as e:
        logger.warning(f"[assistant-status] Failed to read vector count: {e}")
        return None


def _knowledge_base_state(vector_count: int | None) -> tuple[bool, str, str | None]:
    if _vectorstore is None:
        if not _index_storage_exists():
            return False, "index_missing", KNOWLEDGE_BASE_INDEX_MISSING_MESSAGE
        return False, "not_loaded", ASSISTANT_STARTING_MESSAGE
    if vector_count is None:
        return False, "index_error", KNOWLEDGE_BASE_INDEX_ERROR_MESSAGE
    if vector_count <= 0:
        return False, "empty", KNOWLEDGE_BASE_EMPTY_MESSAGE
    return True, "ready", None


def _assistant_not_ready_reason(status: AssistantStatusResponse) -> tuple[str, str] | None:
    if status.ready:
        return None
    if status.assistant_message:
        return status.status, status.assistant_message
    return status.status, ASSISTANT_STARTING_MESSAGE


def _stream_error_payload(exc: Exception) -> tuple[str, str]:
    if exc.__class__.__name__ == "KnowledgeBaseNotReadyError":
        return "knowledge_base_not_ready", str(exc)
    if isinstance(exc, AssistantNotReadyError):
        return exc.code, exc.message
    if _is_gigachat_credentials_error(exc):
        return "configuration_error", ASSISTANT_INVALID_CREDENTIALS_MESSAGE
    return "assistant_error", "Не удалось получить ответ ассистента. Попробуйте позже."


def _looks_like_personal_appointment_question(question: str) -> bool:
    text = (question or "").strip().lower().replace("ё", "е")
    return bool(
        APPOINTMENT_QUESTION_RE.search(text)
        and PERSONAL_APPOINTMENT_RE.search(text)
    )


def _format_date_ru(value: Any) -> str:
    if value is None:
        return "дата не указана"
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
            return parsed.strftime("%d.%m.%Y")
        except ValueError:
            return value
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _format_time_ru(value: Any) -> str:
    if value is None:
        return "время не указано"
    if isinstance(value, str):
        return value[:5]
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)


def _appointment_status_label(status: str | None) -> str:
    return {
        "new": "новая",
        "confirmed": "подтверждена",
        "cancelled": "отменена",
        "done": "завершена",
        "archive": "в архиве",
    }.get((status or "").lower(), status or "не указан")


def _appointment_sort_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get("date") or ""),
        str(item.get("time") or ""),
        str(item.get("created_at") or ""),
    )


def _legacy_user_appointment_filters(model: Any, user: User) -> list[Any]:
    filters = []
    if getattr(user, "email", None):
        filters.append(model.user_email == user.email)
    return filters


def _can_view_unbound_appointments(user: User) -> bool:
    role_name = (getattr(getattr(user, "role", None), "role_name", "") or "").lower()
    return bool(
        getattr(user, "can_access_internal_docs", False)
        or role_name in {"admin", "administrator", "админ", "администратор", "employee", "staff", "сотрудник"}
    )


def _appointment_item_answer_prefix(item: dict) -> str:
    if item.get("unbound"):
        return "К вашей учётной записи запись не привязана. Последняя непривязанная запись"
    return "Ваша последняя найденная запись"


def _user_appointment_items(db: Session, user: User, include_unbound: bool = False) -> list[dict]:
    items: list[dict] = []

    appointment_filters = [Appointment.user_id == user.id]
    appointment_filters.extend(_legacy_user_appointment_filters(Appointment, user))
    for row in (
        db.query(Appointment)
        .filter(
            or_(*appointment_filters),
            Appointment.status != "cancelled",
        )
        .order_by(
            Appointment.appointment_date.desc(),
            Appointment.appointment_time.desc(),
            Appointment.created_at.desc(),
        )
        .limit(10)
        .all()
    ):
        items.append({
            "kind": "запись на приём",
            "date": row.appointment_date,
            "time": row.appointment_time,
            "status": row.status,
            "comment": row.comment,
            "created_at": row.created_at,
            "unbound": False,
        })

    tpmpk_filters = [TPMPKAppointment.user_id == user.id]
    tpmpk_filters.extend(_legacy_user_appointment_filters(TPMPKAppointment, user))
    for appointment, day in (
        db.query(TPMPKAppointment, TPMPKWorkingDay)
        .join(TPMPKWorkingDay, TPMPKWorkingDay.id == TPMPKAppointment.working_day_id)
        .filter(
            or_(*tpmpk_filters),
            TPMPKAppointment.status != "cancelled",
        )
        .order_by(
            TPMPKWorkingDay.date.desc(),
            TPMPKAppointment.start_time.desc(),
            TPMPKAppointment.created_at.desc(),
        )
        .limit(10)
        .all()
    ):
        items.append({
            "kind": "запись ТПМПК",
            "date": day.date,
            "time": appointment.start_time,
            "status": appointment.status,
            "comment": None,
            "created_at": appointment.created_at,
            "unbound": False,
        })

    if include_unbound and not items:
        for row in (
            db.query(Appointment)
            .filter(
                Appointment.user_id.is_(None),
                Appointment.status != "cancelled",
            )
            .order_by(
                Appointment.appointment_date.desc(),
                Appointment.appointment_time.desc(),
                Appointment.created_at.desc(),
            )
            .limit(10)
            .all()
        ):
            items.append({
                "kind": "запись на приём",
                "date": row.appointment_date,
                "time": row.appointment_time,
                "status": row.status,
                "comment": row.comment,
                "created_at": row.created_at,
                "unbound": True,
            })

        for appointment, day in (
            db.query(TPMPKAppointment, TPMPKWorkingDay)
            .join(TPMPKWorkingDay, TPMPKWorkingDay.id == TPMPKAppointment.working_day_id)
            .filter(
                TPMPKAppointment.user_id.is_(None),
                TPMPKAppointment.status != "cancelled",
            )
            .order_by(
                TPMPKWorkingDay.date.desc(),
                TPMPKAppointment.start_time.desc(),
                TPMPKAppointment.created_at.desc(),
            )
            .limit(10)
            .all()
        ):
            items.append({
                "kind": "непривязанная запись ТПМПК",
                "date": day.date,
                "time": appointment.start_time,
                "status": appointment.status,
                "comment": None,
                "created_at": appointment.created_at,
                "unbound": True,
            })

    return sorted(items, key=_appointment_sort_key, reverse=True)


def _appointment_answer_result(
    db: Session,
    user: User | None,
    question: str,
) -> dict | None:
    if not _looks_like_personal_appointment_question(question):
        return None

    if user is None:
        return {
            "answer": (
                "Чтобы посмотреть вашу запись, нужно авторизоваться. "
                "После входа я смогу назвать дату, время и статус записей, "
                "которые привязаны к вашей учётной записи."
            ),
            "rewritten_question": question,
            "sources": [],
            "access_scope": "personal",
            "intent": "appointment_lookup",
        }

    try:
        items = _user_appointment_items(
            db,
            user,
            include_unbound=_can_view_unbound_appointments(user),
        )
    except Exception:
        logger.exception("[assistant] Failed to read user appointments")
        return {
            "answer": (
                "Сейчас не удалось получить данные о ваших записях. "
                "Попробуйте позже или обратитесь в учреждение для уточнения."
            ),
            "rewritten_question": question,
            "sources": [],
            "access_scope": "personal",
            "intent": "appointment_lookup",
        }

    if not items:
        return {
            "answer": (
                "У вашей учётной записи пока нет сохранённых записей. "
                "Если запись оформлялась до авторизации или без входа в аккаунт, "
                "она может быть не привязана к профилю. Для уточнения обратитесь в учреждение."
            ),
            "rewritten_question": question,
            "sources": [],
            "access_scope": "personal",
            "intent": "appointment_lookup",
        }

    item = items[0]
    answer = (
        f"{_appointment_item_answer_prefix(item)}: {_format_date_ru(item['date'])} "
        f"в {_format_time_ru(item['time'])}. "
        f"Тип: {item['kind']}. "
        f"Статус: {_appointment_status_label(item.get('status'))}."
    )
    if item.get("comment"):
        answer += f" Комментарий: {item['comment']}."

    return {
        "answer": answer,
        "rewritten_question": question,
        "sources": [],
        "access_scope": "personal",
        "intent": "appointment_lookup",
    }


def get_assistant_status() -> AssistantStatusResponse:
    cleanup_idle_sessions()
    settings = get_assistant_settings()
    vectorstore_ready = _vectorstore is not None
    reranker_ready = _is_reranker_ready()
    vector_count = _safe_vector_count()
    knowledge_base_ready, knowledge_base_status, knowledge_base_message = _knowledge_base_state(vector_count)
    credentials_message = _missing_gigachat_credentials_message()
    rate_limit_active_buckets, rate_limit_rejections = _rate_limit_stats()
    request_metrics = _request_metrics_snapshot()
    with _status_lock:
        embeddings_ready = _embeddings_ready
        warmup_started_at = _warmup_started_at
        warmup_completed_at = _warmup_completed_at
        last_error = _assistant_last_error
        last_request_error = _assistant_last_request_error
        last_request_error_at = _assistant_last_request_error_at
        evicted_sessions = _evicted_sessions_total

    ready = bool(
        vectorstore_ready
        and knowledge_base_ready
        and reranker_ready
        and embeddings_ready
        and warmup_completed_at
        and not last_error
        and not credentials_message
    )
    if credentials_message:
        status = "configuration_error"
        assistant_message = credentials_message
    elif knowledge_base_status in {"index_missing", "empty", "index_error"}:
        status = "knowledge_base_not_ready"
        assistant_message = knowledge_base_message
    elif last_error:
        status = "error"
        assistant_message = last_error
    elif ready:
        status = "ready"
        assistant_message = None
    elif warmup_started_at:
        status = "warming_up"
        assistant_message = ASSISTANT_STARTING_MESSAGE
    else:
        status = "starting"
        assistant_message = ASSISTANT_STARTING_MESSAGE

    return AssistantStatusResponse(
        status=status,
        ready=ready,
        vectorstore_ready=vectorstore_ready,
        reranker_ready=reranker_ready,
        embeddings_ready=embeddings_ready,
        knowledge_base_ready=knowledge_base_ready,
        knowledge_base_status=knowledge_base_status,
        knowledge_base_message=knowledge_base_message,
        assistant_message=assistant_message,
        vector_count=vector_count,
        sessions=_sessions_count(),
        warmup_started_at=warmup_started_at,
        warmup_completed_at=warmup_completed_at,
        last_error=last_error,
        last_request_error=last_request_error,
        last_request_error_at=last_request_error_at,
        session_ttl_seconds=settings.session_ttl_seconds,
        max_sessions=ASSISTANT_MAX_SESSIONS,
        evicted_sessions=evicted_sessions,
        question_max_length=settings.question_max_length,
        session_id_max_length=ASSISTANT_SESSION_ID_MAX_LENGTH,
        history_limit_max=ASSISTANT_HISTORY_LIMIT_MAX,
        gigachat_timeout_seconds=cfg.request_timeout,
        gigachat_max_retries=cfg.max_retries,
        rate_limit_window_seconds=settings.rate_limit_window_seconds,
        rate_limit_max_requests=settings.rate_limit_max_requests,
        rate_limit_active_buckets=rate_limit_active_buckets,
        rate_limit_rejections=rate_limit_rejections,
        **request_metrics,
    )


def _user_role(db: Session, user: User | None) -> UserRole | None:
    if user is None or user.role_id is None:
        return None
    return db.query(UserRole).filter(UserRole.id == user.role_id).first()


def _user_role_name(db: Session, user: User | None) -> str | None:
    role = _user_role(db, user)
    return role.role_name if role else None


def _session_context(db: Session, user: User | None, session_id: str) -> tuple[str | None, str, str, str]:
    role = _user_role(db, user)
    role_name = role.role_name if role else None
    access_scope = access_scope_for_role(
        role_name,
        can_access_internal_docs=getattr(role, "can_access_internal_docs", False),
    )
    clean_session_id = _validated_session_id(session_id)
    session_key = scoped_session_id(
        clean_session_id,
        access_scope,
        user.id if user else None,
    )
    return role_name, access_scope, clean_session_id, session_key


def _user_history_payload(user: User | None) -> dict:
    if user is None:
        return {"id": None, "email": None}
    return {
        "id": user.id,
        "email": user.email,
    }


def _validation_error(message: str) -> None:
    raise HTTPException(status_code=422, detail=message)


def _validated_question(question: str) -> str:
    clean_question = (question or "").strip()
    if not clean_question:
        _validation_error("Вопрос не должен быть пустым.")
    question_max_length = get_assistant_settings().question_max_length
    if len(clean_question) > question_max_length:
        _validation_error(f"Вопрос слишком длинный. Максимум: {question_max_length} символов.")
    return clean_question


def _validated_session_id(session_id: str | None) -> str:
    clean_session_id = (session_id or "default").strip() or "default"
    if len(clean_session_id) > ASSISTANT_SESSION_ID_MAX_LENGTH:
        _validation_error(f"session_id слишком длинный. Максимум: {ASSISTANT_SESSION_ID_MAX_LENGTH} символов.")
    if not SESSION_ID_RE.fullmatch(clean_session_id):
        _validation_error(
            "session_id может содержать только буквы, цифры, точку, дефис, подчёркивание, двоеточие и @."
        )
    return clean_session_id


def _dt_iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _history_session_query(db: Session, session_key: str):
    return db.query(AssistantChatSession).filter(
        AssistantChatSession.session_key == session_key
    )


def _sync_history_session(
    db: Session,
    *,
    session_key: str,
    session_id: str,
    user: User | None,
    user_role: str | None,
    access_scope: str,
) -> AssistantChatSession:
    now = datetime.now(timezone.utc)
    session = _history_session_query(db, session_key).first()
    if session is None:
        session = AssistantChatSession(
            session_key=session_key,
            session_id=session_id,
            access_scope=access_scope,
            user_role=user_role,
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        db.flush()
        return session

    session.session_id = session_id
    session.access_scope = access_scope
    session.user_role = user_role
    session.user_id = user.id if user else None
    session.user_email = user.email if user else None
    session.updated_at = now
    db.flush()
    return session


def _db_user_history_payload(session: AssistantChatSession) -> dict:
    return {
        "id": session.user_id,
        "email": session.user_email,
    }


def _normalize_manual_quality_payload(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    normalized = dict(value)
    raw_score = normalized.get("score")
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        return None
    normalized["score"] = max(1, min(5, score))
    normalized["max_score"] = 5
    return normalized


def _db_message_payload(message: AssistantChatMessage) -> dict:
    payload = {
        "id": f"{message.turn_id}:{message.role}" if message.turn_id else str(message.id),
        "db_id": message.id,
        "turn_id": message.turn_id,
        "role": message.role,
        "content": message.content,
        "created_at": _dt_iso(message.created_at),
    }
    if message.message_metadata:
        metadata = dict(message.message_metadata)
        metadata.pop("quality", None)
        manual_quality = _normalize_manual_quality_payload(metadata.get("manual_quality"))
        if manual_quality:
            metadata["manual_quality"] = manual_quality
            payload["manual_quality"] = manual_quality
            payload["quality"] = manual_quality
        else:
            metadata.pop("manual_quality", None)
            payload["manual_quality"] = None
            payload["quality"] = None
        payload["metadata"] = metadata
    return payload


def _db_session_payload(
    session: AssistantChatSession,
    messages: list[AssistantChatMessage],
) -> dict:
    return {
        "session_id": session.session_id,
        "scoped_session_id": session.session_key,
        "access_scope": session.access_scope,
        "user_role": session.user_role,
        "user": _db_user_history_payload(session),
        "created_at": _dt_iso(session.created_at),
        "updated_at": _dt_iso(session.updated_at),
        "messages": [_db_message_payload(message) for message in messages],
    }


def _get_session_history(
    db: Session,
    session_key: str,
    fallback: dict,
    limit: int | None = None,
) -> dict:
    session = _history_session_query(db, session_key).first()
    if session is None:
        return fallback

    query = db.query(AssistantChatMessage).filter(
        AssistantChatMessage.assistant_session_id == session.id
    )
    if limit and limit > 0:
        messages = list(
            reversed(
                query.order_by(AssistantChatMessage.id.desc())
                .limit(limit)
                .all()
            )
        )
    else:
        messages = query.order_by(AssistantChatMessage.id.asc()).all()
    return _db_session_payload(session, messages)


def _get_answer_message_or_404(db: Session, message_id: int) -> AssistantChatMessage:
    message = db.query(AssistantChatMessage).filter(
        AssistantChatMessage.id == message_id,
        AssistantChatMessage.role == "assistant",
    ).first()
    if message is None:
        raise HTTPException(status_code=404, detail="Ответ ассистента не найден")
    return message


def _question_for_answer(db: Session, message: AssistantChatMessage) -> AssistantChatMessage | None:
    return db.query(AssistantChatMessage).filter(
        AssistantChatMessage.assistant_session_id == message.assistant_session_id,
        AssistantChatMessage.turn_id == message.turn_id,
        AssistantChatMessage.role == "user",
    ).order_by(AssistantChatMessage.id.asc()).first()


def _clean_quality_tags(tags: list[str]) -> list[str]:
    clean_tags: list[str] = []
    for tag in tags:
        clean_tag = str(tag or "").strip()
        if clean_tag:
            clean_tags.append(clean_tag[:64])
        if len(clean_tags) >= 10:
            break
    return clean_tags


def _manual_quality_payload(
    body: AssistantAnswerQualityRequest,
    current_user: User,
    user_role: str | None,
) -> dict | None:
    if body.score is None:
        return None
    comment = body.comment.strip() if body.comment else None
    return {
        "score": body.score,
        "max_score": 5,
        "comment": comment or None,
        "tags": _clean_quality_tags(body.tags),
        "rated_at": _now_utc_iso(),
        "rated_by": {
            "id": current_user.id,
            "email": current_user.email,
            "role": user_role,
        },
    }


def _answer_quality_payload(db: Session, message: AssistantChatMessage) -> dict:
    message_payload = _db_message_payload(message)
    metadata = message_payload.get("metadata") or {}
    question_message = _question_for_answer(db, message)
    session = message.session
    return {
        "message_id": message.id,
        "turn_id": message.turn_id,
        "session": {
            "id": session.id if session else None,
            "session_id": session.session_id if session else None,
            "scoped_session_id": session.session_key if session else None,
            "access_scope": session.access_scope if session else None,
            "user_role": session.user_role if session else None,
            "user": _db_user_history_payload(session) if session else None,
        },
        "question": question_message.content if question_message else None,
        "answer": message.content,
        "created_at": _dt_iso(message.created_at),
        "quality": message_payload.get("manual_quality"),
        "manual_quality": message_payload.get("manual_quality"),
        "rated": message_payload.get("manual_quality") is not None,
        "sources": metadata.get("sources", []),
        "metadata": metadata,
    }


def _append_history_turn(
    db: Session,
    *,
    session_key: str,
    session_id: str,
    user: User | None,
    user_role: str | None,
    access_scope: str,
    question: str,
    result: dict,
) -> None:
    created_at = datetime.now(timezone.utc)
    turn_id = uuid.uuid4().hex
    try:
        session = _sync_history_session(
            db,
            session_key=session_key,
            session_id=session_id,
            user=user,
            user_role=user_role,
            access_scope=access_scope,
        )
        db.add_all(
            [
                AssistantChatMessage(
                    assistant_session_id=session.id,
                    turn_id=turn_id,
                    role="user",
                    content=question,
                    created_at=created_at,
                ),
                AssistantChatMessage(
                    assistant_session_id=session.id,
                    turn_id=turn_id,
                    role="assistant",
                    content=result.get("answer", ""),
                    created_at=created_at,
                    message_metadata={
                        "rewritten_question": result.get("rewritten_question", ""),
                        "sources": result.get("sources", []),
                        "access_scope": result.get("access_scope", access_scope),
                        "user_role": user_role,
                        "storage_purpose": "staff_analysis",
                    },
                ),
            ]
        )
        db.flush()
        history_max_messages = get_assistant_settings().history_max_messages
        if history_max_messages > 0:
            message_count = db.query(AssistantChatMessage).filter(
                AssistantChatMessage.assistant_session_id == session.id
            ).count()
            overflow = message_count - history_max_messages
            if overflow > 0:
                old_ids = [
                    row.id
                    for row in db.query(AssistantChatMessage.id)
                    .filter(AssistantChatMessage.assistant_session_id == session.id)
                    .order_by(AssistantChatMessage.id.asc())
                    .limit(overflow)
                    .all()
                ]
                if old_ids:
                    db.query(AssistantChatMessage).filter(
                        AssistantChatMessage.id.in_(old_ids)
                    ).delete(synchronize_session=False)
        session.updated_at = created_at
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[assistant-history] Failed to save dialog history to database")
        raise


def _clear_session_history(
    db: Session,
    *,
    session_key: str,
    session_id: str,
    user: User | None,
    user_role: str | None,
    access_scope: str,
) -> int:
    try:
        session = _sync_history_session(
            db,
            session_key=session_key,
            session_id=session_id,
            user=user,
            user_role=user_role,
            access_scope=access_scope,
        )
        deleted = db.query(AssistantChatMessage).filter(
            AssistantChatMessage.assistant_session_id == session.id
        ).delete(synchronize_session=False)
        session.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[assistant-history] Failed to clear dialog history in database")
        raise
    return int(deleted or 0)


def reload_all_sessions(stats: dict | None = None) -> None:
    """
    Вызывается планировщиком после обновления индекса.
    Перепривязывает все сессии к обновлённому vectorstore,
    сохраняя историю диалогов.
    """
    cleanup_idle_sessions()
    vs    = get_vectorstore()
    count = vs._collection.count()
    session_items = _session_items_snapshot()
    logger.info(
        f"[assistant] Перезагружаю {len(session_items)} сессий "
        f"(векторов: {count})"
    )
    for session_id, rag in session_items:
        with _get_session_lock(session_id):
            rag.set_vectorstore(vs)

    if stats:
        site = stats.get("site", {})
        s3   = stats.get("s3",   {})
        logger.info(
            f"[assistant] Обновление: сайт +{site.get('added',0)} "
            f"~{site.get('updated',0)} -{site.get('removed',0)} | "
            f"S3 +{s3.get('added',0)} -{s3.get('removed',0)}"
        )


def _settings_payload(settings: AssistantSettings) -> AssistantSettingsResponse:
    return AssistantSettingsResponse(
        **settings.__dict__,
        available_gigachat_models=list(AVAILABLE_GIGACHAT_MODELS),
    )


def _settings_role_name(user: User) -> str:
    role = getattr(user, "role", None)
    if isinstance(role, str):
        return role.lower()
    return (getattr(role, "role_name", None) or "").lower()


def _require_settings_admin(current_user: User = Depends(get_current_user)) -> User:
    if _settings_role_name(current_user) not in {"admin", "methodist"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав для изменения настроек ассистента.")
    return current_user


def _drop_active_user_sessions() -> int:
    with _sessions_lock:
        session_ids = [
            session_id
            for session_id in _sessions
            if session_id != WARMUP_SESSION_ID
        ]
        for session_id in session_ids:
            _sessions.pop(session_id, None)
            _session_accessed_at.pop(session_id, None)
    return len(session_ids)


def _apply_assistant_settings(previous: AssistantSettings, current: AssistantSettings) -> None:
    if previous.gigachat_model != current.gigachat_model:
        cfg.reset()
        dropped_sessions = _drop_active_user_sessions()
        logger.info(
            "[assistant-settings] GigaChat model changed to %s; dropped %s active sessions",
            current.gigachat_model,
            dropped_sessions,
        )

    if (
        previous.rate_limit_window_seconds != current.rate_limit_window_seconds
        or previous.rate_limit_max_requests != current.rate_limit_max_requests
    ):
        with _rate_limit_lock:
            _rate_limit_buckets.clear()

    if previous.session_ttl_seconds != current.session_ttl_seconds:
        cleanup_idle_sessions(force=True)


register_settings_listener(_apply_assistant_settings)


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=AssistantStatusResponse)
def status():
    return get_assistant_status()


@router.get("/settings", response_model=AssistantSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(_require_settings_admin),
):
    return _settings_payload(load_assistant_settings(db))


@router.put("/settings", response_model=AssistantSettingsResponse)
def put_settings(
    body: AssistantSettingsUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_require_settings_admin),
):
    settings = update_assistant_settings(db, **body.model_dump())
    return _settings_payload(settings)


@router.get("/quality")
def list_answer_quality(
    limit: int = Query(ASSISTANT_HISTORY_DEFAULT_LIMIT, ge=1, le=ASSISTANT_HISTORY_LIMIT_MAX),
    session_id: str | None = Query(None, min_length=1, max_length=ASSISTANT_SESSION_ID_MAX_LENGTH),
    rated_only: bool = Query(False),
    min_score: int | None = Query(None, ge=1, le=5),
    max_score: int | None = Query(None, ge=1, le=5),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if min_score is not None and max_score is not None and min_score > max_score:
        _validation_error("min_score не может быть больше max_score.")

    query = db.query(AssistantChatMessage).join(AssistantChatSession).filter(
        AssistantChatMessage.role == "assistant"
    )
    if session_id:
        query = query.filter(AssistantChatSession.session_id == _validated_session_id(session_id))

    fetch_limit = min(max(limit * 5, limit), 1000)
    messages = query.order_by(AssistantChatMessage.id.desc()).limit(fetch_limit).all()

    items: list[dict] = []
    for message in messages:
        payload = _answer_quality_payload(db, message)
        manual_quality = payload.get("manual_quality")

        if rated_only and not manual_quality:
            continue
        score = manual_quality.get("score") if isinstance(manual_quality, dict) else None
        if score is not None:
            if min_score is not None and score < min_score:
                continue
            if max_score is not None and score > max_score:
                continue
        elif min_score is not None or max_score is not None:
            continue

        items.append(payload)
        if len(items) >= limit:
            break

    return {
        "items": items,
        "count": len(items),
        "max_score": 5,
        "rated_only": rated_only,
    }


@router.get("/quality/{message_id}")
def get_answer_quality(
    message_id: int = ApiPath(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message = _get_answer_message_or_404(db, message_id)
    return _answer_quality_payload(db, message)


@router.post("/quality/{message_id}")
def rate_answer_quality(
    body: AssistantAnswerQualityRequest,
    message_id: int = ApiPath(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message = _get_answer_message_or_404(db, message_id)
    metadata = dict(message.message_metadata or {})
    metadata.pop("quality", None)
    manual_quality = _manual_quality_payload(
        body,
        current_user,
        _user_role_name(db, current_user),
    )
    if manual_quality is None:
        metadata.pop("manual_quality", None)
    else:
        metadata["manual_quality"] = manual_quality
    metadata["storage_purpose"] = "staff_analysis"
    message.message_metadata = metadata
    db.commit()
    db.refresh(message)
    return _answer_quality_payload(db, message)


@router.post("/ask/stream")
def ask_stream(
    body: AskRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    started_at = monotonic()
    try:
        cleanup_idle_sessions()
        question = _validated_question(body.question)
        role_name, access_scope, clean_session_id, session_key = _session_context(db, current_user, body.session_id)
        _check_assistant_rate_limit(request, current_user)
    except Exception:
        _record_assistant_request(monotonic() - started_at, False)
        raise

    def event_stream():
        successful = False
        history_db = SessionLocal()
        try:
            yield _sse("status", {"stage": "queued", "message": "Запрос принят"})
            direct_result = _appointment_answer_result(history_db, current_user, question)
            if direct_result is not None:
                _append_history_turn(
                    history_db,
                    session_key=session_key,
                    session_id=clean_session_id,
                    user=current_user,
                    user_role=role_name,
                    access_scope=access_scope,
                    question=question,
                    result=direct_result,
                )
                set_assistant_last_request_error(None)
                successful = True
                yield _sse("token", {"content": direct_result["answer"]})
                yield _sse("done", {**direct_result, "user_role": role_name})
                return

            not_ready = _assistant_not_ready_reason(get_assistant_status())
            if not_ready is not None:
                code, message = not_ready
                set_assistant_last_request_error(message)
                yield _sse("status", {"stage": "not_ready", "message": message})
                yield _sse("error", {"code": code, "detail": message})
                return

            with _get_session_lock(session_key):
                rag = _get_or_create_rag_locked(session_key)
                for event in rag.ask_stream(question, access_scope=access_scope):
                    event_type = event.get("type", "status")
                    if event_type == "token":
                        yield _sse("token", {"content": event.get("content", "")})
                    elif event_type == "done":
                        result = event.get("result") or {}
                        _append_history_turn(
                            history_db,
                            session_key=session_key,
                            session_id=clean_session_id,
                            user=current_user,
                            user_role=role_name,
                            access_scope=access_scope,
                            question=question,
                            result=result,
                        )
                        set_assistant_last_request_error(None)
                        successful = True
                        yield _sse("done", {**result, "user_role": role_name})
                    elif event_type == "rewritten_question":
                        yield _sse("rewritten_question", {
                            "rewritten_question": event.get("rewritten_question", question)
                        })
                    elif event_type == "sources":
                        yield _sse("sources", {"sources": event.get("sources", [])})
                    else:
                        yield _sse("status", {
                            "stage": event.get("stage", event_type),
                            "message": event.get("message", ""),
                        })
        except Exception as exc:
            code, message = _stream_error_payload(exc)
            set_assistant_last_request_error(message)
            logger.exception("[assistant] Failed to stream answer")
            yield _sse("error", {
                "code": code,
                "detail": message,
            })
        finally:
            history_db.close()
            _record_assistant_request(monotonic() - started_at, successful)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/clear/{session_id}")
def clear_history(
    session_id: str = ApiPath(..., min_length=1, max_length=ASSISTANT_SESSION_ID_MAX_LENGTH),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    cleanup_idle_sessions()
    role_name, access_scope, clean_session_id, session_key = _session_context(db, current_user, session_id)
    with _get_session_lock(session_key):
        with _sessions_lock:
            rag = _sessions.get(session_key)
            if rag is not None:
                _touch_session_unlocked(session_key)
        if rag is not None:
            rag.clear_memory()
        deleted_messages = _clear_session_history(
            db,
            session_key=session_key,
            session_id=clean_session_id,
            user=current_user,
            user_role=role_name,
            access_scope=access_scope,
        )
    return {
        "status": "ok",
        "session_id": clean_session_id,
        "scoped_session_id": session_key,
        "deleted_messages": deleted_messages,
    }


@router.get("/history/{session_id}")
def get_history(
    session_id: str = ApiPath(..., min_length=1, max_length=ASSISTANT_SESSION_ID_MAX_LENGTH),
    limit: int = Query(ASSISTANT_HISTORY_DEFAULT_LIMIT, ge=1, le=ASSISTANT_HISTORY_LIMIT_MAX),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    role_name, access_scope, clean_session_id, session_key = _session_context(db, current_user, session_id)
    return _get_session_history(
        db,
        session_key,
        fallback={
            "session_id": clean_session_id,
            "scoped_session_id": session_key,
            "access_scope": access_scope,
            "user_role": role_name,
            "user": _user_history_payload(current_user),
            "messages": [],
        },
        limit=limit,
    )
