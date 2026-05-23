from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import hash_password
from database import get_db
from models import User, UserRole
from permissions import normalize_role_permissions, require_admin_user
from schemas import RolePermissionsUpdate, RoleResponse, UserAdminCreate, UserAdminUpdate, UserResponse

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_admin_user)],
)


def _normalize_role_name(role_name: str | None) -> str | None:
    normalized = str(role_name or "").strip().lower()
    return normalized or None


def _role_or_400(db: Session, role_name: str | None) -> UserRole | None:
    normalized = _normalize_role_name(role_name)
    if normalized is None:
        return None
    role = db.query(UserRole).filter(UserRole.role_name == normalized).first()
    if not role:
        raise HTTPException(status_code=400, detail="Указанная роль не найдена")
    return role


def _username_or_default(email: str, username: str | None = None) -> str:
    return (username or email.split("@")[0]).strip()


def _ensure_unique_user_identity(
    db: Session,
    *,
    email: str | None = None,
    username: str | None = None,
    exclude_user_id: int | None = None,
) -> None:
    if email:
        query = db.query(User).filter(User.email == email)
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        if query.first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким email уже существует",
            )

    if username:
        query = db.query(User).filter(User.username == username)
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        if query.first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким именем уже существует",
            )


@router.get("/", response_model=List[UserResponse])
def get_all_users(db: Session = Depends(get_db)):
    """Получить список всех пользователей."""
    return db.query(User).order_by(User.id.asc()).all()


@router.get("/roles/", response_model=List[RoleResponse])
def get_all_roles(db: Session = Depends(get_db)):
    """Получить список доступных ролей пользователей."""
    roles = db.query(UserRole).order_by(UserRole.role_name.asc()).all()
    for role in roles:
        role.permissions = normalize_role_permissions(role.permissions, role.role_name)
    return roles


@router.put("/roles/{role_id}/permissions/", response_model=RoleResponse)
def update_role_permissions(
    role_id: int,
    permissions_data: RolePermissionsUpdate,
    db: Session = Depends(get_db),
):
    """Обновить права доступа выбранной роли к разделам системы."""
    role = db.query(UserRole).filter_by(id=role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")

    role.permissions = normalize_role_permissions(permissions_data.permissions, role.role_name)
    db.commit()
    db.refresh(role)
    role.permissions = normalize_role_permissions(role.permissions, role.role_name)
    return role


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Получить одного пользователя по ID."""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user_data: UserAdminCreate, db: Session = Depends(get_db)):
    """Создать нового пользователя."""
    username = _username_or_default(user_data.email, user_data.username)
    _ensure_unique_user_identity(db, email=user_data.email, username=username)
    role = _role_or_400(db, user_data.role)

    new_user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        username=username,
        last_name=user_data.last_name,
        first_name=user_data.first_name,
        middle_name=user_data.middle_name,
        is_active=user_data.is_active,
        role_id=role.id if role else None,
        allowed_methodika_subjects=user_data.allowed_methodika_subjects,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserAdminUpdate,
    db: Session = Depends(get_db),
):
    """Обновить данные пользователя."""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    payload = user_data.model_dump(exclude_unset=True)
    email = payload.get("email")
    username = payload.get("username")
    if email and username is None:
        username = user.username or _username_or_default(email)

    _ensure_unique_user_identity(
        db,
        email=email,
        username=username,
        exclude_user_id=user.id,
    )

    if email is not None:
        user.email = email
    if username is not None:
        user.username = username
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
    if "allowed_methodika_subjects" in payload:
        user.allowed_methodika_subjects = payload["allowed_methodika_subjects"] or []
    if "role" in payload:
        role = _role_or_400(db, payload["role"])
        user.role_id = role.id if role else None

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Удалить пользователя."""
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    db.delete(user)
    db.commit()
    return None
