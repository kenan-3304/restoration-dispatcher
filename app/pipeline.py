import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models import StructuredData
from app.db import insert_call, update_call
from app.address import validate_address
from app.crm import push_to_crm
from app.dispatch import (
    build_dispatch_sms,
    build_caller_confirmation_sms,
    build_address_confirmed_sms,
    build_outside_radius_sms,
    build_address_unverifiable_sms,
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
        callback = resolve_callback_number(data, caller_id)

        # 1. Insert row (idempotency check)
        row_id, was_duplicate = await insert_call(
            vapi_call_id, data, raw_payload, customer_id=customer["id"], callback_number=callback
        )
        if was_duplicate:
            logger.info("Duplicate call %s, skipping pipeline", vapi_call_id)
            return

        logger.info(
            "Processing call %s (row %s), customer=%s, outcome=%s",
            vapi_call_id, row_id, customer["name"], data.call_outcome,
        )

        # 2. Only emergency_dispatch triggers dispatch; everything else is logged only
        if data.call_outcome != "emergency_dispatch":
            logger.info(
                "Call %s outcome=%s, no dispatch needed", vapi_call_id, data.call_outcome
            )
            return

        # 3. Validate property address
        if not data.address_full:
            await update_call(
                vapi_call_id,
                address_validation_status="skipped",
                error="No property address provided",
            )
            await _dispatch_no_address(vapi_call_id, data, customer, callback)
            return

        tier, formatted_address, distance = await validate_address(
            data.address_full,
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
            await _send_dispatch(vapi_call_id, data, customer, callback, distance=distance, borderline=False, formatted_address=formatted_address)
            await _push_crm(vapi_call_id, data, customer, callback)
        elif tier == "borderline":
            await _send_dispatch(vapi_call_id, data, customer, callback, distance=distance, borderline=True, formatted_address=formatted_address)
            await _push_crm(vapi_call_id, data, customer, callback)
        elif tier in ("out_of_radius", "invalid"):
            await _send_caller_confirmation(vapi_call_id, data, customer, callback)
        elif tier == "skipped":
            await _send_dispatch(vapi_call_id, data, customer, callback, distance=None, borderline=False)
            await _push_crm(vapi_call_id, data, customer, callback)

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
    callback: Optional[str],
    distance: Optional[float],
    borderline: bool,
    formatted_address: Optional[str] = None,
):
    """Send dispatch SMS to on-call tech."""
    body = build_dispatch_sms(data, callback=callback, distance=distance, borderline=borderline, formatted_address=formatted_address)
    to = customer["on_call_phone"]

    if not to:
        logger.error("No on_call_phone for customer %s, cannot dispatch", customer["id"])
        await update_call(vapi_call_id, error="No on-call phone configured")
        return

    try:
        sid = await send_sms(to, body)
        now = datetime.now(timezone.utc)
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
    callback: Optional[str],
):
    """Send address confirmation SMS to the caller."""
    if not callback:
        logger.warning("No callback number for call %s, cannot send confirmation", vapi_call_id)
        await update_call(vapi_call_id, error="No callback number for caller confirmation")
        return

    body = build_caller_confirmation_sms(data.caller_name, customer_name=customer["name"])

    try:
        sid = await send_sms(callback, body)
        now = datetime.now(timezone.utc)
        await update_call(
            vapi_call_id,
            caller_confirmation_sent=True,
            caller_confirmation_sent_at=now,
            caller_confirmation_sid=sid,
            pending_address_confirmation=True,
        )
        logger.info("Caller confirmation SMS sent for call %s to %s, SID=%s", vapi_call_id, callback, sid)
    except Exception as e:
        logger.error("Caller confirmation SMS failed for call %s: %s", vapi_call_id, e)
        await update_call(vapi_call_id, error=f"Caller confirmation SMS failed: {e}")


async def _dispatch_no_address(
    vapi_call_id: str,
    data: StructuredData,
    customer: dict,
    callback: Optional[str],
):
    """Dispatch even without an address — tech will need to call back."""
    await _send_dispatch(vapi_call_id, data, customer, callback, distance=None, borderline=False)
    await _push_crm(vapi_call_id, data, customer, callback)


async def _push_crm(vapi_call_id: str, data: StructuredData, customer: dict, callback: Optional[str]):
    """Push call to CRM if the customer has one configured. Errors are logged, not raised."""
    if not customer.get("crm_type"):
        return
    call_dict = {"vapi_call_id": vapi_call_id, **data.model_dump(), "callback_number": callback}
    crm_job_id = await push_to_crm(
        customer["crm_type"],
        customer.get("crm_config"),
        call_dict,
        customer,
    )
    if crm_job_id:
        await update_call(vapi_call_id, crm_job_id=crm_job_id)
        logger.info("CRM job %s created for call %s", crm_job_id, vapi_call_id)


async def process_address_reply(call: dict, customer: dict, address: str):
    """Process a caller's SMS reply containing their corrected address.

    Re-validates the address and dispatches if it's in range, otherwise
    sends the caller a clear closing message. Either way the pending flag is cleared.
    """
    vapi_call_id = call["vapi_call_id"]
    callback = call.get("callback_number")
    caller_name = call.get("caller_name")

    # Close the pending window immediately so duplicate replies don't double-dispatch
    await update_call(vapi_call_id, pending_address_confirmation=False, address_full=address)

    try:
        tier, formatted_address, distance = await validate_address(
            address,
            service_center_lat=customer["service_center_lat"],
            service_center_lng=customer["service_center_lng"],
            service_radius_mi=customer["service_radius_mi"],
        )

        await update_call(
            vapi_call_id,
            address_validation_status=tier,
            validated_address=formatted_address,
            distance_from_service_center_mi=distance,
        )

        if tier in ("in_radius", "borderline"):
            data = StructuredData(
                call_outcome=call.get("call_outcome") or "emergency_dispatch",
                loss_type=call.get("loss_type") or "other",
                caller_name=caller_name or "",
                address_full=formatted_address or address,
                call_summary=call.get("call_summary") or "",
                is_active=call.get("is_active"),
                source_detail=call.get("source_detail"),
            )
            await _send_dispatch(vapi_call_id, data, customer, callback, distance=distance, borderline=(tier == "borderline"))
            await _push_crm(vapi_call_id, data, customer, callback)
            if callback:
                msg = build_address_confirmed_sms(caller_name, customer["name"], formatted_address or address)
                await send_sms(callback, msg)

        elif tier == "out_of_radius":
            logger.info("Call %s still out of radius after address reply, no dispatch", vapi_call_id)
            if callback:
                await send_sms(callback, build_outside_radius_sms(caller_name, customer["name"]))

        else:  # invalid
            logger.info("Call %s address still unverifiable after reply, no dispatch", vapi_call_id)
            if callback:
                await send_sms(callback, build_address_unverifiable_sms(caller_name, customer["name"]))

    except Exception as e:
        logger.exception("Error processing address reply for call %s: %s", vapi_call_id, e)
        await update_call(vapi_call_id, error=f"Address reply processing failed: {e}")
