from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from main import register
from models import User
from schemas import UserCreate


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def test_public_registration_schema_does_not_expose_username():
    assert "username" not in UserCreate.model_json_schema()["properties"]


def test_public_registration_does_not_store_username():
    db = _session()
    try:
        first = register(
            UserCreate(email="person@example.com", password="secret123"),
            db=db,
        )
        second = register(
            UserCreate(email="person@example.org", password="secret123"),
            db=db,
        )

        assert "username" not in first.model_dump()
        assert "username" not in second.model_dump()
        assert "username" not in User.__table__.columns
        assert first.created_at is not None
        assert second.created_at is not None
        assert User.__table__.columns["created_at"].nullable is False
    finally:
        db.close()
