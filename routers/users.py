from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from auth import hash_password
from database import get_db
from models import User, UserRole
from schemas import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

def _normalize_role_name(role_name: str | None) -> str:
    return str(role_name or "user").strip().lower() or "user"


def _role_by_name(db: Session, role_name: str | None) -> UserRole | None:
    normalized = _normalize_role_name(role_name)
    return db.query(UserRole).filter(UserRole.role_name == normalized).first()


# ====================== ПОЛУЧЕНИЕ ПОЛЬЗОВАТЕЛЕЙ ======================
@router.get("/", response_model=List[UserResponse])
def get_all_users(db: Session = Depends(get_db)):
    """Получить список всех пользователей"""
    users = db.query(User).all()
    return users


@router.get("/roles/")
def get_all_roles(db: Session = Depends(get_db)):
    roles = db.query(UserRole).order_by(UserRole.role_name.asc()).all()
    return [
        {
            "id": role.id,
            "role_name": role.role_name,
            "permissions": getattr(role, "permissions", {}) or {},
        }
        for role in roles
    ]


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

    role = _role_by_name(db, user_data.role)
    if not role:
        raise HTTPException(status_code=400, detail="Role not found")

    new_user = User(
        last_name=user_data.last_name,
        first_name=user_data.first_name,
        middle_name=user_data.middle_name,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        is_active=user_data.is_active,
        role_id=role.id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


# ====================== ОБНОВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ======================
@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: Session = Depends(get_db)
):
    """Обновить данные пользователя"""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    payload = user_data.model_dump(exclude_unset=True)
    if "email" in payload:
        user.email = payload["email"]
    if "last_name" in payload:
        user.last_name = payload["last_name"]
    if "first_name" in payload:
        user.first_name = payload["first_name"]
    if "middle_name" in payload:
        user.middle_name = payload["middle_name"]
    if payload.get("password"):
        user.password_hash = hash_password(payload["password"])
    if "is_active" in payload:
        user.is_active = payload["is_active"]
    if "role" in payload:
        role = _role_by_name(db, payload["role"])
        if not role:
            raise HTTPException(status_code=400, detail="Role not found")
        user.role_id = role.id

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
