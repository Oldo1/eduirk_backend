from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List, Optional
from io import BytesIO
import os
import shutil
import zipfile
from datetime import datetime
from database import get_db
from models import (
    CertificateTemplate, TemplateTextElement, GeneratedCertificate,
    TemplateSigner, User
)
from schemas import (
    CertificateTemplateCreate, CertificateTemplateResponse,
    TemplateTextElementCreate, TemplateTextElementResponse,
    CertificateGenerateRequest, GeneratedCertificateResponse,
    TemplateSignerCreate, TemplateSignerResponse,
    ManualCertificateRequest,
    TemplateFullUpdateRequest, TemplateFullResponse,
)
from utils.pdf_generator import generate_certificate_pdf
from utils.excel_batch import (
    read_fio_list_from_excel,
    assign_unique_pdf_names,
    sanitize_zip_entry_basename,
)
from utils.certificate_text import merge_legacy_variables
from reportlab.lib.utils import ImageReader

router = APIRouter(prefix="/certificates", tags=["certificates"])

# Пакетная генерация: размер файла и число строк
_MAX_BATCH_EXCEL_BYTES = 15 * 1024 * 1024  # 15 МБ
_MAX_BATCH_ROWS = 500


def _default_archive_name() -> str:
    return f"certificates_{datetime.now().strftime('%y%m%d_%H%M')}"


def _build_archive_filename(archive_name: Optional[str]) -> str:
    raw_name = (archive_name or "").strip()
    if not raw_name:
        return f"{_default_archive_name()}.zip"
    base = sanitize_zip_entry_basename(raw_name, max_len=120)
    if not base.lower().endswith(".zip"):
        base = f"{base}.zip"
    return base


def _validate_template_selector(
    template_id: Optional[int], template_name: Optional[str]
) -> None:
    has_id = template_id is not None
    name_clean = (template_name or "").strip()
    has_name = bool(name_clean)
    if has_id and has_name:
        raise HTTPException(
            status_code=400,
            detail="Укажите только template_id или только template_name, не оба сразу",
        )
    if not has_id and not has_name:
        raise HTTPException(
            status_code=400,
            detail="Нужно указать template_id или template_name",
        )


def _get_template_by_selector(
    db: Session, template_id: Optional[int], template_name: Optional[str]
) -> CertificateTemplate:
    if template_id is not None:
        template = db.query(CertificateTemplate).filter_by(id=template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return template
    name_clean = (template_name or "").strip()
    template = (
        db.query(CertificateTemplate).filter(CertificateTemplate.name == name_clean).first()
    )
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f'Шаблон с именем «{name_clean}» не найден',
        )
    return template


def _is_likely_xlsx(content: bytes) -> bool:
    # .xlsx — это ZIP; старый .xls начинается с D0 CF 11 E0
    return len(content) >= 4 and content[:2] == b"PK"


