"""Create or update an admin user from environment variables."""

from __future__ import annotations

import os

from sqlalchemy import inspect, text

from auth import hash_password
from database import Base, SessionLocal, engine
from models import User, UserRole

LOGIN = os.getenv("ADMIN_EMAIL", "admin@example.local")
USERNAME = os.getenv("ADMIN_USERNAME", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD")
ROLE = os.getenv("ADMIN_ROLE", "admin")


def migrate_users_table() -> None:
    """Bring the users table in line with models.User for older databases."""
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        if "hashed_password" in cols and "password_hash" not in cols:
            conn.execute(text("ALTER TABLE users RENAME COLUMN hashed_password TO password_hash"))
        if "username" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(100)"))
        if "role_id" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES user_role(id)"))
        if "created_at" not in cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT now()"
            ))
        existing_indexes = {ix["name"] for ix in insp.get_indexes("users")}
        if "ix_users_username" not in existing_indexes:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"
            ))


def main() -> None:
    if not PASSWORD:
        raise SystemExit("Set ADMIN_PASSWORD before running create_admin.py")

    Base.metadata.create_all(bind=engine)
    migrate_users_table()
    db = SessionLocal()
    try:
        role = db.query(UserRole).filter_by(role_name=ROLE).first()
        if not role:
            role = UserRole(role_name=ROLE)
            db.add(role)
            db.commit()
            db.refresh(role)
            print(f"Created role: {role.role_name} (id={role.id})")
        else:
            print(f"Role already exists: {role.role_name} (id={role.id})")

        user = db.query(User).filter_by(email=LOGIN).first()
        if user:
            user.password_hash = hash_password(PASSWORD)
            user.role_id = role.id
            user.is_active = True
            user.username = USERNAME
            db.commit()
            db.refresh(user)
            print(f"Updated user: {user.email} (id={user.id}, role_id={user.role_id})")
        else:
            user = User(
                email=LOGIN,
                password_hash=hash_password(PASSWORD),
                username=USERNAME,
                is_active=True,
                role_id=role.id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Created user: {user.email} (id={user.id}, role_id={user.role_id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
