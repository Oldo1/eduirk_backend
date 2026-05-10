from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import Appointment
from schemas import AppointmentCreate, AppointmentResponse

router = APIRouter(prefix="/appointments", tags=["appointments"])


# ====================== СОЗДАНИЕ ЗАПИСИ ======================
@router.post("/", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
def create_appointment(data: AppointmentCreate, db: Session = Depends(get_db)):
    """Создать новую запись на приём"""
    appointment = Appointment(
        full_name=data.full_name,
        appointment_date=data.appointment_date,
        appointment_time=data.appointment_time,
        comment=data.comment,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


# ====================== ПОЛУЧЕНИЕ ВСЕХ ЗАПИСЕЙ ======================
@router.get("/", response_model=List[AppointmentResponse])
def get_appointments(db: Session = Depends(get_db)):
    """Получить список всех записей на приём"""
    return db.query(Appointment).order_by(
        Appointment.appointment_date, Appointment.appointment_time
    ).all()


# ====================== УДАЛЕНИЕ ЗАПИСИ ======================
@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    """Удалить запись на приём"""
    appt = db.query(Appointment).filter_by(id=appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(appt)
    db.commit()
    return None
