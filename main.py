from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import timedelta
from contextlib import asynccontextmanager

from database import engine, get_db
from models import Base, User
from schemas import UserCreate, UserResponse, Token
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from routers.assistant import router as assistant_router, init_rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_rag()
    yield

app = FastAPI(lifespan=lifespan)

# CORS — разрешаем запросы с фронта
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # адрес Vite dev-сервера
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.include_router(assistant_router)


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email уже зарегистрирован",
        )
    user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
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
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
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