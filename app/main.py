import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.db import init_db, get_recent_calls, get_customer_by_assistant_id, get_customer_by_id, get_pending_call_by_phone
from app.models import StructuredData, TestDispatchRequest
from app.pipeline import process_call, process_address_reply

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Restoration dispatcher started")
    yield
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
        life_safety_concern=req.life_safety_concern,
        caller_name=req.caller_name,
        callback_number=req.callback_number,
        address_full=req.address_full,
        address_confirmed=req.address_confirmed,
        loss_type=req.loss_type,
        is_active=req.is_active,
        source_detail=req.source_detail,
        water_clean_or_dirty=req.water_clean_or_dirty,
        access_notes=req.access_notes,
        insurance_carrier=req.insurance_carrier,
        callback_reason=req.callback_reason,
        call_summary=req.call_summary,
    )

    raw_payload = json.dumps(req.model_dump())
    background_tasks.add_task(process_call, req.call_id, data, raw_payload, customer)
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
