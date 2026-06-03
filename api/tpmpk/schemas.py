from datetime import date as Date, datetime as DateTime, time

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_SLOT_MINUTES = 10
MAX_SLOT_MINUTES = 240
SLOT_MINUTES_STEP = 5


class AppointmentCreate(BaseModel):
    working_day_id: int = Field(..., gt=0)
    start_time: time
    lock_session_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    child_full_name: str = Field(..., min_length=2, max_length=255)
    child_age: int = Field(..., ge=0, le=18)
    child_registered_irkutsk: bool
    document_readiness: str = Field(
        ...,
        pattern=r"^(full|not_ready|psychiatrist_consultation)$",
    )
    parent_phone: str = Field(..., pattern=r"^\+7\d{10}$")
    is_repeat: bool = False
    needs_psychiatrist: bool = False
    consent_pd: bool
    consent_special: bool


class SlotResponse(BaseModel):
    working_day_id: int
    date: Date
    start_time: time
    is_available: bool = True
    slot_minutes: int | None = None


class SlotLockRequest(BaseModel):
    working_day_id: int | None = Field(default=None, gt=0)
    date: Date | None = None
    start_time: time
    session_id: str = Field(
        ...,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @model_validator(mode="after")
    def validate_day_reference(self):
        if self.working_day_id is None and self.date is None:
            raise ValueError("Укажите дату или идентификатор рабочего дня")
        return self


class SlotLockReleaseRequest(BaseModel):
    working_day_id: int | None = Field(default=None, gt=0)
    date: Date | None = None
    start_time: time
    session_id: str = Field(
        ...,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @model_validator(mode="after")
    def validate_day_reference(self):
        if self.working_day_id is None and self.date is None:
            raise ValueError("Укажите дату или идентификатор рабочего дня")
        return self


class SlotLockResponse(BaseModel):
    working_day_id: int
    date: Date
    start_time: time
    session_id: str
    expires_at: DateTime


class AppointmentResponse(BaseModel):
    appointment_id: int | None = None
    working_day_id: int
    start_time: time
    status: str


class WorkingDayUpdate(BaseModel):
    is_open: bool | None = None
    open_time: time | None = None
    close_time: time | None = None
    lunch_start: time | None = None
    lunch_end: time | None = None
    slot_minutes: int | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("slot_minutes")
    @classmethod
    def validate_slot_minutes(cls, value):
        if value is not None and not MIN_SLOT_MINUTES <= value <= MAX_SLOT_MINUTES:
            raise ValueError(f"Длительность приема должна быть от {MIN_SLOT_MINUTES} до {MAX_SLOT_MINUTES} минут")
        if value is not None and value % SLOT_MINUTES_STEP != 0:
            raise ValueError(f"Длительность приема должна быть кратна {SLOT_MINUTES_STEP} минутам")
        return value


class ScheduleTemplateUpdate(BaseModel):
    weekday: int = Field(..., ge=0, le=6)
    is_working_default: bool
    open_time: time | None = None
    close_time: time | None = None
    lunch_start: time | None = None
    lunch_end: time | None = None
    slot_minutes: int = Field(..., ge=MIN_SLOT_MINUTES, le=MAX_SLOT_MINUTES)

    @field_validator("slot_minutes")
    @classmethod
    def validate_template_slot_minutes(cls, value):
        if not MIN_SLOT_MINUTES <= value <= MAX_SLOT_MINUTES:
            raise ValueError(f"Длительность приема должна быть от {MIN_SLOT_MINUTES} до {MAX_SLOT_MINUTES} минут")
        if value % SLOT_MINUTES_STEP != 0:
            raise ValueError(f"Длительность приема должна быть кратна {SLOT_MINUTES_STEP} минутам")
        return value


class ScheduleTemplateBulkUpdate(BaseModel):
    items: list[ScheduleTemplateUpdate] = Field(..., min_length=1, max_length=7)


class DayTransferRequest(BaseModel):
    target_date: Date
    allow_partial: bool = False


class ManualAppointmentCreate(BaseModel):
    date: Date
    start_time: time
    child_full_name: str = Field(..., min_length=2, max_length=255)
    child_age: int = Field(..., ge=0, le=18)
    child_registered_irkutsk: bool
    document_readiness: str = Field(
        ...,
        pattern=r"^(full|not_ready|psychiatrist_consultation)$",
    )
    parent_phone: str = Field(..., pattern=r"^\+7\d{10}$")
    is_repeat: bool = False
    needs_psychiatrist: bool = False
    source: str = "phone"


__all__ = [
    "AppointmentCreate",
    "AppointmentResponse",
    "DayTransferRequest",
    "ManualAppointmentCreate",
    "ScheduleTemplateBulkUpdate",
    "ScheduleTemplateUpdate",
    "SlotLockReleaseRequest",
    "SlotLockRequest",
    "SlotLockResponse",
    "SlotResponse",
    "WorkingDayUpdate",
]
