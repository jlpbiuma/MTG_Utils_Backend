from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import os

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mtg_utils?schema=public")
    CARDTRADER_API_TOKEN: str = os.getenv("CARDTRADER_API_TOKEN", "")
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ]
    DEMO_USER_ID: str = "00000000-0000-0000-0000-000000000000"
    WHATSAPP_DB_PATH: str = os.getenv("WHATSAPP_DB_PATH", "/app/whatsapp_data/cards.db")
    SLOW_REQUEST_MS: float = float(os.getenv("SLOW_REQUEST_MS", "200"))
    OCR_API_URL: str = os.getenv("OCR_API_URL", "http://host.docker.internal:8020")
    OCR_API_TOKEN: str = os.getenv("OCR_API_TOKEN", "")
    OCR_API_TIMEOUT: float = float(os.getenv("OCR_API_TIMEOUT", "15"))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
