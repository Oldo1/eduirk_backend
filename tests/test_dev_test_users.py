from fastapi import FastAPI, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import verify_password, create_access_token
from database import Base, get_db
from models import User
from schemas import Token


def test_dev_test_users_are_seeded_with_expected_credentials():
    from dev_seed import DEV_TEST_USERS, ensure_dev_test_users

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        ensure_dev_test_users(db, enabled=True)

        for credentials in DEV_TEST_USERS:
            user = db.query(User).filter_by(email=credentials["email"]).first()
            assert user is not None, credentials["email"]
            assert user.username == credentials["username"]
            assert user.is_active is True
            assert user.role is not None
            assert user.role.role_name == credentials["role"]
            assert verify_password(credentials["password"], user.password_hash)
    finally:
        db.close()


def test_seeded_dev_test_users_can_login_and_receive_roles():
    from dev_seed import DEV_TEST_USERS, ensure_dev_test_users

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        ensure_dev_test_users(db, enabled=True)
    finally:
        db.close()

    app = FastAPI()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def role_name(session, user):
        return user.role.role_name if user.role else "user"

    @app.post("/auth/login", response_model=Token)
    def login(form_data: OAuth2PasswordRequestForm = Depends(), session=Depends(override_get_db)):
        user = session.query(User).filter(User.email == form_data.username).first()
        assert user is not None
        assert verify_password(form_data.password, user.password_hash)
        role = role_name(session, user)
        token = create_access_token({"sub": user.email, "role": role})
        return {"access_token": token, "role": role, "user": {"id": user.id, "email": user.email, "username": user.username, "is_active": user.is_active, "role": role}}

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    for credentials in DEV_TEST_USERS:
        response = client.post(
            "/auth/login",
            data={"username": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, credentials["email"]
        payload = response.json()
        assert payload["role"] == credentials["role"]
        assert payload["user"]["role"] == credentials["role"]
        assert payload["access_token"]


def test_main_auth_login_accepts_all_dev_test_roles():
    from dev_seed import DEV_TEST_USERS, ensure_dev_test_users
    from main import SessionLocal, app

    db = SessionLocal()
    try:
        ensure_dev_test_users(db, enabled=True)
    finally:
        db.close()

    client = TestClient(app)

    for credentials in DEV_TEST_USERS:
        response = client.post(
            "/auth/login",
            data={"username": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, credentials["email"]
        payload = response.json()
        assert payload["role"] == credentials["role"]
        assert payload["user"]["role"] == credentials["role"]
