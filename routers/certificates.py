from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
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

router = APIRouter(prefix="/certificates", tags=["certificates"])


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
@router.post("/generate", response_model=GeneratedCertificateResponse)
def generate_certificate(
    request: CertificateGenerateRequest,
    db: Session = Depends(get_db)
):
    try:
        # 1. Получаем шаблон
        template = db.query(CertificateTemplate).filter_by(id=request.template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Шаблон не найден")

        # 2. Получаем элементы
        elements = db.query(TemplateTextElement)\
            .filter_by(template_id=request.template_id)\
            .order_by(TemplateTextElement.y_mm.asc())\
            .all()

        if not elements:
            raise HTTPException(status_code=400, detail="У шаблона нет текстовых элементов")

        # 3. Генерируем PDF
        fio = "Иванов Иван Иванович"
        pdf_buffer = generate_certificate_pdf(
            template=template,
            elements=elements,
            fio=fio,
            event_name=request.event_name
        )

        # 4. Сохраняем PDF
        output_dir = "static/certificates/generated"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"cert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(output_dir, filename)

        with open(file_path, "wb") as f:
            f.write(pdf_buffer.getvalue())

        file_url = f"/static/certificates/generated/{filename}"

        # 5. Сохраняем запись в базу БЕЗ создания пользователя каждый раз
        cert = GeneratedCertificate(
            template_id=request.template_id,
            recipient_id=None,
            event_name=request.event_name,
            file_url=file_url,
            generated_by_id=1                     # просто используем id=1
        )

        db.add(cert)
        db.commit()
        db.refresh(cert)

        print(f"✅ Сертификат успешно создан: {filename}")
        return cert

    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        print("=== ОШИБКА ПРИ ГЕНЕРАЦИИ СЕРТИФИКАТА ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}")