import logging
from datetime import timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from difflib import SequenceMatcher

load_dotenv()

from database import engine, Base, get_db, SessionLocal
from dev_seed import ensure_dev_test_users
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from schemas import UserCreate, UserResponse, Token
from models import User, UserRole
from api import tpmpk_router
from dom_uchitelya import router as dom_uchitelya_router
from routers.certificates import router as certificates_router
from routers.users import router as users_router
from utils.schema_patch import (
    ensure_certificate_layout_columns,
    ensure_postgresql_extensions,
    ensure_tpmpk_bot_question_columns,
    ensure_tpmpk_duplicate_guard,
    ensure_tpmpk_slot_minutes_range,
)
from utils.local_docs import local_openapi_docs_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

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


app = FastAPI(title="ИМЦРО API", docs_url=None)

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

@app.get("/docs", include_in_schema=False)
def local_docs():
    return HTMLResponse(local_openapi_docs_html())

ensure_postgresql_extensions(engine)
Base.metadata.create_all(bind=engine)
ensure_certificate_layout_columns(engine)
ensure_tpmpk_bot_question_columns(engine)
ensure_tpmpk_slot_minutes_range(engine)
ensure_tpmpk_duplicate_guard(engine)
with SessionLocal() as seed_db:
    ensure_dev_test_users(seed_db)

app.include_router(certificates_router)
app.include_router(users_router)
app.include_router(tpmpk_router)
app.include_router(dom_uchitelya_router)


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
        allowed_methodika_subjects=getattr(user, "allowed_methodika_subjects", None) or [],
    )


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    username = user_data.username or user_data.email.split("@")[0]
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username is already taken")
    user = User(
        email=user_data.email,
        username=username,
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
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role_name = _user_role_name(db, user)
    access_token = create_access_token(
        data={"sub": user.email, "role": role_name},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=access_token, role=role_name, user=_user_response(db, user))


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_response(db, current_user)
