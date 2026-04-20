from pydantic import BaseModel, EmailStr, Field, model_validator
from datetime import datetime
from typing import Optional, List, Dict


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
    signers_y_mm: float = Field(248.0, ge=0, le=297, description="Первая строка подписей от верха листа, мм")
    signers_block_x_mm: float = Field(105.0, ge=0, le=210, description="Центр блока подписей по X, мм")
    signers_row_height_mm: float = Field(32.0, ge=10, le=160, description="Высота строки подписанта, мм")
    signers_band_width_mm: float = Field(168.0, ge=25, le=210, description="Ширина полосы подписей, мм")
    signers_font_size: float = Field(10.0, ge=5, le=36, description="Базовый кегль текста подписей (макс. до auto-fit)")
    signers_text_color: str = Field("#1e293b", max_length=16)
    signers_font_weight: str = Field("400", max_length=8, description="400–800 (жирность, при 600+ — полужирный шрифт если есть)")
    margin_left_mm: float = Field(12.0, ge=0, le=80)
    margin_right_mm: float = Field(12.0, ge=0, le=80)
    margin_top_mm: float = Field(12.0, ge=0, le=120)
    margin_bottom_mm: float = Field(12.0, ge=0, le=120)


class CertificateTemplateResponse(BaseModel):
    id: int
    name: str
    background_url: Optional[str]
    signers_y_mm: float
    signers_block_x_mm: float
    signers_row_height_mm: float
    signers_band_width_mm: float
    signers_font_size: float
    signers_text_color: str
    signers_font_weight: str
    margin_left_mm: float
    margin_right_mm: float
    margin_top_mm: float
    margin_bottom_mm: float
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
    max_width_mm: Optional[float] = Field(None, ge=5, le=210)
    max_height_mm: Optional[float] = Field(None, ge=5, le=280)


class TemplateTextElementResponse(BaseModel):
    id: int
    text: str
    is_variable: bool
    x_mm: float
    y_mm: float
    font_size: int
    align: str
    max_width_mm: Optional[float]
    max_height_mm: Optional[float]

    model_config = {"from_attributes": True}


# ====================== ГЕНЕРАЦИЯ ======================
class CertificateGenerateRequest(BaseModel):
    """Ровно один из template_id / template_name; variables — значения для {Ключ} в шаблоне."""

    template_id: Optional[int] = None
    template_name: Optional[str] = Field(None, max_length=200)
    variables: Dict[str, str] = Field(default_factory=dict)
    recipient_id: Optional[int] = None
    event_name: Optional[str] = Field(
        None,
        max_length=300,
        description="Устарело: лучше передавать в variables['Мероприятие']",
    )

    @model_validator(mode="after")
    def _validate_generate(self):
        has_id = self.template_id is not None
        has_name = bool((self.template_name or "").strip())
        if has_id and has_name:
            raise ValueError("Укажите только template_id или только template_name")
        if not has_id and not has_name:
            raise ValueError("Нужен template_id или template_name")
        if len(self.variables) > 80:
            raise ValueError("В variables не больше 80 ключей")
        for k, v in self.variables.items():
            if len(str(v)) > 800:
                raise ValueError(f"Значение переменной «{k}» слишком длинное (макс. 800 символов)")
        return self


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
    offset_y_mm: float = Field(0.0, ge=-120, le=160, description="Доп. сдвиг строки вниз, мм")
    facsimile_offset_x_mm: float = Field(0.0, ge=-80, le=80, description="Сдвиг факсимиле вправо, мм")
    facsimile_offset_y_mm: float = Field(0.0, ge=-80, le=80, description="Сдвиг факсимиле вниз по листу, мм")
    facsimile_scale: float = Field(1.0, ge=0.2, le=3.0, description="Множитель размера вписанного изображения")


class TemplateSignerResponse(BaseModel):
    id: int
    template_id: int
    order: int
    position: str
    full_name: str
    facsimile_url: Optional[str]
    offset_y_mm: float
    facsimile_offset_x_mm: float
    facsimile_offset_y_mm: float
    facsimile_scale: float
    created_at: datetime

    model_config = {"from_attributes": True}


