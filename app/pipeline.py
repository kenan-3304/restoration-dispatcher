import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models import StructuredData
from app.db import insert_call, update_call
from app.address import validate_address
from app.dispatch import (
    build_dispatch_sms,
    build_caller_confirmation_sms,
    resolve_callback_number,
    send_sms,
)

logger = logging.getLogger(__name__)


async def process_call(
    vapi_call_id: str,
    data: StructuredData,
    raw_payload: str,
    customer: dict,
    caller_id: Optional[str] = None,
):
    """Main pipeline: insert, validate address, dispatch or confirm."""
    try:
        # 1. Insert row (idempotency check)
        row_id, was_duplicate = await insert_call(
            vapi_call_id, data, raw_payload, customer_id=customer["id"]
        )
        if was_duplicate:
            logger.info("Duplicate call %s, skipping pipeline", vapi_call_id)
            return

        logger.info(
            "Processing call %s (row %s), customer=%s, outcome=%s",
            vapi_call_id, row_id, customer["name"], data.call_outcome,
        )

        # 2. Check call_outcome — only dispatch for emergency_dispatch
        if data.call_outcome in ("spam", "non_emergency", "referral"):
            logger.info("Call %s outcome is %s, no dispatch needed", vapi_call_id, data.call_outcome)
            return

        # 3. Validate property address
        if not data.property_address:
            await update_call(
                vapi_call_id,
                address_validation_status="skipped",
                error="No property address provided",
            )
            await _dispatch_no_address(vapi_call_id, data, customer, caller_id)
            return

        tier, formatted_address, distance = await validate_address(
            data.property_address,
            service_center_lat=customer["service_center_lat"],
            service_center_lng=customer["service_center_lng"],
            service_radius_mi=customer["service_radius_mi"],
        )

        # 4. Update row with validation result
        await update_call(
            vapi_call_id,
            address_validation_status=tier,
            validated_address=formatted_address,
            distance_from_service_center_mi=distance,
        )

        # 5. Act based on tier
        if tier == "in_radius":
            await _send_dispatch(vapi_call_id, data, customer, distance=distance, borderline=False)
        elif tier == "borderline":
            await _send_dispatch(vapi_call_id, data, customer, distance=distance, borderline=True)
        elif tier in ("out_of_radius", "invalid"):
            await _send_caller_confirmation(vapi_call_id, data, customer, caller_id)
        elif tier == "skipped":
            await _send_dispatch(vapi_call_id, data, customer, distance=None, borderline=False)

    except Exception as e:
        logger.exception("Error processing call %s: %s", vapi_call_id, e)
        try:
            await update_call(vapi_call_id, error=str(e))
        except Exception:
            logger.exception("Failed to record error for call %s", vapi_call_id)


async def _send_dispatch(
    vapi_call_id: str,
    data: StructuredData,
    customer: dict,
    distance: Optional[float],
    borderline: bool,
):
    """Send dispatch SMS to on-call tech."""
    body = build_dispatch_sms(data, distance=distance, borderline=borderline)
    to = customer["on_call_phone"]

    if not to:
        logger.error("No on_call_phone for customer %s, cannot dispatch", customer["id"])
        await update_call(vapi_call_id, error="No on-call phone configured")
        return

    try:
        sid = await send_sms(to, body)
        now = datetime.now(timezone.utc).isoformat()
        await update_call(
            vapi_call_id,
            dispatch_sent=True,
            dispatch_sent_at=now,
            dispatch_sid=sid,
        )
        logger.info("Dispatch SMS sent for call %s, SID=%s", vapi_call_id, sid)
    except Exception as e:
        logger.error("Dispatch SMS failed for call %s: %s", vapi_call_id, e)
        await update_call(vapi_call_id, error=f"Dispatch SMS failed: {e}")


async def _send_caller_confirmation(
    vapi_call_id: str,
    data: StructuredData,
    customer: dict,
    caller_id: Optional[str] = None,
):
    """Send address confirmation SMS to the caller."""
    callback = resolve_callback_number(data, caller_id)
    if not callback:
        logger.warning("No callback number for call %s, cannot send confirmation", vapi_call_id)
        await update_call(vapi_call_id, error="No callback number for caller confirmation")
        return

    body = build_caller_confirmation_sms(data.caller_name, customer_name=customer["name"])

    try:
        sid = await send_sms(callback, body)
        now = datetime.now(timezone.utc).isoformat()
        await update_call(
            vapi_call_id,
            caller_confirmation_sent=True,
            caller_confirmation_sent_at=now,
            caller_confirmation_sid=sid,
        )
        logger.info("Caller confirmation SMS sent for call %s to %s, SID=%s", vapi_call_id, callback, sid)
    except Exception as e:
        logger.error("Caller confirmation SMS failed for call %s: %s", vapi_call_id, e)
        await update_call(vapi_call_id, error=f"Caller confirmation SMS failed: {e}")


async def _dispatch_no_address(
    vapi_call_id: str,
    data: StructuredData,
    customer: dict,
    caller_id: Optional[str] = None,
):
    """Dispatch even without an address — tech will need to call back."""
    await _send_dispatch(vapi_call_id, data, customer, distance=None, borderline=False)
