from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
import asyncio
import contextlib
import logging
import os
from typing import Any

from database import engine, Base, SessionLocal, format_database_connection_error, get_db
from dev_seed import ensure_dev_test_users
from assistant_settings import (
    get_assistant_settings,
    load_assistant_settings,
    register_settings_listener,
)
from models import User, UserRole
from permissions import user_permissions
from rate_limit import RateLimitMiddleware
from auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    get_current_user, get_user_from_refresh_token,
    ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
)
from schemas import RefreshTokenRequest, UserCreate, UserResponse, Token
from api import tpmpk_router
from dom_uchitelya import router as dom_uchitelya_router

from routers.assistant import (
    router as assistant_router,
    init_rag,
    warmup_embeddings,
    get_vectorstore,
    reload_all_sessions,
    mark_assistant_warmup_started,
    mark_assistant_warmup_completed,
    set_assistant_last_error,
    assistant_startup_error_message,
    EMBEDDINGS,
)
from routers.certificates import router as certificates_router
from routers.users import router as users_router
from routers.appointments import router as appointments_router
from routers.articles import router as articles_router
from utils.schema_patch import (
    ensure_certificate_layout_columns,
    ensure_article_editor_columns,
    ensure_postgresql_extensions,
    ensure_tpmpk_bot_question_columns,
    ensure_tpmpk_duplicate_guard,
    ensure_tpmpk_slot_minutes_range,
    ensure_user_name_columns,
    ensure_user_registration_date_column,
    ensure_user_role_permission_columns,
    ensure_appointment_user_columns,
    remove_username_columns,
)
from utils.local_docs import local_openapi_docs_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

SITE_SEARCH_INDEX = [
    {"title": "Главная", "url": "/", "description": "Новости, мероприятия и основные разделы сайта."},
    {"title": "ТПМПК", "url": "/tpmpk/", "description": "Раздел территориальной психолого-медико-педагогической комиссии."},
    {"title": "Запись на обследование ПМПК", "url": "/tpmpk/zapis", "description": "Онлайн-заявка на обследование ребенка."},
    {"title": "Документы ТПМПК", "url": "/tpmpk/dokumenty/", "description": "Перечень документов для прохождения комиссии."},
    {"title": "Бланки и формы ТПМПК", "url": "/tpmpk/blanki/", "description": "Заявления, согласия и формы для родителей."},
    {"title": "График работы комиссии", "url": "/tpmpk/grafik/", "description": "Расписание приема и режим работы комиссии."},
    {"title": "Состав комиссии", "url": "/tpmpk/sostav/", "description": "Специалисты и направления работы комиссии."},
    {"title": "Нормативные акты", "url": "/tpmpk/npa/", "description": "Правовая база и положения ТПМПК."},
    {"title": "Часто задаваемые вопросы", "url": "/tpmpk/faq/", "description": "Ответы на частые вопросы о прохождении комиссии."},
    {"title": "Для родителей", "url": "/tpmpk/dlya-roditeley/", "description": "Памятки и рекомендации для семей."},
    {"title": "Для педагогов", "url": "/tpmpk/dlya-pedagogov/", "description": "Материалы для образовательных организаций."},
    {"title": "Контакты ТПМПК", "url": "/tpmpk/kontakty/", "description": "Телефон, адрес и порядок обращения."},
    {"title": "Сведения об образовательной организации", "url": "/", "description": "Основная информация об учреждении."},
    {"title": "Дом учителя", "url": "/dom-uchitelya/", "description": "Городские образовательные мероприятия и методическая поддержка."},
    {"title": "Новости Дома учителя", "url": "/dom-uchitelya/novosti/", "description": "Собственная лента новостей Дома учителя."},
    {"title": "Программа Дома учителя", "url": "/dom-uchitelya/programma/", "description": "Программа мероприятий Дома учителя."},
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
        return [page for page in SITE_SEARCH_INDEX if page["url"] == target][:limit]

    trgm = _pg_trgm_suggestions(path, db=db, limit=limit)
    if trgm:
        return trgm[:limit]

    ranked = sorted(
        SITE_SEARCH_INDEX,
        key=lambda page: _score_page(path, page),
        reverse=True,
    )
    return ranked[:limit]


def _cors_origins_from_env() -> list[str]:
    raw_origins = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()]