# ====================== АТОМАРНОЕ ОБНОВЛЕНИЕ ШАБЛОНА ======================
class TemplateTextElementInput(BaseModel):
    """Элемент текста для атомарного обновления шаблона."""
    text: str
    is_variable: bool = False
    x_mm: float
    y_mm: float
    font_size: int = 24
    align: str = "center"
    max_width_mm: Optional[float] = Field(None, ge=0, le=300)
    max_height_mm: Optional[float] = Field(None, ge=0, le=400)


class TemplateSignerInput(BaseModel):
    """Подписант для атомарного обновления шаблона."""
    order: int = 1
    position: str
    full_name: str
    facsimile_url: Optional[str] = None
    offset_y_mm: float = Field(0.0, ge=-200, le=300)
    facsimile_offset_x_mm: float = Field(0.0, ge=-150, le=150)
    facsimile_offset_y_mm: float = Field(0.0, ge=-150, le=150)
    facsimile_scale: float = Field(1.0, ge=0.1, le=5.0)


class TemplateFullUpdateRequest(BaseModel):
    """
    Атомарное обновление шаблона: метаданные + все элементы + все подписанты.
    Старые элементы и подписанты удаляются и заменяются новыми.
    """
    name: str = Field(..., max_length=200)
    background_url: Optional[str] = None
    signers_y_mm: float = Field(248.0, ge=0, le=400)
    signers_block_x_mm: float = Field(105.0, ge=0, le=300)
    signers_row_height_mm: float = Field(32.0, ge=5, le=300)
    signers_band_width_mm: float = Field(168.0, ge=10, le=400)
    signers_font_size: float = Field(10.0, ge=1, le=72)
    signers_text_color: str = Field("#1e293b", max_length=16)
    signers_font_weight: str = Field("400", max_length=8)
    margin_left_mm: float = Field(12.0, ge=0, le=200)
    margin_right_mm: float = Field(12.0, ge=0, le=200)
    margin_top_mm: float = Field(12.0, ge=0, le=200)
    margin_bottom_mm: float = Field(12.0, ge=0, le=200)
    elements: List[TemplateTextElementInput] = Field(default_factory=list)
    signers: List[TemplateSignerInput] = Field(default_factory=list, max_length=3)


class TemplateFullResponse(BaseModel):
    """Ответ на атомарное обновление: шаблон + элементы + подписанты."""
    template: CertificateTemplateResponse
    elements: List[TemplateTextElementResponse]
    signers: List[TemplateSignerResponse]


# ====================== РУЧНАЯ ВЫДАЧА ======================
class ManualCertificateRequest(BaseModel):
    """
    Ручная выдача одного сертификата: все переменные задаются вручную.
    Обязательные быстрые поля (ФИО, Мероприятие, Дата) + произвольные доп. переменные.
    """
    template_id: Optional[int] = None
    template_name: Optional[str] = Field(None, max_length=200)

    # Быстрые поля (удобство UX)
    fio: str = Field(..., min_length=1, max_length=300, description="ФИО получателя")
    event_name: str = Field(..., min_length=1, max_length=300, description="Название мероприятия")
    date: Optional[str] = Field(None, max_length=100, description="Дата (необязательно)")

    # Произвольные дополнительные переменные {Ключ: Значение}
    extra_variables: Dict[str, str] = Field(
        default_factory=dict,
        description="Дополнительные переменные для подстановки в шаблон",
    )

    @model_validator(mode="after")
    def _validate_manual(self):
        has_id = self.template_id is not None
        has_name = bool((self.template_name or "").strip())
        if has_id and has_name:
            raise ValueError("Укажите только template_id или только template_name")
        if not has_id and not has_name:
            raise ValueError("Нужен template_id или template_name")
        if len(self.extra_variables) > 50:
            raise ValueError("Не более 50 дополнительных переменных")
        for k, v in self.extra_variables.items():
            if len(str(k)) > 100:
                raise ValueError(f"Имя переменной слишком длинное: «{k[:30]}…»")
            if len(str(v)) > 800:
                raise ValueError(f"Значение переменной «{k}» слишком длинное (макс. 800 символов)")
        return self
