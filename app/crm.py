"""
CRM integration — push a dispatched call as a new job/lead.

Supported CRM types: "servicetitan", "jobnimbus"

Each customer row stores crm_type (TEXT) and crm_config (JSON TEXT).
push_to_crm() is the only public entry point; all errors are caught
internally so a CRM failure never interrupts the dispatch pipeline.
"""
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def push_to_crm(
    crm_type: Optional[str],
    crm_config: Optional[str],
    call: dict,
    customer: dict,
) -> Optional[str]:
    """Push call data to the customer's CRM. Returns job/lead ID or None."""
    if not crm_type:
        return None

    config: dict = {}
    if crm_config:
        try:
            config = json.loads(crm_config)
        except json.JSONDecodeError:
            logger.error("Invalid crm_config JSON for customer %s", customer["id"])
            return None

    try:
        if crm_type == "servicetitan":
            return await _push_servicetitan(config, call, customer)
        if crm_type == "jobnimbus":
            return await _push_jobnimbus(config, call, customer)
        logger.warning("Unknown CRM type '%s' for customer %s", crm_type, customer["id"])
        return None
    except Exception as e:
        logger.error(
            "CRM push failed (call=%s, customer=%s, crm=%s): %s",
            call.get("vapi_call_id"), customer["id"], crm_type, e,
        )
        return None


# ---------------------------------------------------------------------------
# ServiceTitan
# ---------------------------------------------------------------------------

async def _push_servicetitan(config: dict, call: dict, customer: dict) -> Optional[str]:
    required = ("client_id", "client_secret", "app_key", "tenant_id")
    if missing := [k for k in required if not config.get(k)]:
        logger.error("ServiceTitan config missing keys: %s", missing)
        return None

    tid = config["tenant_id"]

    async with httpx.AsyncClient(timeout=15) as http:
        # 1. OAuth token
        token_resp = await http.post(
            "https://auth.servicetitan.io/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]

        headers = {
            "Authorization": f"Bearer {token}",
            "ST-App-Key": config["app_key"],
            "Content-Type": "application/json",
        }

        # 2. Find or create the customer in ServiceTitan
        st_customer_id = await _st_find_or_create_customer(http, headers, tid, call)
        if not st_customer_id:
            return None

        # 3. Create job
        job_body: dict = {
            "customerId": st_customer_id,
            "summary": _build_summary(call, customer),
            "priority": "Urgent" if call.get("is_active") == "yes" else "Medium",
        }
        if config.get("job_type_id"):
            job_body["typeId"] = int(config["job_type_id"])
        if config.get("business_unit_id"):
            job_body["businessUnitId"] = int(config["business_unit_id"])

        job_resp = await http.post(
            f"https://api.servicetitan.io/jpm/v2/tenant/{tid}/jobs",
            json=job_body,
            headers=headers,
        )
        job_resp.raise_for_status()
        job_id = str(job_resp.json()["id"])
        logger.info("ServiceTitan job created: %s (call %s)", job_id, call.get("vapi_call_id"))
        return job_id


async def _st_find_or_create_customer(
    http: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    call: dict,
) -> Optional[int]:
    phone = call.get("callback_number") or ""

    # Search by phone first
    if phone:
        resp = await http.get(
            f"https://api.servicetitan.io/crm/v2/tenant/{tenant_id}/customers",
            params={"phone": phone, "pageSize": 1},
            headers=headers,
        )
        if resp.is_success:
            data = resp.json().get("data", [])
            if data:
                return data[0]["id"]

    # Create new customer
    raw_address = call.get("address_full") or ""
    parts = [p.strip() for p in raw_address.split(",")]
    street = parts[0] if parts else raw_address

    resp = await http.post(
        f"https://api.servicetitan.io/crm/v2/tenant/{tenant_id}/customers",
        json={
            "name": call.get("caller_name") or "Unknown Caller",
            "type": "Residential",
            "address": {"street": street, "city": "", "state": "", "zip": "", "country": "USA"},
            "contacts": [{"type": "Phone", "value": phone, "memo": "Mobile"}] if phone else [],
        },
        headers=headers,
    )
    if resp.is_success:
        return resp.json()["id"]

    logger.error("Failed to create ServiceTitan customer: %s", resp.text)
    return None


# ---------------------------------------------------------------------------
# JobNimbus
# ---------------------------------------------------------------------------

async def _push_jobnimbus(config: dict, call: dict, customer: dict) -> Optional[str]:
    if not config.get("api_key"):
        logger.error("JobNimbus config missing 'api_key' for customer %s", customer["id"])
        return None

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    name_parts = (call.get("caller_name") or "Unknown Caller").split(maxsplit=1)
    first = name_parts[0]
    last = name_parts[1] if len(name_parts) > 1 else "Caller"

    async with httpx.AsyncClient(timeout=15) as http:
        # Create contact
        contact_resp = await http.post(
            "https://app.jobnimbus.com/api1/contacts",
            json={
                "first_name": first,
                "last_name": last,
                "mobile_phone": call.get("callback_number") or "",
                "address_line1": call.get("address_full") or "",
            },
            headers=headers,
        )
        contact_resp.raise_for_status()
        contact_id = contact_resp.json()["jnid"]

        # Create job linked to contact
        loss = (call.get("loss_type") or "loss").title()
        job_resp = await http.post(
            "https://app.jobnimbus.com/api1/jobs",
            json={
                "name": f"{loss} — {call.get('caller_name', 'Unknown')}",
                "customer": contact_id,
                "description": _build_summary(call, customer),
                "status_name": "New Lead",
            },
            headers=headers,
        )
        job_resp.raise_for_status()
        job_id = job_resp.json()["jnid"]
        logger.info("JobNimbus job created: %s (call %s)", job_id, call.get("vapi_call_id"))
        return job_id


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _build_summary(call: dict, customer: dict) -> str:
    parts = []
    if call.get("loss_type"):
        is_active = call.get("is_active")
        active = "Active" if is_active == "yes" else ("Non-active" if is_active == "no" else "Activity unknown")
        parts.append(f"{active} {call['loss_type']} loss")
    if call.get("address_full"):
        parts.append(f"Address: {call['address_full']}")
    if call.get("source_detail"):
        parts.append(f"Source: {call['source_detail']}")
    if call.get("call_summary"):
        parts.append(call["call_summary"])
    return " | ".join(parts) or "Restoration dispatch"
