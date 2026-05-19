import os
from dotenv import load_dotenv

load_dotenv()


def _str(key: str, default: str = "") -> str:
    val = os.getenv(key, default).strip()
    return val if val and not val.startswith("#") else default


def _float(key: str, default: float = 0.0) -> float:
    raw = _str(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _int(key: str, default: int = 0) -> int:
    raw = _str(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


class Settings:
    VAPI_WEBHOOK_SECRET: str = _str("VAPI_WEBHOOK_SECRET")

    GOOGLE_ADDRESS_VALIDATION_API_KEY: str = _str("GOOGLE_ADDRESS_VALIDATION_API_KEY")

    TWILIO_SID: str = _str("TWILIO_SID")
    TWILIO_AUTH: str = _str("TWILIO_AUTH")
    TWILIO_FROM_NUMBER: str = _str("TWILIO_FROM_NUMBER")

    OPENAI_API_KEY: str = _str("OPENAI_API_KEY")

    CUSTOMER_NAME: str = _str("CUSTOMER_NAME")
    CUSTOMER_ON_CALL_PHONE: str = _str("CUSTOMER_ON_CALL_PHONE")
    CUSTOMER_SERVICE_CENTER_LAT: float = _float("CUSTOMER_SERVICE_CENTER_LAT")
    CUSTOMER_SERVICE_CENTER_LNG: float = _float("CUSTOMER_SERVICE_CENTER_LNG")
    CUSTOMER_SERVICE_RADIUS_MI: int = _int("CUSTOMER_SERVICE_RADIUS_MI", 30)
    CUSTOMER_OWNER_PHONE: str = _str("CUSTOMER_OWNER_PHONE")
    CUSTOMER_VAPI_ASSISTANT_ID: str = _str("CUSTOMER_VAPI_ASSISTANT_ID")

    DATABASE_PATH: str = _str("DATABASE_PATH", "./data/dispatcher.db")


settings = Settings()
