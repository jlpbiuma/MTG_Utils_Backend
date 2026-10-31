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
    # Comma-separated emails allowed to act as any user (X-User-Id) with their
    # own session. Empty by default: set it only in local development.
    ADMIN_USER_EMAILS: str = os.getenv("ADMIN_USER_EMAILS", "")
    # Local development only: accept X-User-Id / a bare user UUID without a
    # session and fall back to the demo user. Never enable it in production.
    ALLOW_INSECURE_DEV_AUTH: bool = os.getenv("ALLOW_INSECURE_DEV_AUTH", "false").lower() in ("1", "true", "yes")

    @property
    def admin_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.ADMIN_USER_EMAILS.split(",") if e.strip()}

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
