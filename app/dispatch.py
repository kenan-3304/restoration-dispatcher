import logging
import re
from typing import Optional

from twilio.rest import Client as TwilioClient

from app.config import settings
from app.models import StructuredData

logger = logging.getLogger(__name__)

LOSS_TYPE_EMOJI = {
    "water": "\U0001f4a7",   # droplet
    "fire": "\U0001f525",
    "smoke": "\U0001f6AC",
    "mold": "\U0001f9a0",
}
DEFAULT_EMOJI = "\u26a0\ufe0f"  # warning sign


def _sanitize_phone(number: Optional[str]) -> Optional[str]:
    """Extract a usable phone number. Prefer extracted callback_number if it has 7+ digits."""
    if not number:
        return None
    digits = re.sub(r"\D", "", number)
    if len(digits) >= 7:
        # Ensure E.164: if 10 digits assume US
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        elif number.startswith("+"):
            return number
        else:
            return f"+{digits}"
    return None


def resolve_callback_number(data: StructuredData, caller_id: Optional[str] = None) -> Optional[str]:
    """Pick the best callback number: prefer extracted callback_number with 7+ digits, fall back to caller_id."""
    extracted = _sanitize_phone(data.callback_number)
    if extracted:
        return extracted
    return _sanitize_phone(caller_id)


def build_dispatch_sms(data: StructuredData, distance: Optional[float] = None, borderline: bool = False) -> str:
    """Build the dispatch SMS body for the on-call tech."""
    emoji = LOSS_TYPE_EMOJI.get((data.loss_type or "").lower(), DEFAULT_EMOJI)
    loss_upper = (data.loss_type or "UNKNOWN").upper()
    urgency = "ACTIVE EMERGENCY" if data.is_active else "NON-ACTIVE"

    lines = []

    if borderline and distance is not None:
        lines.append(f"\u26a0\ufe0f OUTSIDE NORMAL SERVICE RADIUS ({distance:.0f} mi) \u2014 CONFIRM BEFORE ROLLING")
        lines.append("")

    lines.append(f"\U0001f6a8 {emoji} {loss_upper} \u2014 {urgency}")

    address_line = data.property_address or "NO ADDRESS"
    if distance is not None and not borderline:
        address_line += f" ({distance:.0f} mi)"
    lines.append(address_line)

    active_str = "ACTIVE" if data.is_active else "Not active"
    source_str = data.source_of_loss or "unknown source"
    lines.append(f"{active_str} \u2014 {source_str}")

    rooms_line = f"Rooms: {data.rooms_affected}" if data.rooms_affected else ""
    if data.water_category:
        rooms_line += f" | Cat {data.water_category}" if rooms_line else f"Cat {data.water_category}"
    if rooms_line:
        lines.append(rooms_line)

    if data.life_safety_concern:
        lines.append("\u26a0\ufe0f LIFE SAFETY CONCERN")

    lines.append(f"Insurance: {data.insurance_carrier or 'None provided'}")

    callback = _sanitize_phone(data.callback_number) or "no number"
    lines.append(f"Caller: {data.caller_name or 'Unknown'}, {callback}")
    lines.append("---")
    lines.append(data.call_summary or "No summary available")

    return "\n".join(lines)


def build_caller_confirmation_sms(caller_name: Optional[str]) -> str:
    """Build the address confirmation SMS for the caller."""
    name = caller_name or "there"
    customer = settings.CUSTOMER_NAME or "our"
    return (
        f"Hi {name}, this is {customer}'s dispatch line following up on your call. "
        f"We want to make sure we got your address right \u2014 can you reply to this "
        f"message with the full property address including city and ZIP? "
        f"We'll get a technician out as soon as we confirm."
    )


async def send_sms(to: str, body: str) -> Optional[str]:
    """Send an SMS via Twilio. Returns the message SID or None on failure."""
    if not settings.TWILIO_SID or not settings.TWILIO_AUTH:
        logger.warning("Twilio credentials not configured, skipping SMS")
        return None

    try:
        client = TwilioClient(settings.TWILIO_SID, settings.TWILIO_AUTH)
        # Twilio's Python SDK create() is synchronous; we call it directly.
        # For true async, would need to run in executor, but for MVP this is fine
        # since it's already in a background task.
        message = client.messages.create(
            body=body,
            from_=settings.TWILIO_FROM_NUMBER,
            to=to,
        )
        logger.info("SMS sent to %s, SID=%s", to, message.sid)
        return message.sid
    except Exception as e:
        logger.error("Failed to send SMS to %s: %s", to, e)
        raise
