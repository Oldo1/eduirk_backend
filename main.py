import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from difflib import SequenceMatcher

load_dotenv()

from database import engine, Base, get_db, SessionLocal
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from schemas import UserCreate, UserResponse, Token
from models import User
from api import tpmpk_router
from routers.certificates import router as certificates_router
from routers.users import router as users_router
from utils.schema_patch import ensure_certificate_layout_columns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RAG_ENABLED = os.getenv("ENABLE_RAG", "false").lower() in {"1", "true", "yes", "on"}
_scheduler = None
_bg_task_status: dict = {
    "running": False,
    "mode": None,
    "started_at": None,
    "result": None,
    "error": None,
}

SITE_SEARCH_INDEX = [
    {"title": "Главная", "url": "/", "description": "Новости, мероприятия и основные разделы сайта."},
    {"title": "ТПМПК", "url": "/tpmpk/", "description": "Раздел территориальной психолого-медико-педагогической комиссии."},
    {"title": "Запись на обследование ПМПК", "url": "/tpmpk/zapis", "description": "Онлайн-заявка на обследование ребенка."},
    {"title": "Документы ТПМПК", "url": "/tpmpk/dokumenty/", "description": "Перечень документов для прохождения комиссии."},
    {"title": "Бланки и формы ТПМПК", "url": "/tpmpk/blanki/", "description": "Заявления, согласия и формы для родителей."},
    {"title": "График работы комиссии", "url": "/tpmpk/grafik/", "description": "Расписание приема и режим работы ТПМПК."},
    {"title": "Состав комиссии", "url": "/tpmpk/sostav/", "description": "Специалисты и направления работы комиссии."},
    {"title": "Нормативные акты", "url": "/tpmpk/npa/", "description": "Правовая база и положения ТПМПК."},
    {"title": "Часто задаваемые вопросы", "url": "/tpmpk/faq/", "description": "Ответы на частые вопросы о прохождении комиссии."},
    {"title": "Для родителей", "url": "/tpmpk/dlya-roditeley/", "description": "Памятки и рекомендации для семей."},
    {"title": "Для педагогов", "url": "/tpmpk/dlya-pedagogov/", "description": "Материалы для образовательных организаций."},
    {"title": "Контакты ТПМПК", "url": "/tpmpk/kontakty/", "description": "Телефон, адрес и порядок обращения."},
    {"title": "Сведения об образовательной организации", "url": "/", "description": "Основная информация об учреждении."},
    {"title": "Дом учителя", "url": "/", "description": "Городские образовательные мероприятия и методическая поддержка."},
    {"title": "Методическое пространство", "url": "/", "description": "Материалы, проекты и события для педагогов."},
]

legacy_redirect_map = {
    "/pmpk/": "/tpmpk/",
    "/pmk/": "/tpmpk/",
    "/tpmpk/docs/": "/tpmpk/dokumenty/",
    "/tpmpk/documents/": "/tpmpk/dokumenty/",
    "/tpmpk/forms/": "/tpmpk/blanki/",
    "/tpmpk/schedule/": "/tpmpk/grafik/",
    "/tpmpk/contacts/": "/tpmpk/kontakty/",
    "/tpmpk/parents/": "/tpmpk/dlya-roditeley/",
    "/tpmpk/teachers/": "/tpmpk/dlya-pedagogov/",
}


def _normalize_search_text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").strip("/").split())


def _score_page(query: str, page: dict) -> float:
    haystack = _normalize_search_text(
        f"{page['title']} {page['url']} {page.get('description', '')}"
    )
    needle = _normalize_search_text(query)
    if not needle:
        return 0
    if needle in haystack:
        return 1.0
    return SequenceMatcher(None, needle, haystack).ratio()


