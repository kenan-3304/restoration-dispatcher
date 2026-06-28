import os
from dotenv import load_dotenv

load_dotenv()


def _str(key: str, default: str = "") -> str:
    val = os.getenv(key, default).strip()
    return val if val and not val.startswith("#") else default


class Settings:
    VAPI_WEBHOOK_SECRET: str = _str("VAPI_WEBHOOK_SECRET")

    GOOGLE_ADDRESS_VALIDATION_API_KEY: str = _str("GOOGLE_ADDRESS_VALIDATION_API_KEY")

    TWILIO_SID: str = _str("TWILIO_SID")
    TWILIO_AUTH: str = _str("TWILIO_AUTH")
    TWILIO_FROM_NUMBER: str = _str("TWILIO_FROM_NUMBER")

    OPENAI_API_KEY: str = _str("OPENAI_API_KEY")

    DATABASE_URL: str = _str("DATABASE_URL")
    ADMIN_API_KEY: str = _str("ADMIN_API_KEY")


settings = Settings()
