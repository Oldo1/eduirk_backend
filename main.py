from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import logging

from database import engine, Base, get_db
from models import User, UserRole
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from schemas import UserCreate, UserResponse, Token

from routers.assistant import (
    router as assistant_router,
    init_rag,
    get_vectorstore,
    reload_all_sessions,
    EMBEDDINGS,
)
from routers.certificates import router as certificates_router
from routers.users import router as users_router
from routers.appointments import router as appointments_router
from utils.schema_patch import ensure_certificate_layout_columns

from updater import RAGScheduler, UPDATE_INTERVAL_HOURS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ── Планировщик (глобальный, чтобы была ссылка) ───────────────────────────────
_scheduler: RAGScheduler | None = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    # 1. Инициализируем RAG
    init_rag()

    # 2. Запускаем планировщик обновлений
    _scheduler = RAGScheduler(
        vectorstore=get_vectorstore(),
        embeddings=EMBEDDINGS,
        interval_hours=UPDATE_INTERVAL_HOURS,   # менять в updater.py
        on_update_done=reload_all_sessions,      # callback после обновления
        run_on_start=False,                      # True = сразу краулить при старте
    )
    _scheduler.start()
    logger.info(f"[main] Планировщик запущен (каждые {UPDATE_INTERVAL_HOURS} ч.)")

    yield

    # Остановка
    if _scheduler:
        _scheduler.stop()
    logger.info("[main] Сервер остановлен")


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan, title="ИМЦРО API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
app.include_router(appointments_router)


# ── Состояние фоновых задач ───────────────────────────────────────────────────

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
        state = UpdateState()   # создаём пустой (файла нет — загружает пустой)

        # Полная индексация
        stats = incremental_update(
            vectorstore=vs,
            embeddings=_EMBEDDINGS,
            state=state,
            on_update_done=_reload,
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

def _user_role_name(db: Session, user: User) -> str | None:
    if user.role_id is None:
        return None
    role = db.query(UserRole).filter(UserRole.id == user.role_id).first()
    return role.role_name if role else None


def _user_response(db: Session, user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        is_active=user.is_active,
        role=_user_role_name(db, user),
    )


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Логин уже занят")
    user = User(
        email=user_data.email,
        username=user_data.username,
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
    identifier = form_data.username
    user = (
        db.query(User)
        .filter((User.email == identifier) | (User.username == identifier))
        .first()
    )
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role_name = _user_role_name(db, user)
    access_token = create_access_token(
        data={"sub": user.email, "role": role_name},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(
        access_token=access_token,
        role=role_name,
        user=_user_response(db, user),
    )


@app.get("/auth/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _user_response(db, current_user)


logger.info("Сервер запущен успешно")
logger.info(f"  • Автообновление RAG:         каждые {UPDATE_INTERVAL_HOURS} ч.")
logger.info("  • Инкрементальное обновление: POST /admin/update/run   (сайт + S3)")
logger.info("  • Только сайт:                POST /admin/update/site")
logger.info("  • Только документы:           POST /admin/update/docs")
logger.info("  • Полная переиндексация:      POST /admin/reindex")
logger.info("  • Статус:                     GET  /admin/update/status")