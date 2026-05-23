from datetime import date, time, timedelta
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.tpmpk.router import _irkutsk_today, _is_future_slot_irkutsk, _is_transferable_status, _keep_source_day_open_after_transfer, router
from api.tpmpk.router import _appointment_duplicate_key
from api.tpmpk.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    DayTransferRequest,
    ManualAppointmentCreate,
    ScheduleTemplateBulkUpdate,
    SlotResponse,
    SlotLockRequest,
    SlotLockReleaseRequest,
    SlotLockResponse,
    WorkingDayUpdate,
)
from database import Base, get_db
from models import TPMPKAppointment
from models.tpmpk import TPMPKWorkingDay


def test_tpmpk_schemas_are_importable():
    appointment = AppointmentCreate(
        working_day_id=1,
        start_time=time(9, 0),
        child_full_name="Test Child",
        child_age=7,
        child_registered_irkutsk=True,
        document_readiness="full",
        parent_phone="+71234567890",
        consent_pd=True,
        consent_special=True,
    )

    slot = SlotResponse(
        working_day_id=1,
        date=date(2026, 4, 25),
        start_time=time(9, 0),
        is_available=True,
    )
    response = AppointmentResponse(
        appointment_id=None,
        working_day_id=appointment.working_day_id,
        start_time=appointment.start_time,
        status="validated",
    )

    assert slot.is_available is True
    assert response.status == "validated"


def test_tpmpk_slot_lock_schemas_are_importable():
    request = SlotLockRequest(
        date=date(2026, 4, 25),
        start_time=time(9, 0),
        session_id="session-123",
    )
    release = SlotLockReleaseRequest(
        working_day_id=1,
        start_time=time(9, 0),
        session_id="session-123",
    )
    response = SlotLockResponse(
        working_day_id=1,
        date=date(2026, 4, 25),
        start_time=time(9, 0),
        session_id="session-123",
        expires_at=datetime(2026, 4, 25, 1, 10, tzinfo=ZoneInfo("UTC")),
    )

    assert request.session_id == release.session_id
    assert response.working_day_id == 1


def test_zapis_requires_both_consents():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    payload = {
        "working_day_id": 1,
        "start_time": "09:00:00",
        "child_full_name": "Test Child",
        "child_age": 7,
        "child_registered_irkutsk": True,
        "document_readiness": "full",
        "parent_phone": "+71234567890",
        "consent_pd": True,
        "consent_special": False,
    }

    response = client.post("/api/tpmpk/zapis/", json=payload)

    assert response.status_code == 400
    assert "согласия" in response.json()["detail"].lower()


