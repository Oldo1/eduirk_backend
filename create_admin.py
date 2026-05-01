"""Создаёт роль 'admin' и пользователя admin/admin. Идемпотентно."""
from sqlalchemy import inspect, text
from database import SessionLocal, engine, Base
from models import User, UserRole
from auth import hash_password

LOGIN = "admin"
PASSWORD = "admin"
ROLE = "admin"


def migrate_users_table() -> None:
    """Подтягивает таблицу users к актуальной схеме models.User."""
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
            print(f"Создана роль: {role.role_name} (id={role.id})")
        else:
            print(f"Роль уже существует: {role.role_name} (id={role.id})")

        user = db.query(User).filter_by(email=LOGIN).first()
        if user:
            user.password_hash = hash_password(PASSWORD)
            user.role_id = role.id
            user.is_active = True
            user.username = LOGIN
            db.commit()
            db.refresh(user)
            print(f"Обновлён пользователь: {user.email} (id={user.id}, role_id={user.role_id})")
        else:
            user = User(
                email=LOGIN,
                password_hash=hash_password(PASSWORD),
                username=LOGIN,
                is_active=True,
                role_id=role.id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Создан пользователь: {user.email} (id={user.id}, role_id={user.role_id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
