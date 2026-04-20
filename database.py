from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv
import os

# Загружаем переменные из .env файла
load_dotenv()

# === ИЗМЕНИ ЭТИ ДАННЫЕ НА СВОИ ===
DB_USER = os.getenv("DB_USER", "postgres")           # твой логин
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")  # твой пароль
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "eduirk_db")          # название твоей базы

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    DATABASE_URL,
    echo=False,           # Поставь True, если хочешь видеть SQL-запросы в консоли
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()