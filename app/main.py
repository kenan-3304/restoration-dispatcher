import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import init_db, get_recent_calls, get_customer_by_assistant_id, get_customer_by_id
from app.models import StructuredData, TestDispatchRequest
from app.pipeline import process_call

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


@app.get("/healthz")
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
        caller_name=req.caller_name,
        callback_number=req.callback_number,
        property_address=req.property_address,
        loss_type=req.loss_type,
        is_active=req.is_active,
        source_of_loss=req.source_of_loss,
        call_outcome=req.call_outcome,
        call_summary=req.call_summary,
        water_category=req.water_category,
        rooms_affected=req.rooms_affected,
        insurance_carrier=req.insurance_carrier,
        life_safety_concern=req.life_safety_concern,
    )

    raw_payload = json.dumps(req.model_dump())
    background_tasks.add_task(process_call, req.call_id, data, raw_payload, customer)
    logger.info("Test dispatch queued for call_id=%s (customer: %s)", req.call_id, customer["name"])

    return {"status": "ok", "call_id": req.call_id, "customer": customer["name"]}


@app.get("/calls/recent")
async def calls_recent(limit: int = 20):
    """Return the last N calls from the database."""
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    rows = await get_recent_calls(limit)
    return {"calls": rows, "count": len(rows)}
