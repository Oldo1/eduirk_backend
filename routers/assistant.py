from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from langchain_huggingface import HuggingFaceEmbeddings
from rag_pipeline import RAGConfig, RAGSystem

router = APIRouter(prefix="/assistant", tags=["assistant"])

# ── Schemas ──────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    session_id: str = "default"  # для разных пользователей — разные сессии

class AskResponse(BaseModel):
    answer: str
    rewritten_question: str
    sources: list[dict]

# ── Инициализация RAG (один раз при старте) ───────────────────────────────────

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

# Хранилище сессий (в памяти — для прода лучше Redis)
_sessions: dict[str, RAGSystem] = {}

_rag_instance: RAGSystem | None = None

def init_rag():
    global _rag_instance, EMBEDDINGS
    rag = RAGSystem(cfg)
    rag.load_index(embeddings=EMBEDDINGS)
    _sessions["default"] = rag
    _rag_instance = rag

def get_rag(session_id: str) -> RAGSystem:
    if session_id not in _sessions:
        rag = RAGSystem(cfg)
        rag.load_index(embeddings=EMBEDDINGS)
        _sessions[session_id] = rag
    return _sessions[session_id]

def get_rag(session_id: str) -> RAGSystem:
    if session_id not in _sessions:
        rag = RAGSystem(cfg)
        rag.load_index(embeddings=EMBEDDINGS)
        _sessions[session_id] = rag
    return _sessions[session_id]

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest):
    rag = get_rag(body.session_id)
    try:
        result = rag.ask(body.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return AskResponse(
        answer=result["answer"],
        rewritten_question=result["rewritten_question"],
        sources=result["sources"],
    )

@router.post("/clear/{session_id}")
def clear_history(session_id: str):
    if session_id in _sessions:
        _sessions[session_id].clear_memory()
    return {"status": "ok"}