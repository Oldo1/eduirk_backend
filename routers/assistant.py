from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from sqlalchemy.orm import Session
from auth import get_optional_current_user
from database import get_db
from models import User, UserRole
from rag_pipeline import RAGConfig, RAGSystem
from assistant_access import access_scope_for_role, scoped_session_id
import logging

logger = logging.getLogger("assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])

# ── Schemas ───────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question:   str
    session_id: str = "default"

class AskResponse(BaseModel):
    answer:             str
    rewritten_question: str
    sources:            list[dict]
    access_scope:       str
    user_role:          str | None = None

# ── Конфигурация ──────────────────────────────────────────────────────────────

cfg = RAGConfig(
    credentials="MDE5YTRkNDctODNiZC03ODFhLTg4MmUtNzM5MGY2ZDVjNTY0OmI2NjFhZGEzLTgwNmQtNDg2MC1iMGVjLWRhMTg2MWFmYTYxOA==",
    scope="GIGACHAT_API_PERS",
    model="GigaChat",
    persist_dir="./chroma_gigachat",
    collection_name="eduirk",
    top_k=5,
    fetch_k=30,
    memory_turns=5,
)

EMBEDDINGS = HuggingFaceEmbeddings(
    model_name="intfloat/multilingual-e5-large",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

# ── Глобальные объекты ────────────────────────────────────────────────────────
# Один vectorstore на весь процесс — все сессии и updater используют его

_vectorstore: Chroma | None       = None
_sessions:    dict[str, RAGSystem] = {}


def get_vectorstore() -> Chroma:
    """Возвращает единый Chroma-объект, создаёт при первом вызове."""
    global _vectorstore
    if _vectorstore is None:
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
    get_rag("__warmup__")
    logger.info(f"[assistant] RAG готов. Векторов в базе: {vs._collection.count()}")


def _make_rag(session_id: str) -> RAGSystem:
    """Создаёт новую RAG-сессию, привязанную к общему vectorstore."""
    rag = RAGSystem(cfg)
    rag.set_vectorstore(get_vectorstore())
    _sessions[session_id] = rag
    return rag


def get_rag(session_id: str) -> RAGSystem:
    if session_id not in _sessions:
        return _make_rag(session_id)
    return _sessions[session_id]


def _user_role_name(db: Session, user: User | None) -> str | None:
    if user is None or user.role_id is None:
        return None
    role = db.query(UserRole).filter(UserRole.id == user.role_id).first()
    return role.role_name if role else None


def reload_all_sessions(stats: dict | None = None) -> None:
    """
    Вызывается планировщиком после обновления индекса.
    Перепривязывает все сессии к обновлённому vectorstore,
    сохраняя историю диалогов.
    """
    vs    = get_vectorstore()
    count = vs._collection.count()
    logger.info(
        f"[assistant] Перезагружаю {len(_sessions)} сессий "
        f"(векторов: {count})"
    )
    for session_id, rag in _sessions.items():
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

@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    role_name = _user_role_name(db, current_user)
    access_scope = access_scope_for_role(role_name)
    session_key = scoped_session_id(
        body.session_id,
        access_scope,
        current_user.id if current_user else None,
    )
    rag = get_rag(session_key)
    try:
        result = rag.ask(body.question, access_scope=access_scope)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return AskResponse(
        answer=result["answer"],
        rewritten_question=result["rewritten_question"],
        sources=result["sources"],
        access_scope=result["access_scope"],
        user_role=role_name,
    )


@router.post("/clear/{session_id}")
def clear_history(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    role_name = _user_role_name(db, current_user)
    access_scope = access_scope_for_role(role_name)
    session_key = scoped_session_id(
        session_id,
        access_scope,
        current_user.id if current_user else None,
    )
    if session_key in _sessions:
        _sessions[session_key].clear_memory()
    return {"status": "ok"}
