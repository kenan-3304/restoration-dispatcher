import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    VAPI_WEBHOOK_SECRET: str = os.getenv("VAPI_WEBHOOK_SECRET", "")

    GOOGLE_ADDRESS_VALIDATION_API_KEY: str = os.getenv("GOOGLE_ADDRESS_VALIDATION_API_KEY", "")

    TWILIO_SID: str = os.getenv("TWILIO_SID", "")
    TWILIO_AUTH: str = os.getenv("TWILIO_AUTH", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    CUSTOMER_NAME: str = os.getenv("CUSTOMER_NAME", "")
    CUSTOMER_ON_CALL_PHONE: str = os.getenv("CUSTOMER_ON_CALL_PHONE", "")
    CUSTOMER_SERVICE_CENTER_LAT: float = float(os.getenv("CUSTOMER_SERVICE_CENTER_LAT", "0"))
    CUSTOMER_SERVICE_CENTER_LNG: float = float(os.getenv("CUSTOMER_SERVICE_CENTER_LNG", "0"))
    CUSTOMER_SERVICE_RADIUS_MI: int = int(os.getenv("CUSTOMER_SERVICE_RADIUS_MI", "30"))
    CUSTOMER_OWNER_PHONE: str = os.getenv("CUSTOMER_OWNER_PHONE", "")

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/dispatcher.db")


settings = Settings()
