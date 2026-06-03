from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
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
    is_active = Column(Boolean, default=True, nullable=False)
    role_id = Column(Integer, ForeignKey("user_role.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    role = relationship("UserRole")


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
    # Раздельные цвета для должности и ФИО (если None — используется signers_text_color)
    signers_position_color = Column(String(16), nullable=True)
    signers_name_color = Column(String(16), nullable=True)
    signers_font_weight = Column(String(8), default="400")
    signers_font_family = Column(String(120), default="DejaVu")
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
    client_id = Column(String(80), nullable=True)
    element_type = Column(String(32), default="text", nullable=False)
    text = Column(String(1000), nullable=False)
    value = Column(String(1000), nullable=True)
    is_variable = Column(Boolean, default=False)
    x_mm = Column(Float, nullable=False)
    y_mm = Column(Float, nullable=False)
    width_mm = Column(Float, nullable=True)
    height_mm = Column(Float, nullable=True)
    font_size = Column(Integer, default=24)
    align = Column(String(10), default="center")
    color = Column(String(16), default="#0F172A")
    font_weight = Column(String(8), default="400")
    font_family = Column(String(120), default="DejaVu")
    italic = Column(Boolean, default=False)
    underline = Column(Boolean, default=False)
    line_height = Column(Float, nullable=True)
    z_index = Column(Integer, nullable=True)
    hidden = Column(Boolean, default=False)
    locked = Column(Boolean, default=False)
    opacity = Column(Float, nullable=True)
    source_url = Column(String(500), nullable=True)
    variable_name = Column(String(120), nullable=True)
    grammar_settings = Column(JSON, nullable=True)
    signer_group_id = Column(String(80), nullable=True)
    anchor = Column(String(20), nullable=True)
    # Ограничение области для auto-fit текста (мм); None — оценка по позиции на листе
    max_width_mm = Column(Float, nullable=True)
    max_height_mm = Column(Float, nullable=True)

    @property
    def public_id(self):
        return self.client_id or self.id


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
