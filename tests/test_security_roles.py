from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.tpmpk.router import _irkutsk_today, router as tpmpk_router
from auth import create_access_token, get_current_user, hash_password
from database import Base, get_db
from dom_uchitelya.router import router as dom_uchitelya_router
from models import User, UserRole
from models.tpmpk import TPMPKAuditLog, TPMPKScheduleTemplate, TPMPKUser, TPMPKWorkingDay
from datetime import time
from routers.certificates import router as certificates_router
from routers.users import router as users_router


def _client_for(router):
    client, _ = _client_and_session_factory_for(router)
    return client


def _client_and_session_factory_for(router):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal


def _user(role: str):
    return SimpleNamespace(id=10, email=f"{role}@example.test", role=role, is_active=True)


def test_users_crud_requires_admin_role():
    client = _client_for(users_router)

    anonymous = client.get("/users/")
    assert anonymous.status_code == 401

    client.app.dependency_overrides[get_current_user] = lambda: _user("user")
    regular_user = client.get("/users/")
    assert regular_user.status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: _user("admin")
    admin = client.get("/users/")
    assert admin.status_code == 200


def test_certificate_template_mutations_require_certificate_manager_role():
    client = _client_for(certificates_router)
    payload = {
        "name": "Protected template",
        "background_url": None,
        "signers_y_mm": 248,
        "signers_block_x_mm": 105,
        "signers_row_height_mm": 32,
        "signers_band_width_mm": 168,
        "signers_font_size": 10,
        "signers_text_color": "#1e293b",
        "signers_font_weight": "400",
        "signers_font_family": "DejaVu",
        "margin_left_mm": 12,
        "margin_right_mm": 12,
        "margin_top_mm": 12,
        "margin_bottom_mm": 12,
    }

    anonymous = client.post("/certificates/templates", json=payload)
    assert anonymous.status_code == 401

    client.app.dependency_overrides[get_current_user] = lambda: _user("user")
    regular_user = client.post("/certificates/templates", json=payload)
    assert regular_user.status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: _user("methodist")
    methodist = client.post("/certificates/templates", json=payload)
    assert methodist.status_code == 200


def test_article_editor_admin_routes_require_editor_role():
    client = _client_for(dom_uchitelya_router)

    anonymous = client.get("/api/admin/news/")
    assert anonymous.status_code == 401

    client.app.dependency_overrides[get_current_user] = lambda: _user("user")
    regular_user = client.get("/api/admin/news/")
    assert regular_user.status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: _user("methodist")
    methodist = client.get("/api/admin/news/")
    assert methodist.status_code == 200


def test_tpmpk_admin_endpoints_require_operator_role():
    client = _client_for(tpmpk_router)

    anonymous = client.get("/api/tpmpk/admin/audit/")
    assert anonymous.status_code == 401

    client.app.dependency_overrides[get_current_user] = lambda: _user("user")
    regular_user = client.get("/api/tpmpk/admin/audit/")
    assert regular_user.status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: _user("operator")
    operator = client.get("/api/tpmpk/admin/audit/")
    assert operator.status_code == 200


def test_tpmpk_admin_accepts_real_bearer_token_for_operator_role():
    client, SessionLocal = _client_and_session_factory_for(tpmpk_router)
    db = SessionLocal()
    try:
        role = UserRole(role_name="operator")
        db.add(role)
        db.flush()
        db.add(User(
            email="operator@example.test",
            username="tpmpk_operator",
            password_hash=hash_password("operator123"),
            is_active=True,
            role_id=role.id,
        ))
        for weekday in range(7):
            db.add(TPMPKScheduleTemplate(
                id=weekday + 1,
                weekday=weekday,
                is_working_default=weekday < 5,
                open_time=time(9, 0) if weekday < 5 else None,
                close_time=time(17, 0) if weekday < 5 else None,
                lunch_start=time(13, 0) if weekday < 5 else None,
                lunch_end=time(14, 0) if weekday < 5 else None,
                slot_minutes=30,
            ))
        db.add(TPMPKWorkingDay(
            id=100,
            date=_irkutsk_today(),
            is_open=True,
            open_time=time(9, 0),
            close_time=time(17, 0),
            lunch_start=time(13, 0),
            lunch_end=time(14, 0),
            slot_minutes=30,
        ))
        db.commit()
    finally:
      db.close()

    token = create_access_token({"sub": "operator@example.test", "role": "operator"})
    headers = {"Authorization": f"Bearer {token}"}

    for path in (
        "/api/tpmpk/admin/dashboard/",
        "/api/tpmpk/admin/day/",
        "/api/tpmpk/admin/appointments/",
        "/api/tpmpk/admin/audit/",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, path


def test_tpmpk_dashboard_initializes_schedule_on_sqlite_without_preseed():
    client, SessionLocal = _client_and_session_factory_for(tpmpk_router)
    db = SessionLocal()
    try:
        role = UserRole(role_name="operator")
        db.add(role)
        db.flush()
        db.add(User(
            email="operator@example.test",
            username="tpmpk_operator",
            password_hash=hash_password("operator123"),
            is_active=True,
            role_id=role.id,
        ))
        db.commit()
    finally:
        db.close()

    token = create_access_token({"sub": "operator@example.test", "role": "operator"})
    response = client.get(
        "/api/tpmpk/admin/dashboard/",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"]
    assert payload["nearest_slot"] == "09:00"
    assert payload["today_appointments"] == []


def test_tpmpk_audit_returns_actor_display_name_with_email_fallback():
    client, SessionLocal = _client_and_session_factory_for(tpmpk_router)
    db = SessionLocal()
    try:
        actor = TPMPKUser(
            id=501,
            email="operator@example.test",
            password_hash="not-used",
            role="operator",
        )
        db.add(actor)
        db.flush()
        db.add(TPMPKAuditLog(
            id=701,
            user_id=actor.id,
            action="update_day",
            object_type="working_day",
            object_id=42,
            payload={"date": "2026-05-12"},
        ))
        db.commit()
    finally:
        db.close()

    client.app.dependency_overrides[get_current_user] = lambda: _user("operator")

    response = client.get("/api/tpmpk/admin/audit/")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["user_display_name"] == "operator@example.test"
    assert item["user_email"] == "operator@example.test"
