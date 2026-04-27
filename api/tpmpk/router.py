from datetime import date, datetime, time, timedelta, timezone
import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.tpmpk.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    DayTransferRequest,
    ManualAppointmentCreate,
    ScheduleTemplateBulkUpdate,
    SlotResponse,
    WorkingDayUpdate,
)
from database import get_db
from models import (
    TPMPKAuditLog,
    TPMPKAppointment,
    TPMPKScheduleTemplate,
    TPMPKSlotLock,
    TPMPKUser,
    TPMPKWorkingDay,
)

router = APIRouter(prefix="/api/tpmpk", tags=["tpmpk"])
PD_ENCRYPTION_KEY = os.getenv("PD_ENCRYPTION_KEY", "dev-tpmpk-key-change-me")
DEFAULT_OPEN_TIME = time(9, 0)
DEFAULT_CLOSE_TIME = time(17, 0)
DEFAULT_LUNCH_START = time(13, 0)
DEFAULT_LUNCH_END = time(14, 0)
DEFAULT_SLOT_MINUTES = 30


def _build_day_slots(day: TPMPKWorkingDay) -> list:
    if not day.is_open or not day.open_time or not day.close_time:
        return []

    current = datetime.combine(day.date, day.open_time)
    close_at = datetime.combine(day.date, day.close_time)
    step = timedelta(minutes=day.slot_minutes)
    slots = []

    while current + step <= close_at:
        slot_time = current.time()
        in_lunch = (
            day.lunch_start
            and day.lunch_end
            and day.lunch_start <= slot_time < day.lunch_end
        )
        if not in_lunch:
            slots.append(slot_time)
        current += step

    return slots


def _time_to_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:5]
    return value.strftime("%H:%M")


def _day_to_dict(day: TPMPKWorkingDay) -> dict:
    return {
        "id": day.id,
        "date": day.date.isoformat(),
        "is_open": day.is_open,
        "open_time": _time_to_str(day.open_time),
        "close_time": _time_to_str(day.close_time),
        "lunch_start": _time_to_str(day.lunch_start),
        "lunch_end": _time_to_str(day.lunch_end),
        "slot_minutes": day.slot_minutes,
        "note": day.note,
    }


def _template_to_dict(item: TPMPKScheduleTemplate) -> dict:
    return {
        "id": item.id,
        "weekday": item.weekday,
        "is_working_default": item.is_working_default,
        "open_time": _time_to_str(item.open_time),
        "close_time": _time_to_str(item.close_time),
        "lunch_start": _time_to_str(item.lunch_start),
        "lunch_end": _time_to_str(item.lunch_end),
        "slot_minutes": item.slot_minutes,
    }


