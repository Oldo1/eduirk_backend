from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta
from contextlib import asynccontextmanager

from database import engine, Base, get_db

from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from schemas import UserCreate, UserResponse, Token

from routers.assistant import router as assistant_router, init_rag
from routers.certificates import router as certificates_router
from routers.users import router as users_router
from routers.appointments import router as appointments_router
from utils.schema_patch import ensure_certificate_layout_columns

# ====================== LIFESPAN ======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_rag()
    yield

app = FastAPI(lifespan=lifespan, title="ИМЦРО API")

# ====================== CORS ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ====================== СТАТИЧЕСКИЕ ФАЙЛЫ ======================
app.mount("/static", StaticFiles(directory="static"), name="static")

# ====================== СОЗДАНИЕ ТАБЛИЦ ======================
Base.metadata.create_all(bind=engine)
ensure_certificate_layout_columns(engine)

# ====================== РОУТЕРЫ ======================
app.include_router(assistant_router)
app.include_router(certificates_router)
app.include_router(users_router)
app.include_router(appointments_router)

# ====================== АУТЕНТИФИКАЦИЯ ======================
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


print("✅ Сервер запущен успешно")
print("   • Статические файлы подключены (/static)")
print("   • Аутентификация работает")
print("   • CORS разрешён для localhost:5173")