from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from datetime import datetime
from typing import Optional, List, Dict


# ====================== Аутентификация ======================
class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    id: int
    email: str
    username: Optional[str] = None
    is_active: bool
    role: Optional[str] = None
    can_access_internal_docs: bool = False
    allowed_methodika_subjects: List[str] = Field(default_factory=list)

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return getattr(value, "role_name", None)

    @field_validator("allowed_methodika_subjects", mode="before")
    @classmethod
    def _normalize_allowed_subjects(cls, value):
        return value or []

    @field_validator("can_access_internal_docs", mode="before")
    @classmethod
    def _normalize_internal_docs_access(cls, value):
        return bool(value)

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    role: Optional[str] = None
    user: Optional["UserResponse"] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenData(BaseModel):
    email: str | None = None


# ====================== ШАБЛОНЫ ======================
PUBLISHING_SCOPES = {"imcro_only", "dom_uchitelya_only", "both"}
ARTICLE_STATUSES = {"draft", "published", "archive"}


class ArticleBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    slug: str = Field(..., min_length=1, max_length=160)
    status: str = Field("draft", max_length=20)
    excerpt: Optional[str] = Field(None, max_length=800)
    image: Optional[str] = Field(None, max_length=500)
    lead: Optional[str] = Field(None, max_length=800)
    body: str = ""
    cover_image_url: Optional[str] = Field(None, max_length=500)
    is_pinned: bool = False
    duplicate_to_main: bool = False
    duplicate_to_events: bool = False
    blocks: List[Dict] = Field(default_factory=list)
    attachments: List[Dict] = Field(default_factory=list)
    categories: List = Field(default_factory=list)
    tags: List = Field(default_factory=list)
    publishing_scope: str = "both"
    methodika_subject: Optional[str] = Field(None, max_length=120)
    dom_uchitelya_section: Optional[str] = Field(None, max_length=120)
    noko_section: Optional[str] = Field(None, max_length=120)
    hub_kind: Optional[str] = Field(None, max_length=64)
    hub_path: Optional[str] = Field(None, max_length=160)
    published_at: Optional[datetime] = None

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        slug = "-".join(str(value or "").strip().lower().split())
        if not slug:
            raise ValueError("slug is required")
        return slug

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in ARTICLE_STATUSES:
            raise ValueError("status must be draft, published, or archive")
        return value

    @field_validator("publishing_scope")
    @classmethod
    def _validate_publishing_scope(cls, value: str) -> str:
        if value not in PUBLISHING_SCOPES:
            raise ValueError("publishing_scope must be imcro_only, dom_uchitelya_only, or both")
        return value

    @model_validator(mode="after")
    def _sync_legacy_fields(self):
        if self.lead is None and self.excerpt is not None:
            self.lead = self.excerpt
        if self.excerpt is None and self.lead is not None:
            self.excerpt = self.lead
        if self.cover_image_url is None and self.image is not None:
            self.cover_image_url = self.image
        if self.image is None and self.cover_image_url is not None:
            self.image = self.cover_image_url
        return self


class ArticleCreate(ArticleBase):
    pass


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    slug: Optional[str] = Field(None, min_length=1, max_length=160)
    status: Optional[str] = Field(None, max_length=20)
    excerpt: Optional[str] = Field(None, max_length=800)
    image: Optional[str] = Field(None, max_length=500)
    lead: Optional[str] = Field(None, max_length=800)
    body: Optional[str] = None
    cover_image_url: Optional[str] = Field(None, max_length=500)
    is_pinned: Optional[bool] = None
    duplicate_to_main: Optional[bool] = None
    duplicate_to_events: Optional[bool] = None
    blocks: Optional[List[Dict]] = None
    attachments: Optional[List[Dict]] = None
    categories: Optional[List] = None
    tags: Optional[List] = None
    publishing_scope: Optional[str] = None
    methodika_subject: Optional[str] = Field(None, max_length=120)
    dom_uchitelya_section: Optional[str] = Field(None, max_length=120)
    noko_section: Optional[str] = Field(None, max_length=120)
    hub_kind: Optional[str] = Field(None, max_length=64)
    hub_path: Optional[str] = Field(None, max_length=160)
    published_at: Optional[datetime] = None

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str | None) -> str | None:
        if value is None:
            return value
        slug = "-".join(str(value or "").strip().lower().split())
        if not slug:
            raise ValueError("slug is required")
        return slug

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in ARTICLE_STATUSES:
            raise ValueError("status must be draft, published, or archive")
        return value

    @field_validator("publishing_scope")
    @classmethod
    def _validate_publishing_scope(cls, value: str | None) -> str | None:
        if value is not None and value not in PUBLISHING_SCOPES:
            raise ValueError("publishing_scope must be imcro_only, dom_uchitelya_only, or both")
        return value


