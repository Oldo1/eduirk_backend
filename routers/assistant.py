from fastapi import APIRouter, Depends, HTTPException, Query, Request, Path as ApiPath
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any
from auth import get_optional_current_user
from database import get_db
from models import User, UserRole
from assistant_access import access_scope_for_role, scoped_session_id
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic
import hashlib
import json
import logging
import os
import re
import uuid

logger = logging.getLogger("assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])
ASSISTANT_HISTORY_FILE = Path(os.getenv("ASSISTANT_HISTORY_FILE", "./chroma_gigachat/assistant_history.json"))
ASSISTANT_HISTORY_DIR = Path(os.getenv("ASSISTANT_HISTORY_DIR", str(ASSISTANT_HISTORY_FILE.with_suffix(""))))
ASSISTANT_HISTORY_MAX_MESSAGES = int(os.getenv("ASSISTANT_HISTORY_MAX_MESSAGES", "400"))
ASSISTANT_QUESTION_MAX_LENGTH = max(1, int(os.getenv("ASSISTANT_QUESTION_MAX_LENGTH", "4000")))
ASSISTANT_SESSION_ID_MAX_LENGTH = max(1, int(os.getenv("ASSISTANT_SESSION_ID_MAX_LENGTH", "120")))
ASSISTANT_HISTORY_LIMIT_MAX = max(1, int(os.getenv("ASSISTANT_HISTORY_LIMIT_MAX", "200")))
ASSISTANT_HISTORY_DEFAULT_LIMIT = min(100, ASSISTANT_HISTORY_LIMIT_MAX)
ASSISTANT_SESSION_TTL_SECONDS = int(os.getenv("ASSISTANT_SESSION_TTL_SECONDS", str(3 * 60 * 60)))
ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ASSISTANT_SESSION_CLEANUP_INTERVAL_SECONDS", "300"))
ASSISTANT_MAX_SESSIONS = int(os.getenv("ASSISTANT_MAX_SESSIONS", "200"))
ASSISTANT_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("ASSISTANT_RATE_LIMIT_WINDOW_SECONDS", "60"))
ASSISTANT_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("ASSISTANT_RATE_LIMIT_MAX_REQUESTS", "12"))
ASSISTANT_RATE_LIMIT_MAX_ENTRIES = int(os.getenv("ASSISTANT_RATE_LIMIT_MAX_ENTRIES", "1000"))
WARMUP_SESSION_ID = "__warmup__"
SESSION_ID_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё._:@-]+$")
_history_lock = Lock()
_hydrated_sessions: set[str] = set()
_hydrated_sessions_lock = Lock()
_status_lock = Lock()
_warmup_started_at: str | None = None
_warmup_completed_at: str | None = None
_assistant_last_error: str | None = None
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
    question: str = Field(..., min_length=1, max_length=ASSISTANT_QUESTION_MAX_LENGTH)
    session_id: str = Field("default", min_length=1, max_length=ASSISTANT_SESSION_ID_MAX_LENGTH)

class AskResponse(BaseModel):
    answer:             str
    rewritten_question: str
    sources:            list[dict]
    access_scope:       str
    user_role:          str | None = None


class AssistantStatusResponse(BaseModel):
    status:              str
    ready:               bool
    vectorstore_ready:   bool
    reranker_ready:      bool
    embeddings_ready:    bool
    vector_count:        int | None = None
    sessions:            int
    warmup_started_at:   str | None = None
    warmup_completed_at: str | None = None
    last_error:          str | None = None
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

# ── Конфигурация ──────────────────────────────────────────────────────────────

class LazyRAGConfig:
    def __init__(self) -> None:
        self._value: Any | None = None

    def get(self) -> Any:
        if self._value is None:
            from rag_pipeline import RAGConfig

            self._value = RAGConfig(
                scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
                model=os.getenv("GIGACHAT_MODEL", "GigaChat"),
                persist_dir="./chroma_gigachat",
                collection_name="eduirk",
                top_k=5,
                fetch_k=30,
                memory_turns=5,
            )
        return self._value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.get(), name)


cfg = LazyRAGConfig()

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


def _discard_hydrated_session(session_id: str) -> None:
    with _hydrated_sessions_lock:
        _hydrated_sessions.discard(session_id)