# ── Планировщик (глобальный, чтобы была ссылка) ───────────────────────────────
_scheduler: Any | None = None
_rag_startup_task: asyncio.Task | None = None


def _apply_scheduler_settings(_previous, current) -> None:
    if _scheduler is not None:
        _scheduler.set_interval_hours(current.update_interval_hours)


register_settings_listener(_apply_scheduler_settings)


def _prepare_rag_startup() -> tuple[type, Any]:
    from updater import RAGScheduler

    init_rag()
    warmup_embeddings()
    return RAGScheduler, get_vectorstore()


async def _init_rag_and_scheduler_bg() -> None:
    global _scheduler

    mark_assistant_warmup_started()
    try:
        logger.info("[main] Initializing assistant RAG in background")
        scheduler_cls, vectorstore = await asyncio.to_thread(_prepare_rag_startup)

        update_interval_hours = get_assistant_settings().update_interval_hours
        _scheduler = scheduler_cls(
            vectorstore=vectorstore,
            embeddings=EMBEDDINGS,
            interval_hours=update_interval_hours,
            on_update_done=reload_all_sessions,
            run_on_start=False,
        )
        _scheduler.start()
        mark_assistant_warmup_completed()
        logger.info(f"[main] Scheduler started (every {update_interval_hours} h.)")
    except asyncio.CancelledError:
        logger.info("[main] Assistant RAG startup task cancelled")
        raise
    except Exception as e:
        set_assistant_last_error(assistant_startup_error_message(e))
        logger.error(f"[main] Assistant RAG startup failed: {e}", exc_info=True)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler, _rag_startup_task

    # Start assistant warmup without blocking the API.
    _rag_startup_task = asyncio.create_task(_init_rag_and_scheduler_bg())

    yield

    # Остановка
    if _rag_startup_task and not _rag_startup_task.done():
        _rag_startup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _rag_startup_task
    if _scheduler:
        _scheduler.stop()
    logger.info("[main] Сервер остановлен")


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan, title="ИМЦРО API", docs_url=None)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins_from_env(),
    allow_origin_regex=os.getenv("CORS_ALLOW_ORIGIN_REGEX", r"https?://(localhost|127\.0\.0\.1)(:\d+)?"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/docs", include_in_schema=False)
def local_docs():
    return HTMLResponse(local_openapi_docs_html())


@app.get("/health", include_in_schema=False)
def health_check():
    return {"status": "ok"}


def initialize_database() -> None:
    try:
        ensure_postgresql_extensions(engine)
        Base.metadata.create_all(bind=engine)
        remove_username_columns(engine)
        ensure_user_name_columns(engine)
        ensure_user_registration_date_column(engine)
        ensure_certificate_layout_columns(engine)
        ensure_article_editor_columns(engine)
        ensure_tpmpk_bot_question_columns(engine)
        ensure_tpmpk_slot_minutes_range(engine)
        ensure_tpmpk_duplicate_guard(engine)
        ensure_user_role_permission_columns(engine)
        ensure_appointment_user_columns(engine)
        with SessionLocal() as db:
            load_assistant_settings(db)
            ensure_dev_test_users(db)
    except (UnicodeDecodeError, SQLAlchemyError) as exc:
        raise RuntimeError(format_database_connection_error(exc)) from None


initialize_database()

app.include_router(assistant_router)
app.include_router(certificates_router)
app.include_router(users_router)
app.include_router(appointments_router)
app.include_router(articles_router)
app.include_router(tpmpk_router)
app.include_router(dom_uchitelya_router)


# ── Состояние фоновых задач ───────────────────────────────────────────────────

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


_bg_task_status: dict = {
    "running":    False,
    "mode":       None,       # "incremental" | "incremental_site" | "incremental_docs" | "reindex"
    "started_at": None,
    "progress":   None,       # {"stage", "current", "total", "detail"}
    "result":     None,
    "error":      None,
}


def _make_progress_cb():
    """Возвращает callback, пишущий прогресс в _bg_task_status['progress']."""
    def cb(stage: str, current: int, total: int, detail: str = ""):
        _bg_task_status["progress"] = {
            "stage":   stage,
            "current": current,
            "total":   total,
            "detail":  detail,
        }
    return cb


def _run_incremental_bg(sources: list[str] | None = None):
    """Фоновая функция инкрементального обновления."""
    from updater import incremental_update
    from update_state import UpdateState

    _bg_task_status.update({"running": True, "result": None, "error": None, "progress": None})
    try:
        state = UpdateState()
        stats = incremental_update(
            vectorstore=get_vectorstore(),
            embeddings=EMBEDDINGS,
            state=state,
            on_update_done=reload_all_sessions,
            sources=sources,
            progress_cb=_make_progress_cb(),
        )
        _bg_task_status["result"] = {"mode": "incremental", "stats": stats}
        logger.info("[update] Фоновое обновление завершено")
    except Exception as e:
        _bg_task_status["error"] = str(e)
        logger.error(f"[update] Фоновое обновление упало: {e}", exc_info=True)
    finally:
        _bg_task_status["running"]  = False
        _bg_task_status["progress"] = None


def _run_reindex_bg():
    """Фоновая функция полной переиндексации."""
    from updater import incremental_update
    from update_state import UpdateState
    import routers.assistant as _assistant_module
    from routers.assistant import cfg as _cfg, EMBEDDINGS as _EMBEDDINGS, reload_all_sessions as _reload

    _bg_task_status.update({"running": True, "result": None, "error": None, "progress": None})
    progress_cb = _make_progress_cb()
    try:
        logger.info(f"[reindex] ══ Начинаю полную переиндексацию (коллекция: {_cfg.collection_name}) ══")
        progress_cb("reindex_clear", 0, 0, "Очищаю коллекцию…")

        # Получаем существующий vectorstore
        vs = _assistant_module.get_vectorstore()

        # Удаляем все документы из коллекции (не трогаем саму коллекцию)
        # Это безопаснее чем delete_collection — не рвёт внутренние ссылки
        try:
            existing_ids = vs._collection.get(include=[])["ids"]
            if existing_ids:
                vs._collection.delete(ids=existing_ids)
                logger.info(f"[reindex] Удалено {len(existing_ids)} документов")
            else:
                logger.info("[reindex] Коллекция уже пустая")
            logger.info(f"[reindex] Векторов после очистки: {vs._collection.count()}")
        except Exception as e:
            logger.warning(f"[reindex] Ошибка очистки коллекции: {e}")

        # Сбрасываем state — удаляем файл чтобы всё считалось новым
        import os as _os
        try:
            _os.remove("update_state.json")
            logger.info("[reindex] update_state.json удалён")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"[reindex] Не удалось удалить update_state.json: {e}")
        state = UpdateState()   # создаём пустой (файла нет — загружает пустой)
        state.clear()           # гарантируем полный reindex даже если файл состояния остался

        # Полная индексация
        stats = incremental_update(
            vectorstore=vs,
            embeddings=_EMBEDDINGS,
            state=state,
            on_update_done=_reload,
            use_site_cache=True,
            progress_cb=progress_cb,
        )

        total = vs._collection.count()
        logger.info(f"[reindex] ══ Готово. Векторов в базе: {total} ══")
        _bg_task_status["result"] = {
            "mode": "full_reindex", "vectors": total, "stats": stats
        }
    except Exception as e:
        _bg_task_status["error"] = str(e)
        logger.error(f"[reindex] Ошибка: {e}", exc_info=True)
    finally:
        _bg_task_status["running"]  = False
        _bg_task_status["progress"] = None