def _date_to_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _appointment_to_dict(row) -> dict:
    return {
        "id": row.id,
        "working_day_id": row.working_day_id,
        "date": _date_to_str(row.date),
        "start_time": _time_to_str(row.start_time),
        "child_full_name": row.child_full_name or f"Запись #{row.id}",
        "child_age": row.child_age,
        "is_repeat": row.is_repeat,
        "needs_psychiatrist": row.needs_psychiatrist,
        "consent_pd": row.consent_pd,
        "consent_special": row.consent_special,
        "status": row.status,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _fetch_appointments(db: Session, day: date | None = None) -> list[dict]:
    params = {"key": PD_ENCRYPTION_KEY}
    where = ""
    if day:
        where = "WHERE wd.date = :day"
        params["day"] = day

    rows = db.execute(
        text(
            f"""
            SELECT
                a.id,
                a.working_day_id,
                wd.date,
                a.start_time,
                COALESCE(
                    pgp_sym_decrypt(a.child_full_name, :key),
                    'Запись #' || a.id::text
                ) AS child_full_name,
                a.child_age,
                a.is_repeat,
                a.needs_psychiatrist,
                a.consent_pd,
                a.consent_special,
                a.status,
                a.source,
                a.created_at
            FROM tpmpk_appointment a
            JOIN tpmpk_working_day wd ON wd.id = a.working_day_id
            {where}
            ORDER BY wd.date ASC, a.start_time ASC
            """
        ),
        params,
    ).mappings().all()
    return [_appointment_to_dict(row) for row in rows]


def _get_day_or_404(db: Session, selected_date: date) -> TPMPKWorkingDay:
    return _ensure_working_day(db, selected_date)


def _day_schedule(db: Session, selected_date: date) -> dict:
    day = _get_day_or_404(db, selected_date)
    appointments = {item["start_time"]: item for item in _fetch_appointments(db, selected_date)}
    slots = []
    for slot_time in _build_day_slots(day):
        key = _time_to_str(slot_time)
        appointment = appointments.get(key)
        is_active = appointment and appointment["status"] != "cancelled"
        slots.append({
            "working_day_id": day.id,
            "date": selected_date.isoformat(),
            "start_time": key,
            "status": "occupied" if is_active else "free",
            "appointment": appointment if is_active else None,
        })

    return {"day": _day_to_dict(day), "slots": slots}


def _audit_user_id(db: Session) -> int:
    user = db.query(TPMPKUser).filter(TPMPKUser.email == "system-tpmpk@local").first()
    if user:
        return user.id

    user = TPMPKUser(
        email="system-tpmpk@local",
        password_hash="system",
        role="admin",
    )
    db.add(user)
    db.flush()
    return user.id


def _log_action(db: Session, action: str, object_type: str, object_id: int, payload: dict | None = None):
    db.add(TPMPKAuditLog(
        user_id=_audit_user_id(db),
        action=action,
        object_type=object_type,
        object_id=object_id,
        payload=payload or {},
    ))


def _default_template_row(weekday: int) -> TPMPKScheduleTemplate:
    is_weekday = weekday < 5
    return TPMPKScheduleTemplate(
        weekday=weekday,
        is_working_default=is_weekday,
        open_time=DEFAULT_OPEN_TIME if is_weekday else None,
        close_time=DEFAULT_CLOSE_TIME if is_weekday else None,
        lunch_start=DEFAULT_LUNCH_START if is_weekday else None,
        lunch_end=DEFAULT_LUNCH_END if is_weekday else None,
        slot_minutes=DEFAULT_SLOT_MINUTES,
    )


def _ensure_template(db: Session) -> list[TPMPKScheduleTemplate]:
    existing = {row.weekday: row for row in db.query(TPMPKScheduleTemplate).all()}
    for weekday in range(7):
        if weekday not in existing:
            row = _default_template_row(weekday)
            db.add(row)
            existing[weekday] = row
    db.flush()
    return [existing[weekday] for weekday in range(7)]


def _ensure_working_day(db: Session, selected_date: date) -> TPMPKWorkingDay:
    day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.date == selected_date).first()
    if day:
        return day

    template = _ensure_template(db)[selected_date.weekday()]
    day = TPMPKWorkingDay(
        date=selected_date,
        is_open=template.is_working_default,
        open_time=template.open_time,
        close_time=template.close_time,
        lunch_start=template.lunch_start,
        lunch_end=template.lunch_end,
        slot_minutes=template.slot_minutes,
    )
    db.add(day)
    db.flush()
    return day


def _ensure_days_range(db: Session, start: date, count: int = 60) -> list[TPMPKWorkingDay]:
    for index in range(count):
        _ensure_working_day(db, start + timedelta(days=index))
    db.flush()
    return (
        db.query(TPMPKWorkingDay)
        .filter(TPMPKWorkingDay.date >= start)
        .order_by(TPMPKWorkingDay.date.asc())
        .limit(count)
        .all()
    )


def _free_slots_for_day(db: Session, day: TPMPKWorkingDay) -> list[time]:
    occupied = {
        row.start_time
        for row in db.query(TPMPKAppointment.start_time)
        .filter(
            TPMPKAppointment.working_day_id == day.id,
            TPMPKAppointment.status != "cancelled",
        )
        .all()
    }
    return [slot for slot in _build_day_slots(day) if slot not in occupied]


def _validate_day_hours(day: TPMPKWorkingDay):
    if day.is_open and (not day.open_time or not day.close_time):
        raise HTTPException(status_code=400, detail="Для открытого дня укажите часы работы")
    if day.open_time and day.close_time and day.open_time >= day.close_time:
        raise HTTPException(status_code=400, detail="Время начала должно быть раньше окончания")
    if day.lunch_start and day.lunch_end and day.lunch_start >= day.lunch_end:
        raise HTTPException(status_code=400, detail="Начало обеда должно быть раньше окончания")


