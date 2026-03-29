from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import User, UserRole
from schemas import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


# ====================== ПОЛУЧЕНИЕ ПОЛЬЗОВАТЕЛЕЙ ======================
@router.get("/", response_model=List[UserResponse])
def get_all_users(db: Session = Depends(get_db)):
    """Получить список всех пользователей"""
    users = db.query(User).all()
    return users


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Получить одного пользователя по ID"""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


# ====================== СОЗДАНИЕ ПОЛЬЗОВАТЕЛЯ ======================
@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """Создать нового пользователя"""
    existing = db.query(User).filter_by(email=user_data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким email уже существует"
        )

    new_user = User(
        email=user_data.email,
        password_hash=user_data.password,   # В реальном проекте здесь должен быть hash_password()
        username=user_data.email.split('@')[0],  # временно
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


# ====================== ОБНОВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ======================
@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """Обновить данные пользователя"""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.email = user_data.email
    user.password_hash = user_data.password  # В будущем — хэширование
    user.username = user_data.email.split('@')[0]

    db.commit()
    db.refresh(user)
    return user


# ====================== УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ======================
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Удалить пользователя"""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    db.delete(user)
    db.commit()
    return None