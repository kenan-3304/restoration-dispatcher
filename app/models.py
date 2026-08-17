from pydantic import BaseModel
from typing import Optional, Literal


class StructuredData(BaseModel):
    call_outcome: Literal[
        "emergency_dispatch", "non_emergency", "life_safety_redirect", "no_response", "spam"
    ]
    loss_type: Literal["water", "fire", "smoke", "mold", "other"]
    caller_name: str
    address_full: str
    call_summary: str
    is_active: Optional[Literal["yes", "no", "unknown"]] = None
    source_detail: Optional[str] = None
    alternate_callback_number: Optional[str] = None


class AdminCreateCustomerRequest(BaseModel):
    name: str
    on_call_phone: str
    owner_phone: Optional[str] = None
    lat: float
    lng: float
    radius: int = 30
    assistant_id: str
    crm_type: Optional[str] = None
    crm_config: Optional[str] = None


class VapiWebhook(BaseModel):
    message: dict


class TestDispatchRequest(BaseModel):
    call_id: str
    customer_id: int
    call_outcome: Literal[
        "emergency_dispatch", "non_emergency", "life_safety_redirect", "no_response", "spam"
    ] = "emergency_dispatch"
    loss_type: Literal["water", "fire", "smoke", "mold", "other"] = "water"
    caller_name: str = ""
    address_full: str = ""
    call_summary: str = ""
    is_active: Optional[Literal["yes", "no", "unknown"]] = None
    source_detail: Optional[str] = None
    alternate_callback_number: Optional[str] = None
    caller_phone: Optional[str] = None