@router.get("/slots/", response_model=list[SlotResponse])
def get_slots(date_: date = Query(..., alias="date"), db: Session = Depends(get_db)):
    day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.date == date_).first()
    if not day:
        return []

    occupied = {
        row.start_time
        for row in db.query(TPMPKAppointment.start_time)
        .filter(
            TPMPKAppointment.working_day_id == day.id,
            TPMPKAppointment.status != "cancelled",
        )
        .all()
    }
    locked = {
        row.start_time
        for row in db.query(TPMPKSlotLock.start_time)
        .filter(
            TPMPKSlotLock.working_day_id == day.id,
            TPMPKSlotLock.expires_at > datetime.now(timezone.utc),
        )
        .all()
    }

    busy = occupied | locked
    return [
        SlotResponse(
            working_day_id=day.id,
            date=date_,
            start_time=slot,
            is_available=slot not in busy,
        )
        for slot in _build_day_slots(day)
    ]


@router.post(
    "/zapis/",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(data: AppointmentCreate, db: Session = Depends(get_db)):
    if not (data.consent_pd and data.consent_special):
        raise HTTPException(status_code=400, detail="Требуются оба согласия")

    day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.id == data.working_day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="День не найден")
    if not day.is_open:
        raise HTTPException(status_code=409, detail="День закрыт для записи")

    try:
        row = db.execute(
            text(
                """
                INSERT INTO tpmpk_appointment (
                    working_day_id, start_time, child_full_name, child_age,
                    parent_phone, is_repeat, needs_psychiatrist,
                    consent_pd, consent_special, status, source, created_at
                ) VALUES (
                    :working_day_id, :start_time,
                    pgp_sym_encrypt(:child_full_name, :key), :child_age,
                    pgp_sym_encrypt(:parent_phone, :key),
                    :is_repeat, :needs_psychiatrist,
                    TRUE, TRUE, 'new', 'site', now()
                )
                RETURNING id
                """
            ),
            {
                "working_day_id": data.working_day_id,
                "start_time": data.start_time,
                "child_full_name": data.child_full_name,
                "child_age": data.child_age,
                "parent_phone": data.parent_phone,
                "is_repeat": data.is_repeat,
                "needs_psychiatrist": data.needs_psychiatrist,
                "key": PD_ENCRYPTION_KEY,
            },
        ).one()
        db.execute(
            text(
                """
                DELETE FROM tpmpk_slot_lock
                WHERE working_day_id = :working_day_id AND start_time = :start_time
                """
            ),
            {"working_day_id": data.working_day_id, "start_time": data.start_time},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Слот уже занят")

    return AppointmentResponse(
        appointment_id=row.id,
        working_day_id=data.working_day_id,
        start_time=data.start_time,
        status="new",
    )


@router.get("/admin/dashboard/")
def admin_dashboard(date_: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)):
    date_ = date_ or date.today()
    appointments_today = _fetch_appointments(db, date_)
    active_today = [item for item in appointments_today if item["status"] != "cancelled"]
    new_since = datetime.now(timezone.utc) - timedelta(days=1)
    new_count = db.query(TPMPKAppointment).filter(TPMPKAppointment.created_at >= new_since).count()

    try:
        schedule = _day_schedule(db, date_)
        nearest_slot = next((slot["start_time"] for slot in schedule["slots"] if slot["status"] == "free"), None)
    except HTTPException:
        nearest_slot = None

    return {
        "date": date_.isoformat(),
        "today_count": len(active_today),
        "nearest_slot": nearest_slot,
        "new_24h": new_count,
        "today_appointments": active_today[:6],
    }


@router.get("/admin/day/")
def admin_day(date_: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)):
    date_ = date_ or date.today()
    return _day_schedule(db, date_)


@router.get("/admin/appointments/")
def admin_appointments(date_: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)):
    return {"items": _fetch_appointments(db, date_)}


@router.get("/admin/days/")
def admin_days(db: Session = Depends(get_db)):
    days = _ensure_days_range(db, date.today(), 60)
    db.commit()
    return {"items": [_day_to_dict(day) for day in days]}


@router.patch("/admin/days/{day_id}/")
def update_admin_day(day_id: int, data: WorkingDayUpdate, db: Session = Depends(get_db)):
    day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="День не найден")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(day, field, value)
    _validate_day_hours(day)
    _log_action(db, "update_day", "working_day", day.id, _day_to_dict(day))
    db.commit()
    db.refresh(day)
    return _day_to_dict(day)


