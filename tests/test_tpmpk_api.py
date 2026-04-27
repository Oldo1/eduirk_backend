from datetime import date, time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.tpmpk.router import router
from api.tpmpk.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    DayTransferRequest,
    ManualAppointmentCreate,
    ScheduleTemplateBulkUpdate,
    SlotResponse,
    WorkingDayUpdate,
)


def test_tpmpk_schemas_are_importable():
    appointment = AppointmentCreate(
        working_day_id=1,
        start_time=time(9, 0),
        child_full_name="Test Child",
        child_age=7,
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


def test_zapis_requires_both_consents():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    payload = {
        "working_day_id": 1,
        "start_time": "09:00:00",
        "child_full_name": "Test Child",
        "child_age": 7,
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
    assert ("/api/tpmpk/admin/days/", ("GET",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/", ("PATCH",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/toggle/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/template/", ("GET",)) in routes
    assert ("/api/tpmpk/admin/template/", ("PUT",)) in routes
    assert ("/api/tpmpk/admin/template/apply/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/days/{day_id}/transfer/", ("POST",)) in routes
    assert ("/api/tpmpk/admin/manual-appointments/", ("POST",)) in routes


def test_admin_schemas_cover_step_8_payloads():
    day_update = WorkingDayUpdate(
        is_open=True,
        open_time="09:00",
        close_time="17:00",
        lunch_start="13:00",
        lunch_end="14:00",
        slot_minutes=30,
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
                "slot_minutes": 30,
            }
        ]
    )
    transfer = DayTransferRequest(target_date=date(2026, 5, 12), allow_partial=True)
    manual = ManualAppointmentCreate(
        date=date(2026, 5, 12),
        start_time=time(10, 0),
        child_full_name="Phone Child",
        child_age=8,
        parent_phone="+71234567890",
        is_repeat=True,
        needs_psychiatrist=False,
    )

    assert day_update.slot_minutes == 30
    assert template_update.items[0].weekday == 0
    assert transfer.allow_partial is True
    assert manual.source == "phone"
