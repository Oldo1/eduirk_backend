from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float
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