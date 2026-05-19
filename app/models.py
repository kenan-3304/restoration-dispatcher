from pydantic import BaseModel
from typing import Optional, Literal


class StructuredData(BaseModel):
    caller_name: Optional[str] = None
    callback_number: Optional[str] = None
    property_address: Optional[str] = None
    loss_type: Optional[str] = None
    is_active: Optional[bool] = None
    source_of_loss: Optional[str] = None
    call_outcome: Literal["emergency_dispatch", "non_emergency", "referral", "spam"]
    call_summary: Optional[str] = None
    water_category: Optional[str] = None
    rooms_affected: Optional[str] = None
    insurance_carrier: Optional[str] = None
    life_safety_concern: Optional[bool] = None


class VapiWebhook(BaseModel):
    message: dict


class TestDispatchRequest(BaseModel):
    call_id: str
    customer_id: Optional[int] = None
    caller_name: Optional[str] = None
    callback_number: Optional[str] = None
    property_address: Optional[str] = None
    loss_type: Optional[str] = None
    is_active: Optional[bool] = None
    source_of_loss: Optional[str] = None
    call_outcome: Literal["emergency_dispatch", "non_emergency", "referral", "spam"] = "emergency_dispatch"
    call_summary: Optional[str] = None
    water_category: Optional[str] = None
    rooms_affected: Optional[str] = None
    insurance_carrier: Optional[str] = None
    life_safety_concern: Optional[bool] = None