# ── Служебные эндпоинты обновления ───────────────────────────────────────────

@app.get("/admin/update/status")
def update_status():
    """Статус планировщика + текущей фоновой задачи."""
    scheduler_info = _scheduler.status() if _scheduler else {"error": "Планировщик не запущен"}
    return {
        "scheduler":   scheduler_info,
        "background":  _bg_task_status,
    }


def _start_incremental(
    background_tasks: BackgroundTasks,
    sources:          list[str],
    mode_label:       str,
):
    if _bg_task_status["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"Уже выполняется задача: {_bg_task_status['mode']}. Дождитесь завершения."
        )

    _bg_task_status.update({
        "mode":       mode_label,
        "started_at": datetime.now().isoformat(),
        "result":     None,
        "error":      None,
    })
    background_tasks.add_task(_run_incremental_bg, sources)
    return {
        "status":  "started",
        "mode":    mode_label,
        "sources": sources,
        "message": "Обновление запущено в фоне. Статус: GET /admin/update/status",
    }


@app.post("/admin/update/run")
def update_run_now(background_tasks: BackgroundTasks):
    """Инкрементальное обновление обоих источников (сайт + S3)."""
    return _start_incremental(background_tasks, ["site", "s3"], "incremental")


@app.post("/admin/update/site")
def update_site_only(background_tasks: BackgroundTasks):
    """Инкрементальное обновление только страниц сайта."""
    return _start_incremental(background_tasks, ["site"], "incremental_site")


