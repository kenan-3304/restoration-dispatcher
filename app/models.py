from pydantic import BaseModel
from typing import Optional, Literal


class StructuredData(BaseModel):
    call_outcome: Literal["emergency_dispatch", "non_emergency_callback"]
    life_safety_concern: Optional[bool] = None
    caller_name: Optional[str] = None
    callback_number: Optional[str] = None
    address_full: Optional[str] = None
    address_confirmed: Optional[bool] = None
    loss_type: Optional[Literal["water", "fire", "smoke", "mold", "other"]] = None
    is_active: Optional[bool] = None
    source_detail: Optional[str] = None
    water_clean_or_dirty: Optional[Literal["clean", "dirty", "unknown", "not_applicable"]] = None
    access_notes: Optional[str] = None
    insurance_carrier: Optional[str] = None
    callback_reason: Optional[str] = None
    call_summary: Optional[str] = None


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
    call_outcome: Literal["emergency_dispatch", "non_emergency_callback"] = "emergency_dispatch"
    life_safety_concern: Optional[bool] = None
    caller_name: Optional[str] = None
    callback_number: Optional[str] = None
    address_full: Optional[str] = None
    address_confirmed: Optional[bool] = None
    loss_type: Optional[str] = None
    is_active: Optional[bool] = None
    source_detail: Optional[str] = None
    water_clean_or_dirty: Optional[Literal["clean", "dirty", "unknown", "not_applicable"]] = None
    access_notes: Optional[str] = None
    insurance_carrier: Optional[str] = None
    callback_reason: Optional[str] = None
    call_summary: Optional[str] = None
