import math
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EARTH_RADIUS_MI = 3958.8


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance in miles between two lat/lng points using haversine formula."""
    lat1_r, lng1_r = math.radians(lat1), math.radians(lng1)
    lat2_r, lng2_r = math.radians(lat2), math.radians(lng2)

    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_MI * c


def classify_address(
    google_response: dict,
    service_center_lat: float,
    service_center_lng: float,
    service_radius_mi: int,
) -> tuple[str, dict]:
    """
    Returns (tier, details_dict).
    Tiers: 'in_radius', 'borderline', 'out_of_radius', 'invalid'
    """
    verdict = google_response["result"]["verdict"]

    if not verdict.get("addressComplete", False):
        return "invalid", {"reason": "Address incomplete per Google"}

    location = google_response["result"]["geocode"]["location"]
    distance = haversine_miles(
        service_center_lat,
        service_center_lng,
        location["latitude"],
        location["longitude"],
    )

    if verdict.get("hasReplacedComponents", False):
        return "invalid", {"reason": "Google replaced address components", "distance": distance}

    if distance <= service_radius_mi:
        return "in_radius", {"distance": distance}
    elif distance <= service_radius_mi * 1.5:
        return "borderline", {"distance": distance}
    else:
        return "out_of_radius", {"distance": distance}


async def validate_address(
    address: str,
    service_center_lat: float,
    service_center_lng: float,
    service_radius_mi: int,
) -> tuple[str, Optional[str], Optional[float]]:
    """
    Validate an address via Google Address Validation API.
    Returns (tier, formatted_address, distance_mi).
    """
    api_key = settings.GOOGLE_ADDRESS_VALIDATION_API_KEY
    if not api_key:
        logger.warning("No Google API key configured, skipping address validation")
        return "skipped", None, None

    url = f"https://addressvalidation.googleapis.com/v1:validateAddress?key={api_key}"
    payload = {"address": {"addressLines": [address]}}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        google_response = resp.json()

    tier, details = classify_address(
        google_response,
        service_center_lat,
        service_center_lng,
        service_radius_mi,
    )

    formatted_address = (
        google_response.get("result", {}).get("address", {}).get("formattedAddress")
    )
    distance = details.get("distance")

    logger.info(
        "Address validation: tier=%s, distance=%.1f mi, address=%s",
        tier,
        distance if distance else 0,
        formatted_address,
    )

    return tier, formatted_address, distance
