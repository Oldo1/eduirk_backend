"""Development-only test users for local demos and role checks."""

from __future__ import annotations

import os
from typing import Iterable

from sqlalchemy.orm import Session

from auth import hash_password, verify_password
from models import User, UserRole
from permissions import default_permissions_for_role, normalize_role_permissions


DEV_TEST_USERS = [
    {
        "email": "user@mky.test",
        "username": "smirnov_ap",
        "last_name": "Смирнов",
        "first_name": "Алексей",
        "middle_name": "Петрович",
        "password": "user123",
        "role": "user",
    },
    {
        "email": "methodist@mky.test",
        "username": "abramova_iv",
        "last_name": "Абрамова",
        "first_name": "Ирина",
        "middle_name": "Владимировна",
        "password": "methodist123",
        "role": "methodist",
    },
    {
        "email": "operator@mky.test",
        "username": "tpmpk_operator",
        "last_name": "Петрова",
        "first_name": "Ольга",
        "middle_name": "Сергеевна",
        "password": "operator123",
        "role": "operator",
    },
    {
        "email": "admin@mky.test",
        "username": "admin_mky",
        "last_name": "Кузнецова",
        "first_name": "Марина",
        "middle_name": "Андреевна",
        "password": "admin123",
        "role": "admin",
    },
    {
        "email": "domu@mky.test",
        "username": "domu_editor",
        "last_name": "Соколова",
        "first_name": "Елена",
        "middle_name": "Павловна",
        "password": "domu123",
        "role": "domu_editor",
    },
]


def dev_test_users_enabled() -> bool:
    raw = os.getenv("ENABLE_DEV_TEST_USERS")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    secret = os.getenv("SECRET_KEY", "change-this-secret-in-production")
    return secret in {
        "change-me",
        "change-this-secret-in-dev",
        "change-this-secret-in-production",
    }


def _get_or_create_role(db: Session, role_name: str) -> UserRole:
    role = db.query(UserRole).filter_by(role_name=role_name).first()
    if role:
        role.permissions = normalize_role_permissions(role.permissions, role.role_name)
        return role

    role = UserRole(role_name=role_name, permissions=default_permissions_for_role(role_name))
    db.add(role)
    db.flush()
    return role


def ensure_dev_test_users(
    db: Session,
    users: Iterable[dict[str, str]] = DEV_TEST_USERS,
    enabled: bool | None = None,
) -> None:
    if enabled is None:
        enabled = dev_test_users_enabled()
    if not enabled:
        return

    for credentials in users:
        role = _get_or_create_role(db, credentials["role"])
        user = (
            db.query(User)
            .filter(User.email == credentials["email"])
            .first()
        )

        if user is None:
            user = User(
                email=credentials["email"],
                username=credentials["username"],
                last_name=credentials["last_name"],
                first_name=credentials["first_name"],
                middle_name=credentials["middle_name"],
                password_hash=hash_password(credentials["password"]),
                is_active=True,
                role_id=role.id,
            )
            db.add(user)
            continue

        user.username = credentials["username"]
        user.last_name = credentials["last_name"]
        user.first_name = credentials["first_name"]
        user.middle_name = credentials["middle_name"]
        user.is_active = True
        user.role_id = role.id
        if not verify_password(credentials["password"], user.password_hash):
            user.password_hash = hash_password(credentials["password"])

    db.commit()