def test_tpmpk_router_exposes_required_paths():
    routes = {(route.path, tuple(sorted(route.methods))) for route in router.routes}

    assert ("/api/tpmpk/slots/", ("GET",)) in routes
    assert ("/api/tpmpk/zapis/", ("POST",)) in routes
    assert ("/api/tpmpk/slot-locks/", ("POST",)) in routes
    assert ("/api/tpmpk/slot-locks/", ("DELETE",)) in routes
    assert ("/api/tpmpk/admin/days/", ("GET",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/", ("PATCH",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/toggle/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/template/", ("GET",)) in routes
    assert ("/api/tpmpk/admin/template/", ("PUT",)) in routes
    assert ("/api/tpmpk/admin/template/apply/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/transfer/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/manual-appointments/", ("POST",)) in routes


def test_slot_lock_endpoint_holds_and_releases_slot():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    selected_date = _irkutsk_today() + timedelta(days=2)
    db = TestingSessionLocal()
    try:
        db.add(TPMPKWorkingDay(
            id=1,
            date=selected_date,
            is_open=True,
            open_time=time(9, 0),
            close_time=time(10, 0),
            lunch_start=None,
            lunch_end=None,
            slot_minutes=30,
        ))
        db.commit()
    finally:
        db.close()

    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    payload = {
        "date": selected_date.isoformat(),
        "start_time": "09:00:00",
        "session_id": "session-123",
    }
    locked = client.post("/api/tpmpk/slot-locks/", json=payload)
    assert locked.status_code == 201

    conflict = client.post("/api/tpmpk/slot-locks/", json={**payload, "session_id": "session-456"})
    assert conflict.status_code == 409

    slots = client.get(f"/api/tpmpk/slots/?date={selected_date.isoformat()}")
    assert slots.status_code == 200
    first_slot = next(item for item in slots.json() if item["start_time"].startswith("09:00"))
    assert first_slot["is_available"] is False

    released = client.request("DELETE", "/api/tpmpk/slot-locks/", json=payload)
    assert released.status_code == 200
    assert released.json()["released"] == 1

    slots_after_release = client.get(f"/api/tpmpk/slots/?date={selected_date.isoformat()}")
    first_slot_after_release = next(item for item in slots_after_release.json() if item["start_time"].startswith("09:00"))
    assert first_slot_after_release["is_available"] is True


def test_admin_schemas_cover_step_8_payloads():
    day_update = WorkingDayUpdate(
        is_open=True,
        open_time="09:00",
        close_time="17:00",
        lunch_start="13:00",
        lunch_end="14:00",
        slot_minutes=45,
    )
    template_update = ScheduleTemplateBulkUpdate(
        items=[
            {
                "weekday": 0,
                "is_working_default": True,
                "open_time": "09:00",
                "close_time": "17:00",
                "lunch_start": "13:00",
                "lunch_end": "14:00",
                "slot_minutes": 45,
            }
        ]
    )
    transfer = DayTransferRequest(target_date=date(2026, 5, 12), allow_partial=True)
    manual = ManualAppointmentCreate(
        date=date(2026, 5, 12),
        start_time=time(10, 0),
        child_full_name="Phone Child",
        child_age=8,
        child_registered_irkutsk=True,
        document_readiness="full",
        parent_phone="+71234567890",
        is_repeat=True,
        needs_psychiatrist=False,
    )

    assert day_update.slot_minutes == 45
    assert template_update.items[0].weekday == 0
    assert transfer.allow_partial is True
    assert manual.source == "phone"


def test_slot_minutes_must_be_multiple_of_five():
    with pytest.raises(ValueError, match="кратна 5"):
        WorkingDayUpdate(slot_minutes=33)

    with pytest.raises(ValueError, match="кратна 5"):
        ScheduleTemplateBulkUpdate(
            items=[
                {
                    "weekday": 0,
                    "is_working_default": True,
                    "open_time": "09:00",
                    "close_time": "17:00",
                    "lunch_start": "13:00",
                    "lunch_end": "14:00",
                    "slot_minutes": 33,
                }
            ]
        )


def test_public_slots_use_irkutsk_time_for_past_filtering():
    now = datetime(2026, 4, 28, 18, 40, tzinfo=ZoneInfo("Asia/Irkutsk"))

    assert _is_future_slot_irkutsk(date(2026, 4, 28), time(9, 30), now=now) is False
    assert _is_future_slot_irkutsk(date(2026, 4, 28), time(19, 0), now=now) is True
    assert _is_future_slot_irkutsk(date(2026, 4, 29), time(9, 0), now=now) is True


def test_day_transfer_uses_only_active_appointments():
    assert _is_transferable_status("new") is True
    assert _is_transferable_status("confirmed") is True
    assert _is_transferable_status("done") is False
    assert _is_transferable_status("cancelled") is False


def test_day_transfer_keeps_source_day_open():
    class Day:
        is_open = True

    day = Day()

    _keep_source_day_open_after_transfer(day)

    assert day.is_open is True


def test_appointment_duplicate_key_normalizes_child_phone_and_date():
    first = _appointment_duplicate_key(
        child_full_name="  Иванов   Петр  ",
        selected_date=date(2026, 5, 20),
        parent_phone="+7 (999) 111-22-33",
    )
    second = _appointment_duplicate_key(
        child_full_name="иванов петр",
        selected_date=date(2026, 5, 20),
        parent_phone="89991112233",
    )
    other_day = _appointment_duplicate_key(
        child_full_name="иванов петр",
        selected_date=date(2026, 5, 21),
        parent_phone="89991112233",
    )

    assert first == second
    assert first != other_day


def test_tpmpk_appointment_has_database_duplicate_guard():
    assert "duplicate_key" in TPMPKAppointment.__table__.columns

    duplicate_indexes = [
        index
        for index in TPMPKAppointment.__table__.indexes
        if index.name == "tpmpk_appointment_duplicate_active_uniq"
    ]
    assert duplicate_indexes
    assert duplicate_indexes[0].unique is True
