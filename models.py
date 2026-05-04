from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
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

# ====================== СУЩЕСТВУЮЩИЕ ТАБЛИЦЫ ======================
class UserRole(Base):
    __tablename__ = "user_role"
    id = Column(Integer, primary_key=True, index=True)
    role_name = Column(String(50), unique=True, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    username = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    role_id = Column(Integer, ForeignKey("user_role.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ====================== ТАБЛИЦЫ ДЛЯ ГРАМОТ ======================
class CertificateTemplate(Base):
    __tablename__ = "certificate_templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    background_url = Column(String(500), nullable=True)
    # Вертикальная позиция первой строки подписантов от верхнего края листа (мм)
    signers_y_mm = Column(Float, default=248.0)
    # Центр блока подписей по горизонтали (мм от левого края), ширина полосы и шаг строк
    signers_block_x_mm = Column(Float, default=105.0)
    signers_row_height_mm = Column(Float, default=32.0)
    signers_band_width_mm = Column(Float, default=168.0)
    # Текст подписантов (должность / ФИО): базовый кегль, цвет #RRGGBB, вес 400–800
    signers_font_size = Column(Float, default=10.0)
    signers_text_color = Column(String(16), default="#1e293b")
    signers_font_weight = Column(String(8), default="400")
    # Поля грамоты (мм): внутри этой области якорятся блоки и подрезается текст
    margin_left_mm = Column(Float, default=12.0)
    margin_right_mm = Column(Float, default=12.0)
    margin_top_mm = Column(Float, default=12.0)
    margin_bottom_mm = Column(Float, default=12.0)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TemplateTextElement(Base):
    __tablename__ = "template_text_elements"
    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("certificate_templates.id"), nullable=False)
    text = Column(String(500), nullable=False)
    is_variable = Column(Boolean, default=False)
    x_mm = Column(Float, nullable=False)
    y_mm = Column(Float, nullable=False)
    font_size = Column(Integer, default=24)
    align = Column(String(10), default="center")
    # Ограничение области для auto-fit текста (мм); None — оценка по позиции на листе
    max_width_mm = Column(Float, nullable=True)
    max_height_mm = Column(Float, nullable=True)


class GeneratedCertificate(Base):
    __tablename__ = "generated_certificates"
    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("certificate_templates.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_name = Column(String(300), nullable=True)
    file_url = Column(String(500), nullable=False)
    generated_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())


# ====================== ЗАПИСЬ НА ПРИЁМ ======================
class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(200), nullable=False)
    appointment_date = Column(String(10), nullable=False)   # формат: YYYY-MM-DD
    appointment_time = Column(String(5), nullable=False)    # формат: HH:MM
    comment = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TemplateSigner(Base):
    __tablename__ = "template_signers"
    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("certificate_templates.id"), nullable=False)
    order = Column(Integer, default=1)
    position = Column(String(100), nullable=False)
    full_name = Column(String(200), nullable=False)
    facsimile_url = Column(String(500), nullable=True)
    # Дополнительный сдвиг строки подписанта вниз (мм)
    offset_y_mm = Column(Float, default=0.0)
    # Сдвиг факсимиле относительно центра ячейки: вправо / вниз по листу (мм); масштаб к базовому вписанию
    facsimile_offset_x_mm = Column(Float, default=0.0)
    facsimile_offset_y_mm = Column(Float, default=0.0)
    facsimile_scale = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TPMPKScheduleTemplate(Base):
    __tablename__ = "tpmpk_schedule_template"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="tpmpk_schedule_template_weekday_chk"),
        CheckConstraint(
            "slot_minutes BETWEEN 10 AND 240 AND slot_minutes % 5 = 0",
            name="tpmpk_schedule_template_slot_minutes_chk",
        ),
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
        CheckConstraint(
            "slot_minutes BETWEEN 10 AND 240 AND slot_minutes % 5 = 0",
            name="tpmpk_working_day_slot_minutes_chk",
        ),
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
        CheckConstraint(
            "document_readiness IN ('full', 'not_ready', 'psychiatrist_consultation')",
            name="tpmpk_appointment_document_readiness_chk",
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
    child_registered_irkutsk = Column(Boolean, nullable=False)
    document_readiness = Column(String(40), nullable=False)
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
    payload = Column(JSONB().with_variant(JSON, "sqlite"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
