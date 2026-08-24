import logging
import re
from typing import Optional
from urllib.parse import quote_plus

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

_IS_ACTIVE_URGENCY = {"yes": "ACTIVE EMERGENCY", "no": "NON-ACTIVE"}
_IS_ACTIVE_LABEL = {"yes": "ACTIVE", "no": "Not active"}


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
    """Pick the number to text: alternate_callback_number overrides the live caller ID
    if the caller explicitly gave a different number to use; otherwise use caller_id."""
    alternate = _sanitize_phone(data.alternate_callback_number)
    if alternate:
        return alternate
    return _sanitize_phone(caller_id)


def build_dispatch_sms(
    data: StructuredData,
    callback: Optional[str] = None,
    distance: Optional[float] = None,
    borderline: bool = False,
    formatted_address: Optional[str] = None,
) -> str:
    """Build the dispatch SMS body for the on-call tech."""
    emoji = LOSS_TYPE_EMOJI.get((data.loss_type or "").lower(), DEFAULT_EMOJI)
    loss_upper = (data.loss_type or "UNKNOWN").upper()
    urgency = _IS_ACTIVE_URGENCY.get(data.is_active, "ACTIVITY UNKNOWN")

    lines = []

    if borderline and distance is not None:
        lines.append(f"\u26a0\ufe0f OUTSIDE NORMAL SERVICE RADIUS ({distance:.0f} mi) \u2014 CONFIRM BEFORE ROLLING")
        lines.append("")

    lines.append(f"\U0001f6a8 {emoji} {loss_upper} \u2014 {urgency}")

    address = formatted_address or data.address_full
    address_line = address or "NO ADDRESS"
    if distance is not None and not borderline:
        address_line += f" ({distance:.0f} mi)"
    lines.append(address_line)
    if address:
        lines.append(f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}")

    active_str = _IS_ACTIVE_LABEL.get(data.is_active, "Activity unknown")
    source_str = data.source_detail or "unknown source"
    lines.append(f"{active_str} \u2014 {source_str}")

    callback_str = _sanitize_phone(callback) or "no number"
    lines.append(f"Caller: {data.caller_name or 'Unknown'}, {callback_str}")
    lines.append("---")
    lines.append(data.call_summary or "No summary available")

    return "\n".join(lines)


def build_caller_confirmation_sms(caller_name: Optional[str], customer_name: str = "") -> str:
    """Build the address confirmation SMS for the caller."""
    name = caller_name or "there"
    customer = customer_name or "our"
    return (
        f"Hi {name}, this is {customer}'s dispatch line following up on your call. "
        f"We want to make sure we got your address right \u2014 can you reply to this "
        f"message with the full property address including city and ZIP? "
        f"We'll get a technician out as soon as we confirm."
    )


def build_address_confirmed_sms(caller_name: Optional[str], customer_name: str, address: str) -> str:
    """Sent to caller after their replied address validates and dispatch goes out."""
    name = caller_name or "there"
    return (
        f"Got it, {name}! {customer_name} is sending a technician to {address}. "
        f"They'll be in touch shortly."
    )


def build_outside_radius_sms(caller_name: Optional[str], customer_name: str) -> str:
    """Sent to caller when their replied address is still outside the service area."""
    name = caller_name or "there"
    return (
        f"Hi {name}, unfortunately that address is outside {customer_name}'s service area. "
        f"We recommend searching for a local restoration company in your area."
    )


def build_address_unverifiable_sms(caller_name: Optional[str], customer_name: str) -> str:
    """Sent to caller when their replied address cannot be validated."""
    name = caller_name or "there"
    return (
        f"Hi {name}, we weren't able to verify that address. "
        f"Please call {customer_name} directly so we can get you the right help."
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