class ArticleResponse(ArticleBase):
    id: int
    author_id: Optional[int]
    author_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArticleListResponse(BaseModel):
    title: Optional[str] = None
    items: List[ArticleResponse]


class CertificateTemplateCreate(BaseModel):
    name: str = Field(..., max_length=200)
    background_url: Optional[str] = None
    signers_y_mm: float = Field(248.0, ge=0, le=297, description="Первая строка подписей от верха листа, мм")
    signers_block_x_mm: float = Field(105.0, ge=0, le=210, description="Центр блока подписей по X, мм")
    signers_row_height_mm: float = Field(32.0, ge=10, le=160, description="Высота строки подписанта, мм")
    signers_band_width_mm: float = Field(168.0, ge=25, le=210, description="Ширина полосы подписей, мм")
    signers_font_size: float = Field(10.0, ge=5, le=36, description="Базовый кегль текста подписей (макс. до auto-fit)")
    signers_text_color: str = Field("#1e293b", max_length=16)
    signers_position_color: Optional[str] = Field(None, max_length=16, description="Цвет должности (если None — signers_text_color)")
    signers_name_color: Optional[str] = Field(None, max_length=16, description="Цвет ФИО (если None — signers_text_color)")
    signers_font_weight: str = Field("400", max_length=8, description="400–800 (жирность, при 600+ — полужирный шрифт если есть)")
    signers_font_family: str = Field("DejaVu", max_length=120)
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
    signers_position_color: Optional[str]
    signers_name_color: Optional[str]
    signers_font_weight: str
    signers_font_family: str
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
    color: str = Field("#0F172A", max_length=16)
    font_weight: str = Field("400", max_length=8)
    font_family: str = Field("DejaVu", max_length=120)
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
    color: str
    font_weight: str
    font_family: str
    max_width_mm: Optional[float]
    max_height_mm: Optional[float]

    model_config = {"from_attributes": True}


class TemplateVariablesResponse(BaseModel):
    template_id: int
    variables: List[str]


class ExcelInspectResponse(BaseModel):
    headers: List[str]
    row_count: int
    fio_column: Optional[str]
    preview_rows: List[Dict[str, str]]
    template_variables: List[str]
    matched_columns: List[str]
    missing_columns: List[str]


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
    color: str = "#0F172A"
    font_weight: str = "400"
    font_family: str = Field("DejaVu", max_length=120)
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
    signers_position_color: Optional[str] = Field(None, max_length=16)
    signers_name_color: Optional[str] = Field(None, max_length=16)
    signers_font_weight: str = Field("400", max_length=8)
    signers_font_family: str = Field("DejaVu", max_length=120)
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


# ====================== ЗАПИСЬ НА ПРИЁМ ======================
class AppointmentCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200, description="ФИО пациента")
    appointment_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Дата приёма YYYY-MM-DD")
    appointment_time: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="Время приёма HH:MM")
    comment: Optional[str] = Field(None, max_length=500)


class AppointmentResponse(BaseModel):
    id: int
    full_name: str
    appointment_date: str
    appointment_time: str
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
