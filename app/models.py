from pydantic import BaseModel, field_validator
from typing import Optional, Literal

_IS_ACTIVE_VALUES = {"yes", "no", "unknown"}


class StructuredData(BaseModel):
    # No defaults here would mean a call where Vapi's extractor comes back empty
    # (e.g. dead-air / immediate-hangup calls with no transcript to extract from)
    # 400s the webhook and the call is silently dropped instead of logged as
    # no_response. Defaults mirror TestDispatchRequest below.
    call_outcome: Literal[
        "emergency_dispatch", "non_emergency", "life_safety_redirect", "no_response", "spam"
    ] = "no_response"
    loss_type: Literal["water", "fire", "smoke", "mold", "other"] = "other"
    caller_name: str = ""
    address_full: str = ""
    call_summary: str = ""
    is_active: Optional[Literal["yes", "no", "unknown"]] = None
    source_detail: Optional[str] = None
    alternate_callback_number: Optional[str] = None

    @field_validator("is_active", mode="before")
    @classmethod
    def _coerce_is_active(cls, v):
        # Optional enum fields can arrive as "" or an unrecognized value when Vapi's
        # extractor finds nothing to fill in — treat that as "not provided" rather
        # than failing validation and dropping the whole call.
        if v not in _IS_ACTIVE_VALUES:
            return None
        return v

    @field_validator("source_detail", "alternate_callback_number", mode="before")
    @classmethod
    def _blank_optional_str_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


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