# ====================== ЗАГРУЗКА ФАЙЛОВ ======================
@router.post("/upload-background")
async def upload_background(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Только изображения")
    
    upload_dir = "static/certificates/backgrounds"
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    file_path = os.path.join(upload_dir, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {"background_url": f"/static/certificates/backgrounds/{filename}"}


@router.post("/upload-facsimile")
async def upload_facsimile(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Только изображения")
    
    upload_dir = "static/certificates/facsimiles"
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    file_path = os.path.join(upload_dir, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {"facsimile_url": f"/static/certificates/facsimiles/{filename}"}


# ====================== ШАБЛОНЫ ======================
@router.post("/templates", response_model=CertificateTemplateResponse)
def create_template(data: CertificateTemplateCreate, db: Session = Depends(get_db)):
    template = CertificateTemplate(**data.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/templates", response_model=List[CertificateTemplateResponse])
def get_templates(db: Session = Depends(get_db)):
    return db.query(CertificateTemplate).all()


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(CertificateTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    linked_count = db.query(GeneratedCertificate).filter_by(template_id=template_id).count()
    if linked_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить шаблон: по нему уже созданы сертификаты",
        )

    db.query(TemplateTextElement).filter_by(template_id=template_id).delete()
    db.query(TemplateSigner).filter_by(template_id=template_id).delete()
    db.delete(template)
    db.commit()
    return {"ok": True, "message": "Шаблон удалён"}


@router.get("/templates/{template_id}/full", response_model=TemplateFullResponse)
def get_template_full(template_id: int, db: Session = Depends(get_db)):
    """
    Загружает шаблон целиком: метаданные + все элементы + все подписанты.
    Используется для загрузки шаблона в конструктор для редактирования.
    """
    template = db.query(CertificateTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    elements = (
        db.query(TemplateTextElement)
        .filter_by(template_id=template_id)
        .order_by(TemplateTextElement.y_mm.asc())
        .all()
    )
    signers = (
        db.query(TemplateSigner)
        .filter_by(template_id=template_id)
        .order_by(TemplateSigner.order)
        .all()
    )
    return {"template": template, "elements": elements, "signers": signers}


@router.put("/templates/{template_id}/full", response_model=TemplateFullResponse)
def update_template_full(
    template_id: int,
    data: TemplateFullUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Атомарное обновление шаблона: метаданные + элементы + подписанты.
    Старые элементы и подписанты удаляются и заменяются новыми.
    Операция выполняется в одной транзакции — нет риска рассинхронизации.
    """
    template = db.query(CertificateTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    try:
        # 1. Обновляем метаданные шаблона
        template.name = data.name
        template.background_url = data.background_url
        template.signers_y_mm = data.signers_y_mm
        template.signers_block_x_mm = data.signers_block_x_mm
        template.signers_row_height_mm = data.signers_row_height_mm
        template.signers_band_width_mm = data.signers_band_width_mm
        template.signers_font_size = data.signers_font_size
        template.signers_text_color = data.signers_text_color
        template.signers_font_weight = data.signers_font_weight
        template.margin_left_mm = data.margin_left_mm
        template.margin_right_mm = data.margin_right_mm
        template.margin_top_mm = data.margin_top_mm
        template.margin_bottom_mm = data.margin_bottom_mm

        # 2. Удаляем старые элементы и подписантов
        db.query(TemplateTextElement).filter_by(template_id=template_id).delete()
        db.query(TemplateSigner).filter_by(template_id=template_id).delete()

        # 3. Создаём новые элементы
        new_elements = []
        for el in data.elements:
            obj = TemplateTextElement(
                template_id=template_id,
                text=el.text,
                is_variable=el.is_variable,
                x_mm=el.x_mm,
                y_mm=el.y_mm,
                font_size=el.font_size,
                align=el.align,
                max_width_mm=el.max_width_mm,
                max_height_mm=el.max_height_mm,
            )
            db.add(obj)
            new_elements.append(obj)

        # 4. Создаём новых подписантов (максимум 3)
        new_signers = []
        for i, s in enumerate(data.signers[:3]):
            obj = TemplateSigner(
                template_id=template_id,
                order=s.order if s.order else i + 1,
                position=s.position,
                full_name=s.full_name,
                facsimile_url=s.facsimile_url,
                offset_y_mm=s.offset_y_mm,
                facsimile_offset_x_mm=s.facsimile_offset_x_mm,
                facsimile_offset_y_mm=s.facsimile_offset_y_mm,
                facsimile_scale=s.facsimile_scale,
            )
            db.add(obj)
            new_signers.append(obj)

        db.commit()
        db.refresh(template)
        for el in new_elements:
            db.refresh(el)
        for s in new_signers:
            db.refresh(s)

        return {"template": template, "elements": new_elements, "signers": new_signers}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка обновления шаблона: {str(e)}",
        ) from e


# ====================== ЭЛЕМЕНТЫ ======================
@router.post("/templates/{template_id}/elements", response_model=TemplateTextElementResponse)
def add_text_element(template_id: int, element: TemplateTextElementCreate, db: Session = Depends(get_db)):
    if not db.query(CertificateTemplate).filter_by(id=template_id).first():
        raise HTTPException(404, "Шаблон не найден")
    
    el = TemplateTextElement(template_id=template_id, **element.model_dump())
    db.add(el)
    db.commit()
    db.refresh(el)
    return el


@router.get("/templates/{template_id}/elements", response_model=List[TemplateTextElementResponse])
def get_template_elements(template_id: int, db: Session = Depends(get_db)):
    return db.query(TemplateTextElement).filter_by(template_id=template_id).all()


# ====================== ПОДПИСАНТЫ ======================
@router.post("/templates/{template_id}/signers", response_model=TemplateSignerResponse)
def add_signer(template_id: int, signer: TemplateSignerCreate, db: Session = Depends(get_db)):
    if not db.query(CertificateTemplate).filter_by(id=template_id).first():
        raise HTTPException(404, "Шаблон не найден")
    
    signer_obj = TemplateSigner(template_id=template_id, **signer.model_dump())
    db.add(signer_obj)
    db.commit()
    db.refresh(signer_obj)
    return signer_obj


@router.get("/templates/{template_id}/signers", response_model=List[TemplateSignerResponse])
def get_signers(template_id: int, db: Session = Depends(get_db)):
    return db.query(TemplateSigner).filter_by(template_id=template_id).order_by(TemplateSigner.order).all()


# ====================== ГЕНЕРАЦИЯ ======================
@router.post("/batch")
async def batch_generate_certificates(
    file: UploadFile = File(..., description="Excel .xlsx со столбцом «ФИО»"),
    template_id: Optional[int] = Form(None),
    template_name: Optional[str] = Form(None),
    event_name: str = Form(""),
    archive_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Пакетная генерация PDF по списку ФИО из Excel. Ответ — ZIP со всеми грамотами.
    """
    _validate_template_selector(template_id, template_name)

    if file.filename and not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail="Ожидается файл Excel в формате .xlsx (или .xlsm)",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > _MAX_BATCH_EXCEL_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой (максимум {_MAX_BATCH_EXCEL_BYTES // (1024 * 1024)} МБ)",
        )

    if not _is_likely_xlsx(raw):
        raise HTTPException(
            status_code=400,
            detail="Файл не похож на корректный .xlsx. Сохраните таблицу в формате Excel Workbook (.xlsx).",
        )

    event_name = (event_name or "").strip()
    if len(event_name) > 300:
        raise HTTPException(
            status_code=400,
            detail="Название мероприятия не длиннее 300 символов",
        )

    try:
        fio_list, _column_used = read_fio_list_from_excel(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not fio_list:
        raise HTTPException(
            status_code=400,
            detail="В столбце ФИО нет ни одной заполненной строки",
        )

    if len(fio_list) > _MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много строк: {len(fio_list)}. Максимум {_MAX_BATCH_ROWS} за один запрос.",
        )

    template = _get_template_by_selector(db, template_id, template_name)

    elements = (
        db.query(TemplateTextElement)
        .filter_by(template_id=template.id)
        .order_by(TemplateTextElement.y_mm.asc())
        .all()
    )
    if not elements:
        raise HTTPException(
            status_code=400,
            detail="У выбранного шаблона нет текстовых элементов",
        )

    signers = (
        db.query(TemplateSigner)
        .filter_by(template_id=template.id)
        .order_by(TemplateSigner.order)
        .all()
    )
    signers_arg = signers if signers else None
    bg_reader = None
    bg_url = getattr(template, "background_url", None)
    if bg_url:
        resolved_bg_path = bg_url.lstrip("/")
        if os.path.exists(resolved_bg_path):
            try:
                bg_reader = ImageReader(resolved_bg_path)
            except Exception:
                bg_reader = None

    font_name = None
    try:
        from utils.pdf_generator import _canvas_font_name

        font_name = _canvas_font_name()
    except Exception:
        font_name = None

    pdf_names = assign_unique_pdf_names(fio_list)
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as zf:
        for fio, entry_name in zip(fio_list, pdf_names):
            try:
                variables = merge_legacy_variables({}, fio, event_name)
                pdf_buffer = generate_certificate_pdf(
                    template=template,
                    elements=elements,
                    variables=variables,
                    signers=signers_arg,
                    font_name=font_name,
                    bg_reader=bg_reader,
                )
                zf.writestr(entry_name, pdf_buffer.getvalue())
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Ошибка генерации PDF для «{fio}»: {e}",
                ) from e

    payload = zip_buffer.getvalue()
    archive_filename = _build_archive_filename(archive_name)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_filename}"'
        },
    )


@router.post("/manual", response_model=GeneratedCertificateResponse)
def manual_generate_certificate(
    request: ManualCertificateRequest,
    db: Session = Depends(get_db),
):
    """
    Ручная выдача сертификата: все переменные задаются вручную через JSON.

    Собирает итоговый словарь variables из:
    - fio → {ФИО}, {fio}
    - event_name → {Мероприятие}, {мероприятие}
    - date → {Дата}, {дата}
    - extra_variables → произвольные {Ключ: Значение}

    Возвращает GeneratedCertificateResponse с file_url для скачивания.
    """
    try:
        template = _get_template_by_selector(db, request.template_id, request.template_name)

        elements = (
            db.query(TemplateTextElement)
            .filter_by(template_id=template.id)
            .order_by(TemplateTextElement.y_mm.asc())
            .all()
        )
        if not elements:
            raise HTTPException(status_code=400, detail="У шаблона нет текстовых элементов")

        # Собираем переменные: extra_variables → быстрые поля (быстрые поля имеют приоритет)
        variables: dict = dict(request.extra_variables)

        # Быстрые поля перезаписывают extra_variables при совпадении ключей
        fio = request.fio.strip()
        variables["ФИО"] = fio
        variables["фио"] = fio
        variables["fio"] = fio
        variables["FIO"] = fio

        event = request.event_name.strip()
        variables["Мероприятие"] = event
        variables["мероприятие"] = event
        variables["event"] = event
        variables["Event"] = event

        if request.date and request.date.strip():
            date_val = request.date.strip()
            variables["Дата"] = date_val
            variables["дата"] = date_val
            variables["date"] = date_val

        signers = (
            db.query(TemplateSigner)
            .filter_by(template_id=template.id)
            .order_by(TemplateSigner.order)
            .all()
        )
        signers_arg = signers if signers else None

        pdf_buffer = generate_certificate_pdf(
            template=template,
            elements=elements,
            variables=variables,
            signers=signers_arg,
        )

        output_dir = "static/certificates/generated"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(output_dir, filename)

        with open(file_path, "wb") as f:
            f.write(pdf_buffer.getvalue())

        file_url = f"/static/certificates/generated/{filename}"

        cert = GeneratedCertificate(
            template_id=template.id,
            recipient_id=None,
            event_name=event,
            file_url=file_url,
            generated_by_id=1,
        )
        db.add(cert)
        db.commit()
        db.refresh(cert)

        return cert

    except HTTPException:
        raise
    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("Ошибка ручной генерации сертификата")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}") from e


@router.post("/generate", response_model=GeneratedCertificateResponse)
def generate_certificate(
    request: CertificateGenerateRequest,
    db: Session = Depends(get_db),
):
    """
    Одиночная генерация PDF: variables подставляются в плейсхолдеры {Ключ} в тексте шаблона.
    Поле event_name (если передано) добавляет variables['Мероприятие'] для обратной совместимости.
    """
    try:
        template = _get_template_by_selector(db, request.template_id, request.template_name)

        elements = (
            db.query(TemplateTextElement)
            .filter_by(template_id=template.id)
            .order_by(TemplateTextElement.y_mm.asc())
            .all()
        )
        if not elements:
            raise HTTPException(status_code=400, detail="У шаблона нет текстовых элементов")

        variables = dict(request.variables)
        if request.event_name and str(request.event_name).strip():
            variables.setdefault("Мероприятие", request.event_name.strip())

        signers = (
            db.query(TemplateSigner)
            .filter_by(template_id=template.id)
            .order_by(TemplateSigner.order)
            .all()
        )
        signers_arg = signers if signers else None

        pdf_buffer = generate_certificate_pdf(
            template=template,
            elements=elements,
            variables=variables,
            signers=signers_arg,
        )

        output_dir = "static/certificates/generated"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"cert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(output_dir, filename)

        with open(file_path, "wb") as f:
            f.write(pdf_buffer.getvalue())

        file_url = f"/static/certificates/generated/{filename}"

        event_snapshot = variables.get("Мероприятие") or request.event_name

        cert = GeneratedCertificate(
            template_id=template.id,
            recipient_id=request.recipient_id,
            event_name=event_snapshot,
            file_url=file_url,
            generated_by_id=1,
        )

        db.add(cert)
        db.commit()
        db.refresh(cert)

        return cert

    except HTTPException:
        raise
    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("Ошибка одиночной генерации сертификата")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}") from e