# Restoration Dispatcher

FastAPI service that receives end-of-call webhooks from Vapi, validates property addresses via Google Address Validation API, and dispatches emergency restoration jobs to on-call technicians via Twilio SMS.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Required env vars

| Variable | Description |
|---|---|
| `GOOGLE_ADDRESS_VALIDATION_API_KEY` | Google Address Validation API key |
| `TWILIO_SID` | Twilio account SID |
| `TWILIO_AUTH` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Twilio phone number (E.164, e.g. `+15125550000`) |
| `CUSTOMER_NAME` | Your company name (used in SMS) |
| `CUSTOMER_ON_CALL_PHONE` | On-call tech phone (E.164) |
| `CUSTOMER_SERVICE_CENTER_LAT` | Service center latitude |
| `CUSTOMER_SERVICE_CENTER_LNG` | Service center longitude |
| `CUSTOMER_SERVICE_RADIUS_MI` | Service radius in miles |
| `CUSTOMER_OWNER_PHONE` | Owner phone for escalations (E.164) |

Optional: `VAPI_WEBHOOK_SECRET`, `OPENAI_API_KEY`, `DATABASE_PATH` (defaults to `./data/dispatcher.db`).

## Run locally

```bash
uvicorn app.main:app --reload
```

## Test with curl

### Health check

```bash
curl http://localhost:8000/healthz
```

### Test dispatch (in-radius)

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-001",
    "caller_name": "Sarah Chen",
    "callback_number": "+15125551234",
    "property_address": "1247 Oak Street, Austin, TX 78704",
    "loss_type": "water",
    "is_active": true,
    "source_of_loss": "dishwasher supply line",
    "call_outcome": "emergency_dispatch",
    "call_summary": "Active water leak from dishwasher supply line in kitchen.",
    "water_category": "2",
    "rooms_affected": "kitchen, dining",
    "insurance_carrier": "State Farm",
    "life_safety_concern": false
  }'
```

### Test dispatch (out-of-radius)

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-002",
    "caller_name": "John Doe",
    "callback_number": "+12145559876",
    "property_address": "500 Main St, Dallas, TX 75201",
    "loss_type": "fire",
    "is_active": false,
    "source_of_loss": "kitchen fire",
    "call_outcome": "emergency_dispatch",
    "call_summary": "Kitchen fire damage, no active flames.",
    "insurance_carrier": "Allstate",
    "life_safety_concern": false
  }'
```

### View recent calls

```bash
curl http://localhost:8000/calls/recent?limit=10
```

## Run tests

```bash
pytest app/tests/ -v
```

## Deploy to Render

1. Push to GitHub
2. Connect the repo in Render
3. Render auto-detects `render.yaml`
4. Set all env vars in the Render dashboard
5. Deploy

## Point Vapi at it

Set your Vapi assistant's webhook URL to:

```
https://your-render-url.com/webhook/vapi
```

The endpoint accepts Vapi's `end-of-call-report` message type and processes calls in the background.
