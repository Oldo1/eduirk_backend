from datetime import date, time

from pydantic import BaseModel, Field, field_validator


class AppointmentCreate(BaseModel):
    working_day_id: int = Field(..., gt=0)
    start_time: time
    child_full_name: str = Field(..., min_length=2, max_length=255)
    child_age: int = Field(..., ge=0, le=18)
    parent_phone: str = Field(..., pattern=r"^\+7\d{10}$")
    is_repeat: bool = False
    needs_psychiatrist: bool = False
    consent_pd: bool
    consent_special: bool


class SlotResponse(BaseModel):
    working_day_id: int
    date: date
    start_time: time
    is_available: bool = True


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
        if value is not None and value not in {30, 60}:
            raise ValueError("slot_minutes must be 30 or 60")
        return value


class ScheduleTemplateUpdate(BaseModel):
    weekday: int = Field(..., ge=0, le=6)
    is_working_default: bool
    open_time: time | None = None
    close_time: time | None = None
    lunch_start: time | None = None
    lunch_end: time | None = None
    slot_minutes: int = Field(..., ge=30, le=60)

    @field_validator("slot_minutes")
    @classmethod
    def validate_template_slot_minutes(cls, value):
        if value not in {30, 60}:
            raise ValueError("slot_minutes must be 30 or 60")
        return value


class ScheduleTemplateBulkUpdate(BaseModel):
    items: list[ScheduleTemplateUpdate] = Field(..., min_length=1, max_length=7)


class DayTransferRequest(BaseModel):
    target_date: date
    allow_partial: bool = False


class ManualAppointmentCreate(BaseModel):
    date: date
    start_time: time
    child_full_name: str = Field(..., min_length=2, max_length=255)
    child_age: int = Field(..., ge=0, le=18)
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
    "SlotResponse",
    "WorkingDayUpdate",
]