@router.post("/admin/days/{day_id}/toggle/")
def toggle_admin_day(day_id: int, db: Session = Depends(get_db)):
    day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="День не найден")

    day.is_open = not day.is_open
    if day.is_open and (not day.open_time or not day.close_time):
        day.open_time = day.open_time or DEFAULT_OPEN_TIME
        day.close_time = day.close_time or DEFAULT_CLOSE_TIME
        day.lunch_start = day.lunch_start or DEFAULT_LUNCH_START
        day.lunch_end = day.lunch_end or DEFAULT_LUNCH_END
    _validate_day_hours(day)
    _log_action(db, "toggle_day", "working_day", day.id, {"is_open": day.is_open})
    db.commit()
    db.refresh(day)
    return _day_to_dict(day)


@router.get("/admin/template/")
def get_admin_template(db: Session = Depends(get_db)):
    items = _ensure_template(db)
    db.commit()
    return {"items": [_template_to_dict(item) for item in items]}


@router.put("/admin/template/")
def update_admin_template(data: ScheduleTemplateBulkUpdate, db: Session = Depends(get_db)):
    existing = {row.weekday: row for row in _ensure_template(db)}
    seen = set()
    for item in data.items:
        if item.weekday in seen:
            raise HTTPException(status_code=400, detail="День недели повторяется в шаблоне")
        seen.add(item.weekday)
        if item.is_working_default and (not item.open_time or not item.close_time):
            raise HTTPException(status_code=400, detail="Для рабочего дня укажите часы приема")
        if item.open_time and item.close_time and item.open_time >= item.close_time:
            raise HTTPException(status_code=400, detail="Время начала должно быть раньше окончания")
        if item.lunch_start and item.lunch_end and item.lunch_start >= item.lunch_end:
            raise HTTPException(status_code=400, detail="Начало обеда должно быть раньше окончания")

        row = existing[item.weekday]
        row.is_working_default = item.is_working_default
        row.open_time = item.open_time if item.is_working_default else None
        row.close_time = item.close_time if item.is_working_default else None
        row.lunch_start = item.lunch_start if item.is_working_default else None
        row.lunch_end = item.lunch_end if item.is_working_default else None
        row.slot_minutes = item.slot_minutes

    _log_action(db, "update_template", "schedule_template", 0, {"weekdays": sorted(seen)})
    db.commit()
    return {"items": [_template_to_dict(item) for item in _ensure_template(db)]}


@router.post("/admin/template/apply/")
def apply_admin_template(db: Session = Depends(get_db)):
    templates = {row.weekday: row for row in _ensure_template(db)}
    days = _ensure_days_range(db, date.today(), 60)
    for day in days:
        template = templates[day.date.weekday()]
        day.is_open = template.is_working_default
        day.open_time = template.open_time
        day.close_time = template.close_time
        day.lunch_start = template.lunch_start
        day.lunch_end = template.lunch_end
        day.slot_minutes = template.slot_minutes
    _log_action(db, "apply_template", "working_day", 0, {"days": len(days)})
    db.commit()
    return {"status": "ok", "updated": len(days)}