@app.post("/admin/update/docs")
def update_docs_only(background_tasks: BackgroundTasks):
    """Инкрементальное обновление только документов из Yandex S3."""
    return _start_incremental(background_tasks, ["s3"], "incremental_docs")


@app.post("/admin/reindex")
def full_reindex(background_tasks: BackgroundTasks):
    """
    Полная переиндексация в фоне — очищает индекс и строит заново.
    Возвращает ответ сразу, переиндексация идёт в фоне (несколько минут).
    Статус: GET /admin/update/status
    """
    if _bg_task_status["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"Уже выполняется задача: {_bg_task_status['mode']}. Дождитесь завершения."
        )

    _bg_task_status.update({
        "mode":       "reindex",
        "started_at": datetime.now().isoformat(),
        "result":     None,
        "error":      None,
    })
    background_tasks.add_task(_run_reindex_bg)
    return {
        "status":  "started",
        "mode":    "full_reindex",
        "message": "Переиндексация запущена в фоне. Статус: GET /admin/update/status",
    }




# ── Аутентификация ────────────────────────────────────────────────────────────

def _user_role(db: Session, user: User) -> UserRole | None:
    if user.role_id is None:
        return None
    return db.query(UserRole).filter(UserRole.id == user.role_id).first()


def _user_role_name(db: Session, user: User) -> str | None:
    role = _user_role(db, user)
    return role.role_name if role else None


def _user_response(db: Session, user: User) -> UserResponse:
    role = _user_role(db, user)
    return UserResponse(
        id=user.id,
        email=user.email,
        last_name=getattr(user, "last_name", None),
        first_name=getattr(user, "first_name", None),
        middle_name=getattr(user, "middle_name", None),
        created_at=getattr(user, "created_at", None),
        is_active=user.is_active,
        role=role.role_name if role else None,
        can_access_internal_docs=bool(
            getattr(role, "can_access_internal_docs", False)
        ),
        permissions=user_permissions(user),
        allowed_methodika_subjects=getattr(user, "allowed_methodika_subjects", None) or [],
    )


def _token_response(db: Session, user: User) -> Token:
    role_name = _user_role_name(db, user)
    return Token(
        access_token=create_access_token(
            data={"sub": user.email, "role": role_name},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        ),
        refresh_token=create_refresh_token(
            data={"sub": user.email},
            expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        ),
        role=role_name,
        user=_user_response(db, user),
    )


def _password_matches_or_upgrade(db: Session, user: User, plain_password: str) -> bool:
    password_hash = getattr(user, "password_hash", None)
    if verify_password(plain_password, password_hash):
        return True

    if isinstance(password_hash, str) and password_hash == plain_password:
        user.password_hash = hash_password(plain_password)
        db.commit()
        db.refresh(user)
        logger.warning("Upgraded legacy plaintext password hash for user id=%s", user.id)
        return True

    return False


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    user = User(
        email=user_data.email,
        last_name=user_data.last_name,
        first_name=user_data.first_name,
        middle_name=user_data.middle_name,
        password_hash=hash_password(user_data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_response(db, user)


@app.post("/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not _password_matches_or_upgrade(db, user, form_data.password):
        raise HTTPException(
            status_code=401,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(db, user)


@app.post("/auth/refresh", response_model=Token)
def refresh_auth_token(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    user = get_user_from_refresh_token(payload.refresh_token, db)
    return _token_response(db, user)


@app.get("/auth/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _user_response(db, current_user)


logger.info("Сервер запущен успешно")
logger.info(f"  • Автообновление RAG:         каждые {get_assistant_settings().update_interval_hours} ч.")
logger.info("  • Инкрементальное обновление: POST /admin/update/run   (сайт + S3)")
logger.info("  • Только сайт:                POST /admin/update/site")
logger.info("  • Только документы:           POST /admin/update/docs")
logger.info("  • Полная переиндексация:      POST /admin/reindex")
logger.info("  • Статус:                     GET  /admin/update/status")