def _pg_trgm_suggestions(query: str, db: Session | None = None, limit: int = 3) -> list[dict]:
    if db is None or engine.dialect.name != "postgresql":
        return []

    titles = [page["title"] for page in SITE_SEARCH_INDEX]
    urls = [page["url"] for page in SITE_SEARCH_INDEX]
    descriptions = [page["description"] for page in SITE_SEARCH_INDEX]
    try:
        rows = db.execute(
            text(
                """
                select title, url, description,
                       greatest(similarity(title, :query), similarity(url, :query), similarity(description, :query)) as score
                from unnest(:titles, :urls, :descriptions) as pages(title, url, description)
                order by score desc
                limit :limit
                """
            ),
            {"query": query, "titles": titles, "urls": urls, "descriptions": descriptions, "limit": limit},
        ).mappings().all()
        return [
            {"title": row["title"], "url": row["url"], "description": row["description"]}
            for row in rows
            if row["score"] and row["score"] > 0.05
        ]
    except Exception:
        db.rollback()
        return []


def smart_404_suggestions(request_url: str, db: Session | None = None, limit: int = 3) -> list[dict]:
    path = str(request_url or "/").split("?", 1)[0]
    if path in legacy_redirect_map:
        target = legacy_redirect_map[path]
        return [
            page for page in SITE_SEARCH_INDEX if page["url"] == target
        ][:limit]

    trgm = _pg_trgm_suggestions(path, db=db, limit=limit)
    if trgm:
        return trgm[:limit]

    ranked = sorted(
        SITE_SEARCH_INDEX,
        key=lambda page: _score_page(path, page),
        reverse=True,
    )
    return ranked[:limit]

if RAG_ENABLED:
    from routers.assistant import (
        router as assistant_router,
        init_rag,
        get_vectorstore,
        reload_all_sessions,
        EMBEDDINGS,
    )
    from updater import RAGScheduler, UPDATE_INTERVAL_HOURS
else:
    assistant_router = APIRouter(prefix="/assistant", tags=["assistant"])

    def init_rag():
        return None

    def get_vectorstore():
        raise RuntimeError("RAG assistant is disabled")

    def reload_all_sessions(stats: dict | None = None):
        return None

    EMBEDDINGS = None
    RAGScheduler = None
    UPDATE_INTERVAL_HOURS = None

    @assistant_router.post("/ask")
    def ask_disabled():
        raise HTTPException(
            status_code=503,
            detail="RAG assistant is disabled. Set ENABLE_RAG=true and install RAG dependencies.",
        )

    @assistant_router.post("/clear/{session_id}")
    def clear_disabled(session_id: str):
        return {"status": "disabled", "session_id": session_id}


def _run_incremental_bg():
    from updater import incremental_update
    from update_state import UpdateState

    _bg_task_status.update({"running": True, "result": None, "error": None})
    try:
        state = UpdateState()
        stats = incremental_update(
            vectorstore=get_vectorstore(),
            embeddings=EMBEDDINGS,
            state=state,
            on_update_done=reload_all_sessions,
        )
        _bg_task_status["result"] = {"mode": "incremental", "stats": stats}
        logger.info("[update] Incremental update completed")
    except Exception as e:
        _bg_task_status["error"] = str(e)
        logger.error("[update] Incremental update failed: %s", e, exc_info=True)
    finally:
        _bg_task_status["running"] = False


