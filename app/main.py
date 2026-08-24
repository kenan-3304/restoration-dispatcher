import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.db import (
    init_db, close_db,
    get_recent_calls,
    get_customer_by_assistant_id, get_customer_by_id,
    get_pending_call_by_phone,
    create_customer, list_customers,
)
from app.models import AdminCreateCustomerRequest, StructuredData, TestDispatchRequest
from app.pipeline import process_call, process_address_reply

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _require_admin(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY not configured on server")
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if not settings.ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY is not set — admin endpoints will return 503")
    logger.info("Restoration dispatcher started")
    yield
    await close_db()
    logger.info("Restoration dispatcher shutting down")


app = FastAPI(title="Restoration Dispatcher", lifespan=lifespan)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok"}


@app.post("/webhook/vapi")
async def webhook_vapi(request: Request, background_tasks: BackgroundTasks):
    """Receive Vapi end-of-call webhook. Returns 200 immediately, processes in background."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = body.get("message", {})

    msg_type = message.get("type", "")
    if msg_type != "end-of-call-report":
        logger.info("Ignoring message type: %s", msg_type)
        return {"status": "ignored", "reason": f"message type '{msg_type}' not handled"}

    call_data = message.get("call", {})
    call_id = call_data.get("id")
    if not call_id:
        return JSONResponse({"error": "No call.id in payload"}, status_code=400)

    assistant_id = call_data.get("assistantId") or message.get("assistant", {}).get("id")
    if not assistant_id:
        logger.warning("No assistantId in payload for call %s", call_id)
        return {"status": "ignored", "reason": "no assistantId — cannot route to customer"}

    customer = await get_customer_by_assistant_id(assistant_id)
    if not customer:
        logger.warning("Unknown assistantId %s for call %s", assistant_id, call_id)
        return {"status": "ignored", "reason": f"unknown assistant '{assistant_id}'"}

    analysis = message.get("analysis", {})
    structured = analysis.get("structuredData", {})

    if not structured:
        # The unwrap below only fires when analysis.structuredData is non-empty.
        # If it's empty here, either Vapi genuinely extracted nothing, or the
        # data is living somewhere else in the payload than we think — dump the
        # full message body once so we can see the actual shape and stop guessing.
        logger.warning("Raw webhook body for call %s (structuredData empty): %s", call_id, json.dumps(body))

    # Vapi's newer "Structured Outputs" feature nests each configured output's
    # fields under a generated id instead of putting them flat on structuredData:
    # {"<id>": {"name": "restoration", "result": {...fields...}}}. Unwrap it so
    # the rest of the pipeline sees flat fields regardless of which Vapi feature
    # produced them.
    if structured and all(
        isinstance(v, dict) and "result" in v for v in structured.values()
    ):
        entry = next(
            (v for v in structured.values() if v.get("name") == "restoration"),
            next(iter(structured.values())),
        )
        structured = entry.get("result") or {}

    if not structured:
        logger.warning(
            "Empty structuredData for call %s — Vapi's extractor found nothing "
            "(likely a dead-air/no-answer call); treating as no_response",
            call_id,
        )

    try:
        data = StructuredData.model_validate(structured)
    except Exception as e:
        logger.error("Failed to parse structuredData for call %s: %s", call_id, e)
        return JSONResponse({"error": f"Invalid structuredData: {e}"}, status_code=400)

    caller_id = call_data.get("phone_number") or call_data.get("customer", {}).get("number")
    raw_payload = json.dumps(body)

    background_tasks.add_task(process_call, call_id, data, raw_payload, customer, caller_id)
    logger.info("Queued processing for call %s (customer: %s)", call_id, customer["name"])

    return {"status": "ok", "call_id": call_id}


@app.post("/test/dispatch")
async def test_dispatch(req: TestDispatchRequest, background_tasks: BackgroundTasks):
    """Test endpoint: submit structured data directly, runs same pipeline."""
    customer = await get_customer_by_id(req.customer_id)
    if not customer:
        return JSONResponse(
            {"error": f"Customer {req.customer_id} not found"},
            status_code=400,
        )

    data = StructuredData(
        call_outcome=req.call_outcome,
        loss_type=req.loss_type,
        caller_name=req.caller_name,
        address_full=req.address_full,
        call_summary=req.call_summary,
        is_active=req.is_active,
        source_detail=req.source_detail,
        alternate_callback_number=req.alternate_callback_number,
    )

    raw_payload = json.dumps(req.model_dump())
    background_tasks.add_task(process_call, req.call_id, data, raw_payload, customer, req.caller_phone)
    logger.info("Test dispatch queued for call_id=%s (customer: %s)", req.call_id, customer["name"])

    return {"status": "ok", "call_id": req.call_id, "customer": customer["name"]}


@app.post("/webhook/twilio/inbound")
async def twilio_inbound(request: Request, background_tasks: BackgroundTasks):
    """Receive inbound SMS from Twilio. Handles caller address replies."""
    form = await request.form()
    from_number = str(form.get("From", "")).strip()
    body = str(form.get("Body", "")).strip()

    logger.info("Inbound SMS from %s: %.80s", from_number, body)

    if not from_number or not body:
        return _twiml("")

    call = await get_pending_call_by_phone(from_number)
    if not call:
        logger.info("No pending confirmation found for %s, ignoring", from_number)
        return _twiml("")

    customer = await get_customer_by_id(call["customer_id"])
    if not customer:
        return _twiml("")

    background_tasks.add_task(process_address_reply, call, customer, body)
    logger.info("Queued address reply for call %s from %s", call["vapi_call_id"], from_number)

    return _twiml("Thanks — we’re verifying your address now and will follow up shortly.")


@app.post("/admin/customers")
async def admin_create_customer(req: AdminCreateCustomerRequest, _=Depends(_require_admin)):
    try:
        row = await create_customer(
            name=req.name,
            on_call_phone=req.on_call_phone,
            owner_phone=req.owner_phone,
            lat=req.lat,
            lng=req.lng,
            radius=req.radius,
            assistant_id=req.assistant_id,
            crm_type=req.crm_type,
            crm_config=req.crm_config,
        )
        logger.info("Admin: created customer id=%s name=%s", row["id"], row["name"])
        return row
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"A customer with that assistant_id already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/customers")
async def admin_list_customers(_=Depends(_require_admin)):
    return {"customers": await list_customers()}


def _twiml(message: str) -> Response:
    inner = f"<Message>{message}</Message>" if message else ""
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>'
    return Response(content=xml, media_type="application/xml")


@app.get("/calls/recent")
async def calls_recent(limit: int = 20):
    """Return the last N calls from the database."""
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    rows = await get_recent_calls(limit)
    return {"calls": rows, "count": len(rows)}