def cleanup_idle_sessions(force: bool = False) -> int:
    global _last_session_cleanup_at, _evicted_sessions_total

    if ASSISTANT_SESSION_TTL_SECONDS <= 0 and ASSISTANT_MAX_SESSIONS <= 0:
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
            if ASSISTANT_SESSION_TTL_SECONDS > 0
            and now - _session_accessed_at.get(session_id, now) >= ASSISTANT_SESSION_TTL_SECONDS
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
                    ASSISTANT_SESSION_TTL_SECONDS > 0
                    and now - last_access >= ASSISTANT_SESSION_TTL_SECONDS
                )
                overflow_now = session_id in overflow
                if not expired_now and not overflow_now:
                    continue

                _sessions.pop(session_id, None)
                _session_accessed_at.pop(session_id, None)

            _discard_hydrated_session(session_id)
            evicted += 1
        finally:
            session_lock.release()

    if evicted:
        with _status_lock:
            _evicted_sessions_total += evicted
        logger.info(f"[assistant] Evicted idle in-memory sessions: {evicted}")

    return evicted


def _rate_limit_enabled() -> bool:
    return ASSISTANT_RATE_LIMIT_WINDOW_SECONDS > 0 and ASSISTANT_RATE_LIMIT_MAX_REQUESTS > 0


def _rate_limit_key(request: Request, user: User | None) -> str:
    if user is not None:
        return f"user:{user.id}"
    host = request.client.host if request.client else "unknown"
    return f"anonymous:{host}"


def _cleanup_rate_limit_buckets_unlocked(now: float) -> None:
    if not _rate_limit_enabled():
        _rate_limit_buckets.clear()
        return

    cutoff = now - ASSISTANT_RATE_LIMIT_WINDOW_SECONDS
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

    if not _rate_limit_enabled():
        return

    now = monotonic()
    key = _rate_limit_key(request, user)
    cutoff = now - ASSISTANT_RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        bucket = [ts for ts in _rate_limit_buckets.get(key, []) if ts > cutoff]
        if len(bucket) >= ASSISTANT_RATE_LIMIT_MAX_REQUESTS:
            retry_after = max(1, int(bucket[0] + ASSISTANT_RATE_LIMIT_WINDOW_SECONDS - now) + 1)
            _rate_limit_buckets[key] = bucket
            _rate_limit_rejections += 1
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов к ассистенту. Попробуйте через {retry_after} сек.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        _rate_limit_buckets[key] = bucket
        _cleanup_rate_limit_buckets_unlocked(now)


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


def get_vectorstore() -> Any:
    """Возвращает единый Chroma-объект, создаёт при первом вызове."""
    global _vectorstore
    if _vectorstore is None:
        with _vectorstore_lock:
            if _vectorstore is None:
                from langchain_chroma import Chroma

                _vectorstore = Chroma(
                    collection_name=cfg.collection_name,
                    persist_directory=cfg.persist_dir,
                    embedding_function=EMBEDDINGS,
                )
                logger.info(
                    f"[assistant] Vectorstore инициализирован. "
                    f"Векторов: {_vectorstore._collection.count()}"
                )
    return _vectorstore


def init_rag() -> None:
    """Вызывается при старте приложения из lifespan."""
    vs = get_vectorstore()
    # Прогреваем RAG и reranker на старте, чтобы первый пользовательский
    # запрос не ждал загрузку модели и не срывался на frontend timeout.
    get_rag(WARMUP_SESSION_ID)
    logger.info(f"[assistant] RAG готов. Векторов в базе: {vs._collection.count()}")


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


