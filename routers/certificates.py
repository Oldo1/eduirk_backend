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
)
from utils.pdf_generator import generate_certificate_pdf
from utils.excel_batch import read_fio_list_from_excel, assign_unique_pdf_names
from utils.certificate_text import merge_legacy_variables

router = APIRouter(prefix="/certificates", tags=["certificates"])

# Пакетная генерация: размер файла и число строк
_MAX_BATCH_EXCEL_BYTES = 15 * 1024 * 1024  # 15 МБ
_MAX_BATCH_ROWS = 500


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
    template = CertificateTemplate(**data.dict())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/templates", response_model=List[CertificateTemplateResponse])
def get_templates(db: Session = Depends(get_db)):
    return db.query(CertificateTemplate).all()


# ====================== ЭЛЕМЕНТЫ ======================
@router.post("/templates/{template_id}/elements", response_model=TemplateTextElementResponse)
def add_text_element(template_id: int, element: TemplateTextElementCreate, db: Session = Depends(get_db)):
    if not db.query(CertificateTemplate).filter_by(id=template_id).first():
        raise HTTPException(404, "Шаблон не найден")
    
    el = TemplateTextElement(template_id=template_id, **element.dict())
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
    
    signer_obj = TemplateSigner(template_id=template_id, **signer.dict())
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

    pdf_names = assign_unique_pdf_names(fio_list)
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fio, entry_name in zip(fio_list, pdf_names):
            try:
                variables = merge_legacy_variables({}, fio, event_name)
                pdf_buffer = generate_certificate_pdf(
                    template=template,
                    elements=elements,
                    variables=variables,
                    signers=signers_arg,
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="certificates_{stamp}.zip"'
        },
    )


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
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}") from e