@router.post(
    "/admin/manual-appointments/",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_appointment(data: ManualAppointmentCreate, db: Session = Depends(get_db)):
    day = _ensure_working_day(db, data.date)
    if not day.is_open:
        raise HTTPException(status_code=409, detail="День закрыт для записи")

    available = _free_slots_for_day(db, day)
    if data.start_time not in available:
        raise HTTPException(status_code=409, detail="Слот занят или недоступен")

    try:
        row = db.execute(
            text(
                """
                INSERT INTO tpmpk_appointment (
                    working_day_id, start_time, child_full_name, child_age,
                    parent_phone, is_repeat, needs_psychiatrist,
                    consent_pd, consent_special, status, source, created_at
                ) VALUES (
                    :working_day_id, :start_time,
                    pgp_sym_encrypt(:child_full_name, :key), :child_age,
                    pgp_sym_encrypt(:parent_phone, :key),
                    :is_repeat, :needs_psychiatrist,
                    TRUE, TRUE, 'new', 'phone', now()
                )
                RETURNING id
                """
            ),
            {
                "working_day_id": day.id,
                "start_time": data.start_time,
                "child_full_name": data.child_full_name,
                "child_age": data.child_age,
                "parent_phone": data.parent_phone,
                "is_repeat": data.is_repeat,
                "needs_psychiatrist": data.needs_psychiatrist,
                "key": PD_ENCRYPTION_KEY,
            },
        ).one()
        _log_action(db, "create_phone_appointment", "appointment", row.id, {"date": data.date.isoformat()})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Слот уже занят")

    return AppointmentResponse(
        appointment_id=row.id,
        working_day_id=day.id,
        start_time=data.start_time,
        status="new",
    )


@router.post("/admin/days/{day_id}/transfer/")
def transfer_admin_day(day_id: int, data: DayTransferRequest, db: Session = Depends(get_db)):
    source_day = db.query(TPMPKWorkingDay).filter(TPMPKWorkingDay.id == day_id).first()
    if not source_day:
        raise HTTPException(status_code=404, detail="День не найден")
    if data.target_date == source_day.date:
        raise HTTPException(status_code=400, detail="Выберите другую дату для переноса")

    target_day = _ensure_working_day(db, data.target_date)
    if not target_day.is_open:
        raise HTTPException(status_code=409, detail="Новая дата закрыта для записи")

    appointments = (
        db.query(TPMPKAppointment)
        .filter(
            TPMPKAppointment.working_day_id == source_day.id,
            TPMPKAppointment.status != "cancelled",
        )
        .order_by(TPMPKAppointment.start_time.asc())
        .all()
    )
    free_slots = _free_slots_for_day(db, target_day)
    if len(free_slots) < len(appointments) and not data.allow_partial:
        return {
            "status": "not_enough_slots",
            "appointments": len(appointments),
            "free_slots": len(free_slots),
            "can_move": min(len(appointments), len(free_slots)),
        }

    moved = []
    for appointment, slot_time in zip(appointments, free_slots):
        appointment.working_day_id = target_day.id
        appointment.start_time = slot_time
        moved.append({"appointment_id": appointment.id, "start_time": _time_to_str(slot_time)})

    source_day.is_open = False
    _log_action(
        db,
        "transfer_day",
        "working_day",
        source_day.id,
        {
            "target_day_id": target_day.id,
            "target_date": target_day.date.isoformat(),
            "moved": len(moved),
            "total": len(appointments),
            "partial": len(moved) < len(appointments),
        },
    )
    db.commit()
    return {
        "status": "ok",
        "source_day": _day_to_dict(source_day),
        "target_day": _day_to_dict(target_day),
        "moved": moved,
        "not_moved": max(0, len(appointments) - len(moved)),
    }


@router.get("/admin/audit/")
def admin_audit(db: Session = Depends(get_db)):
    rows = db.query(TPMPKAuditLog).order_by(TPMPKAuditLog.created_at.desc()).limit(100).all()
    return {
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "action": row.action,
                "object_type": row.object_type,
                "object_id": row.object_id,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/admin/appointments/{appointment_id}/reveal-phone/")
def reveal_phone(appointment_id: int, db: Session = Depends(get_db)):
    phone = db.execute(
        text(
            """
            SELECT pgp_sym_decrypt(parent_phone, :key) AS phone
            FROM tpmpk_appointment
            WHERE id = :appointment_id
            """
        ),
        {"appointment_id": appointment_id, "key": PD_ENCRYPTION_KEY},
    ).scalar()
    if phone is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    db.add(TPMPKAuditLog(
        user_id=_audit_user_id(db),
        action="reveal_phone",
        object_type="appointment",
        object_id=appointment_id,
        payload={"field": "parent_phone"},
    ))
    db.commit()
    return {"phone": phone}


@router.post("/admin/appointments/{appointment_id}/cancel/")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = db.query(TPMPKAppointment).filter(TPMPKAppointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    appointment.status = "cancelled"
    db.add(TPMPKAuditLog(
        user_id=_audit_user_id(db),
        action="cancel_appointment",
        object_type="appointment",
        object_id=appointment_id,
        payload={"status": "cancelled"},
    ))
    db.commit()
    return {"status": "cancelled"}


@router.post("/admin/appointments/{appointment_id}/done/")
def complete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = db.query(TPMPKAppointment).filter(TPMPKAppointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    appointment.status = "done"
    db.add(TPMPKAuditLog(
        user_id=_audit_user_id(db),
        action="done_appointment",
        object_type="appointment",
        object_id=appointment_id,
        payload={"status": "done"},
    ))
    db.commit()
    return {"status": "done"}
