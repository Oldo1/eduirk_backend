from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base


class TPMPKScheduleTemplate(Base):
    __tablename__ = "tpmpk_schedule_template"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="tpmpk_schedule_template_weekday_chk"),
        CheckConstraint("slot_minutes IN (30, 60)", name="tpmpk_schedule_template_slot_minutes_chk"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    weekday = Column(SmallInteger, unique=True, nullable=False)
    is_working_default = Column(Boolean, nullable=False, server_default=text("FALSE"))
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    lunch_start = Column(Time, nullable=True)
    lunch_end = Column(Time, nullable=True)
    slot_minutes = Column(Integer, nullable=False)


class TPMPKWorkingDay(Base):
    __tablename__ = "tpmpk_working_day"
    __table_args__ = (
        CheckConstraint("slot_minutes IN (30, 60)", name="tpmpk_working_day_slot_minutes_chk"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)
    is_open = Column(Boolean, nullable=False)
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    lunch_start = Column(Time, nullable=True)
    lunch_end = Column(Time, nullable=True)
    slot_minutes = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)
    created_by_user_id = Column(BigInteger, ForeignKey("tpmpk_user.id"), nullable=True)


class TPMPKAppointment(Base):
    __tablename__ = "tpmpk_appointment"
    __table_args__ = (
        CheckConstraint("child_age BETWEEN 0 AND 18", name="tpmpk_appointment_child_age_chk"),
        CheckConstraint("consent_pd IS TRUE", name="tpmpk_appointment_consent_pd_chk"),
        CheckConstraint("consent_special IS TRUE", name="tpmpk_appointment_consent_special_chk"),
        CheckConstraint(
            "status IN ('new', 'confirmed', 'cancelled', 'done')",
            name="tpmpk_appointment_status_chk",
        ),
        CheckConstraint("source IN ('site', 'phone')", name="tpmpk_appointment_source_chk"),
        Index(
            "tpmpk_appointment_slot_uniq",
            "working_day_id",
            "start_time",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    working_day_id = Column(BigInteger, ForeignKey("tpmpk_working_day.id"), nullable=False)
    start_time = Column(Time, nullable=False)
    child_full_name = Column(LargeBinary, nullable=False)
    child_age = Column(Integer, nullable=False)
    parent_phone = Column(LargeBinary, nullable=False)
    is_repeat = Column(Boolean, nullable=True)
    needs_psychiatrist = Column(Boolean, nullable=True)
    consent_pd = Column(Boolean, nullable=False, server_default=text("TRUE"))
    consent_special = Column(Boolean, nullable=False, server_default=text("TRUE"))
    status = Column(String(20), nullable=False, server_default=text("'new'"))
    source = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by_user_id = Column(BigInteger, ForeignKey("tpmpk_user.id"), nullable=True)


class TPMPKSlotLock(Base):
    __tablename__ = "tpmpk_slot_lock"
    __table_args__ = (
        Index("tpmpk_slot_lock_uniq", "working_day_id", "start_time", unique=True),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    working_day_id = Column(BigInteger, ForeignKey("tpmpk_working_day.id"), nullable=False)
    start_time = Column(Time, nullable=False)
    locked_by_session = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class TPMPKUser(Base):
    __tablename__ = "tpmpk_user"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'operator')", name="tpmpk_user_role_chk"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    totp_secret = Column(LargeBinary, nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class TPMPKAuditLog(Base):
    __tablename__ = "tpmpk_audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("tpmpk_user.id"), nullable=False)
    action = Column(String(50), nullable=False)
    object_type = Column(String(50), nullable=False)
    object_id = Column(BigInteger, nullable=False)
    payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "TPMPKAppointment",
    "TPMPKAuditLog",
    "TPMPKScheduleTemplate",
    "TPMPKSlotLock",
    "TPMPKUser",
    "TPMPKWorkingDay",
]