def _run_reindex_bg():
    from updater import incremental_update
    from update_state import UpdateState
    import routers.assistant as assistant_module
    from routers.assistant import cfg, EMBEDDINGS as assistant_embeddings, reload_all_sessions as reload_sessions

    _bg_task_status.update({"running": True, "result": None, "error": None})
    try:
        logger.info("[reindex] Starting full reindex for collection %s", cfg.collection_name)
        vectorstore = assistant_module.get_vectorstore()

        try:
            existing_ids = vectorstore._collection.get(include=[])["ids"]
            if existing_ids:
                vectorstore._collection.delete(ids=existing_ids)
        except Exception as e:
            logger.warning("[reindex] Could not clear collection: %s", e)

        try:
            os.remove("update_state.json")
        except FileNotFoundError:
            pass

        state = UpdateState()
        stats = incremental_update(
            vectorstore=vectorstore,
            embeddings=assistant_embeddings,
            state=state,
            on_update_done=reload_sessions,
        )
        total = vectorstore._collection.count()
        _bg_task_status["result"] = {"mode": "full_reindex", "vectors": total, "stats": stats}
    except Exception as e:
        _bg_task_status["error"] = str(e)
        logger.error("[reindex] Full reindex failed: %s", e, exc_info=True)
    finally:
        _bg_task_status["running"] = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_rag()
    if RAG_ENABLED and RAGScheduler is not None:
        _scheduler = RAGScheduler(
            vectorstore=get_vectorstore(),
            embeddings=EMBEDDINGS,
            interval_hours=UPDATE_INTERVAL_HOURS,
            on_update_done=reload_all_sessions,
            run_on_start=False,
        )
        _scheduler.start()
        logger.info("[main] RAG update scheduler started")
    yield
    if _scheduler:
        _scheduler.stop()


app = FastAPI(lifespan=lifespan, title="ИМЦРО API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):517[0-9]$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

Base.metadata.create_all(bind=engine)
ensure_certificate_layout_columns(engine)

app.include_router(assistant_router)
app.include_router(certificates_router)
app.include_router(users_router)
app.include_router(tpmpk_router)


@app.get("/api/search/")
def site_search(q: str = Query("", max_length=120), db: Session = Depends(get_db)):
    query = q.strip()
    if not query:
        return {"query": query, "results": SITE_SEARCH_INDEX[:6]}

    trgm = _pg_trgm_suggestions(query, db=db, limit=6)
    if trgm:
        return {"query": query, "results": trgm}

    ranked = sorted(
        SITE_SEARCH_INDEX,
        key=lambda page: _score_page(query, page),
        reverse=True,
    )
    return {"query": query, "results": ranked[:6]}


@app.exception_handler(StarletteHTTPException)
async def smart_404_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code != 404:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    db = SessionLocal()
    try:
        suggestions = smart_404_suggestions(str(request.url.path), db=db)
    finally:
        db.close()

    return JSONResponse(
        status_code=404,
        content={
            "detail": exc.detail or "Not Found",
            "message": "Страница не найдена",
            "suggestions": suggestions,
        },
    )


@app.get("/admin/update/status")
def update_status():
    scheduler_info = (
        _scheduler.status()
        if _scheduler
        else {
            "running": False,
            "enabled": RAG_ENABLED,
            "interval_hours": UPDATE_INTERVAL_HOURS,
            "message": "RAG scheduler is not running",
        }
    )
    return {"scheduler": scheduler_info, "background": _bg_task_status}


@app.post("/admin/update/run")
def update_run_now(background_tasks: BackgroundTasks):
    if not RAG_ENABLED:
        raise HTTPException(status_code=503, detail="RAG assistant is disabled. Set ENABLE_RAG=true.")
    if _bg_task_status["running"]:
        raise HTTPException(status_code=409, detail=f"Task already running: {_bg_task_status['mode']}")

    _bg_task_status.update({
        "mode": "incremental",
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    })
    background_tasks.add_task(_run_incremental_bg)
    return {"status": "started", "mode": "incremental"}


@app.post("/admin/reindex")
def full_reindex(background_tasks: BackgroundTasks):
    if not RAG_ENABLED:
        raise HTTPException(status_code=503, detail="RAG assistant is disabled. Set ENABLE_RAG=true.")
    if _bg_task_status["running"]:
        raise HTTPException(status_code=409, detail=f"Task already running: {_bg_task_status['mode']}")

    _bg_task_status.update({
        "mode": "reindex",
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    })
    background_tasks.add_task(_run_reindex_bg)
    return {"status": "started", "mode": "full_reindex"}


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=access_token)


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
