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
    role_id = Column(Integer, ForeignKey("user_role.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ====================== ТАБЛИЦЫ ДЛЯ ГРАМОТ ======================
class CertificateTemplate(Base):
    __tablename__ = "certificate_templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    background_url = Column(String(500), nullable=True)
    signers_y_mm = Column(Integer, default=45)
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())