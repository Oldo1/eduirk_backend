from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional, List


# ====================== Аутентификация ======================
class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: str | None = None


# ====================== ШАБЛОНЫ ======================
class CertificateTemplateCreate(BaseModel):
    name: str = Field(..., max_length=200)
    background_url: Optional[str] = None
    signers_y_mm: int = Field(45, ge=0)


class CertificateTemplateResponse(BaseModel):
    id: int
    name: str
    background_url: Optional[str]
    signers_y_mm: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ====================== ЭЛЕМЕНТЫ ТЕКСТА ======================
class TemplateTextElementCreate(BaseModel):
    text: str
    is_variable: bool = False
    x_mm: float
    y_mm: float
    font_size: int = 24
    align: str = "center"


class TemplateTextElementResponse(BaseModel):
    id: int
    text: str
    is_variable: bool
    x_mm: float
    y_mm: float
    font_size: int
    align: str

    model_config = {"from_attributes": True}


# ====================== ГЕНЕРАЦИЯ ======================
class CertificateGenerateRequest(BaseModel):
    template_id: int
    event_name: str
    recipient_id: Optional[int] = None


class GeneratedCertificateResponse(BaseModel):
    id: int
    template_id: int
    recipient_id: Optional[int]
    event_name: Optional[str]
    file_url: str
    generated_by_id: int
    generated_at: datetime

    model_config = {"from_attributes": True}
    
    
# ====================== ПОДПИСАНТЫ ======================
class TemplateSignerCreate(BaseModel):
    order: int = 1
    position: str
    full_name: str
    facsimile_url: Optional[str] = None


class TemplateSignerResponse(BaseModel):
    id: int
    template_id: int
    order: int
    position: str
    full_name: str
    facsimile_url: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}