def get_assistant_status() -> AssistantStatusResponse:
    cleanup_idle_sessions()
    vectorstore_ready = _vectorstore is not None
    reranker_ready = _is_reranker_ready()
    vector_count = _safe_vector_count()
    rate_limit_active_buckets, rate_limit_rejections = _rate_limit_stats()
    request_metrics = _request_metrics_snapshot()
    with _status_lock:
        embeddings_ready = _embeddings_ready
        warmup_started_at = _warmup_started_at
        warmup_completed_at = _warmup_completed_at
        last_error = _assistant_last_error
        evicted_sessions = _evicted_sessions_total

    ready = bool(
        vectorstore_ready
        and reranker_ready
        and embeddings_ready
        and warmup_completed_at
        and not last_error
    )
    if last_error:
        status = "error"
    elif ready:
        status = "ready"
    elif warmup_started_at:
        status = "warming_up"
    else:
        status = "starting"

    return AssistantStatusResponse(
        status=status,
        ready=ready,
        vectorstore_ready=vectorstore_ready,
        reranker_ready=reranker_ready,
        embeddings_ready=embeddings_ready,
        vector_count=vector_count,
        sessions=_sessions_count(),
        warmup_started_at=warmup_started_at,
        warmup_completed_at=warmup_completed_at,
        last_error=last_error,
        session_ttl_seconds=ASSISTANT_SESSION_TTL_SECONDS,
        max_sessions=ASSISTANT_MAX_SESSIONS,
        evicted_sessions=evicted_sessions,
        question_max_length=ASSISTANT_QUESTION_MAX_LENGTH,
        session_id_max_length=ASSISTANT_SESSION_ID_MAX_LENGTH,
        history_limit_max=ASSISTANT_HISTORY_LIMIT_MAX,
        gigachat_timeout_seconds=cfg.request_timeout,
        gigachat_max_retries=cfg.max_retries,
        rate_limit_window_seconds=ASSISTANT_RATE_LIMIT_WINDOW_SECONDS,
        rate_limit_max_requests=ASSISTANT_RATE_LIMIT_MAX_REQUESTS,
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


def _history_empty() -> dict:
    return {"version": 1, "sessions": {}}


def _session_history_path(session_key: str) -> Path:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    return ASSISTANT_HISTORY_DIR / f"{digest}.json"


def _read_legacy_history_unlocked() -> dict:
    if not ASSISTANT_HISTORY_FILE.exists():
        return _history_empty()
    try:
        data = json.loads(ASSISTANT_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[assistant-history] Failed to read legacy JSON history: {e}")
        return _history_empty()
    if not isinstance(data, dict):
        return _history_empty()
    data.setdefault("version", 1)
    data.setdefault("sessions", {})
    return data


def _session_from_history_payload(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    session = data.get("session") if isinstance(data.get("session"), dict) else data
    if not isinstance(session, dict):
        return None
    session.setdefault("messages", [])
    return session


def _read_session_history_unlocked(session_key: str) -> dict | None:
    path = _session_history_path(session_key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[assistant-history] Failed to read session JSON history: {e}")
            return None
        return _session_from_history_payload(data)

    legacy_session = _read_legacy_history_unlocked().get("sessions", {}).get(session_key)
    if isinstance(legacy_session, dict):
        legacy_session.setdefault("messages", [])
        return legacy_session
    return None


def _write_session_history_unlocked(session_key: str, session: dict) -> None:
    ASSISTANT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_history_path(session_key)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = {"version": 2, "session": session}
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


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
        return {"id": None, "email": None, "username": None}
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
    }


def _validation_error(message: str) -> None:
    raise HTTPException(status_code=422, detail=message)


def _validated_question(question: str) -> str:
    clean_question = (question or "").strip()
    if not clean_question:
        _validation_error("Вопрос не должен быть пустым.")
    if len(clean_question) > ASSISTANT_QUESTION_MAX_LENGTH:
        _validation_error(f"Вопрос слишком длинный. Максимум: {ASSISTANT_QUESTION_MAX_LENGTH} символов.")
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


def _history_session_payload(
    *,
    session_key: str,
    session_id: str,
    user: User | None,
    user_role: str | None,
    access_scope: str,
    created_at: str,
    updated_at: str,
    messages: list[dict] | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "scoped_session_id": session_key,
        "access_scope": access_scope,
        "user_role": user_role,
        "user": _user_history_payload(user),
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": messages or [],
    }


def _get_session_history(session_key: str, fallback: dict, limit: int | None = None) -> dict:
    with _history_lock:
        session = _read_session_history_unlocked(session_key)
    if not session:
        session = fallback
    messages = list(session.get("messages") or [])
    if limit and limit > 0:
        messages = messages[-limit:]
    return {**session, "messages": messages}


def _append_history_turn(
    *,
    session_key: str,
    session_id: str,
    user: User | None,
    user_role: str | None,
    access_scope: str,
    question: str,
    result: dict,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    turn_id = uuid.uuid4().hex
    with _history_lock:
        session = _read_session_history_unlocked(session_key)
        if not session:
            session = _history_session_payload(
                session_key=session_key,
                session_id=session_id,
                user=user,
                user_role=user_role,
                access_scope=access_scope,
                created_at=created_at,
                updated_at=created_at,
            )
        else:
            session.update(
                {
                    "session_id": session_id,
                    "scoped_session_id": session_key,
                    "access_scope": access_scope,
                    "user_role": user_role,
                    "user": _user_history_payload(user),
                    "updated_at": created_at,
                }
            )
        session.setdefault("messages", []).extend(
            [
                {
                    "id": f"{turn_id}:user",
                    "turn_id": turn_id,
                    "role": "user",
                    "content": question,
                    "created_at": created_at,
                },
                {
                    "id": f"{turn_id}:assistant",
                    "turn_id": turn_id,
                    "role": "assistant",
                    "content": result.get("answer", ""),
                    "created_at": created_at,
                    "metadata": {
                        "rewritten_question": result.get("rewritten_question", ""),
                        "sources": result.get("sources", []),
                        "access_scope": result.get("access_scope", access_scope),
                        "user_role": user_role,
                    },
                },
            ]
        )
        if ASSISTANT_HISTORY_MAX_MESSAGES > 0:
            session["messages"] = session["messages"][-ASSISTANT_HISTORY_MAX_MESSAGES:]
        _write_session_history_unlocked(session_key, session)


def _history_turns_for_rag(session_key: str) -> list[dict]:
    with _history_lock:
        session = _read_session_history_unlocked(session_key)
        messages = session.get("messages", []) if session else []
    turns: list[dict] = []
    pending_question: str | None = None
    for message in messages:
        role = message.get("role")
        if role == "user":
            pending_question = message.get("content") or ""
        elif role == "assistant" and pending_question:
            answer = message.get("content") or ""
            if answer:
                turns.append({"question": pending_question, "answer": answer})
            pending_question = None
    return turns[-getattr(cfg, "memory_turns", 5):]


def _hydrate_rag_memory(session_key: str, rag: Any) -> None:
    with _hydrated_sessions_lock:
        if session_key in _hydrated_sessions:
            return
    for turn in _history_turns_for_rag(session_key):
        rag.memory.save(turn["question"], turn["answer"])
    with _hydrated_sessions_lock:
        _hydrated_sessions.add(session_key)


def _clear_session_history(session_key: str, fallback: dict) -> int:
    updated_at = datetime.now(timezone.utc).isoformat()
    with _history_lock:
        session = _read_session_history_unlocked(session_key)
        deleted = len(session.get("messages", [])) if session else 0
        cleared = {
            **fallback,
            "created_at": session.get("created_at", updated_at) if session else updated_at,
            "updated_at": updated_at,
            "messages": [],
        }
        _write_session_history_unlocked(session_key, cleared)
    _discard_hydrated_session(session_key)
    return deleted


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
            f"S3 +{s3.get('added',0)}"
        )


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=AssistantStatusResponse)
def status():
    return get_assistant_status()


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    started_at = monotonic()
    successful = False
    try:
        cleanup_idle_sessions()
        question = _validated_question(body.question)
        role_name, access_scope, clean_session_id, session_key = _session_context(db, current_user, body.session_id)
        _check_assistant_rate_limit(request, current_user)
        with _get_session_lock(session_key):
            rag = _get_or_create_rag_locked(session_key)
            _hydrate_rag_memory(session_key, rag)
            try:
                result = rag.ask(question, access_scope=access_scope)
            except Exception:
                set_assistant_last_error("Ошибка при обработке вопроса. Подробности в логах backend.")
                logger.exception("[assistant] Failed to answer question")
                raise HTTPException(
                    status_code=500,
                    detail="Не удалось получить ответ ассистента. Попробуйте позже.",
                )
            _append_history_turn(
                session_key=session_key,
                session_id=clean_session_id,
                user=current_user,
                user_role=role_name,
                access_scope=access_scope,
                question=question,
                result=result,
            )
        response = AskResponse(
            answer=result["answer"],
            rewritten_question=result["rewritten_question"],
            sources=result["sources"],
            access_scope=result["access_scope"],
            user_role=role_name,
        )
        successful = True
        return response
    finally:
        _record_assistant_request(monotonic() - started_at, successful)


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
            session_key,
            fallback={
                "session_id": clean_session_id,
                "scoped_session_id": session_key,
                "access_scope": access_scope,
                "user_role": role_name,
                "user": _user_history_payload(current_user),
            },
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
