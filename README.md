# Restoration Dispatcher

FastAPI service that receives end-of-call webhooks from Vapi, validates property addresses via Google Address Validation API, and dispatches emergency restoration jobs to on-call technicians via Twilio SMS. Multi-tenant: each customer has their own Vapi assistant ID; the webhook routes to the right customer automatically.

---

## Table of contents

1. [First-time setup](#first-time-setup)
2. [Environment variables](#environment-variables)
3. [Adding a customer](#adding-a-customer)
4. [Running locally](#running-locally)
5. [Testing with curl](#testing-with-curl)
6. [Running the test suite](#running-the-test-suite)
7. [Deploying to Render](#deploying-to-render)
8. [Pointing Vapi at the service](#pointing-vapi-at-the-service)
9. [Ongoing maintenance](#ongoing-maintenance)

---

## First-time setup

```bash
git clone https://github.com/kenan-3304/restoration-dispatcher.git
cd restoration-dispatcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your Twilio, Google, and other credentials in .env
```

---

## Environment variables

All service credentials live in `.env` (locally) or in the Render dashboard (production). Customer config lives in the database, not in env vars.

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_ADDRESS_VALIDATION_API_KEY` | Yes | Google Address Validation API key |
| `TWILIO_SID` | Yes | Twilio account SID |
| `TWILIO_AUTH` | Yes | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Yes | Twilio sending number (E.164, e.g. `+15125550000`) |
| `VAPI_WEBHOOK_SECRET` | No | Vapi webhook secret for signature verification |
| `OPENAI_API_KEY` | No | Reserved for future use |
| `DATABASE_PATH` | No | Path to SQLite file (default: `./data/dispatcher.db`) |

There are no `CUSTOMER_*` env vars. All customer config (name, phones, service area, Vapi assistant ID) lives in the `customers` table.

---

## Adding a customer

Customers are added by running `scripts/add_customer.py`. You need the Vapi assistant ID from the Vapi dashboard (Settings → Assistants → your assistant → copy the ID).

```bash
python scripts/add_customer.py \
  --name "Acme Restoration" \
  --on-call-phone "+15125550001" \
  --owner-phone "+15125550002" \
  --lat 30.2672 \
  --lng -97.7431 \
  --radius 30 \
  --assistant-id "your-vapi-assistant-id-here"
```

Output:
```
Customer added: id=1, name='Acme Restoration', assistant_id='your-vapi-assistant-id-here'
Use customer_id=1 in /test/dispatch requests.
```

**Arguments:**

| Argument | Description |
|---|---|
| `--name` | Company name (appears in SMS confirmation messages) |
| `--on-call-phone` | E.164 number that receives dispatch SMS (the on-call tech) |
| `--owner-phone` | E.164 number for escalations (optional) |
| `--lat` | Latitude of the service center / dispatch hub |
| `--lng` | Longitude of the service center / dispatch hub |
| `--radius` | Service radius in miles. Jobs within this radius dispatch immediately. Jobs within 1.5x dispatch with a warning. Beyond that, caller is asked to confirm address. |
| `--assistant-id` | The Vapi assistant ID. Webhooks are routed to this customer based on this value. |

**To add a second customer**, run the script again with different values. That's it.

**To update a customer**, edit the row directly in SQLite:

```bash
sqlite3 data/dispatcher.db
UPDATE customers SET on_call_phone='+15125559999' WHERE id=1;
.quit
```

**To list all customers:**

```bash
sqlite3 data/dispatcher.db "SELECT id, name, vapi_assistant_id FROM customers;"
```

On Render, SSH into the instance or use the Render shell to run the script against the mounted disk:

```bash
DATABASE_PATH=/opt/render/project/src/data/dispatcher.db \
python scripts/add_customer.py --name "..." ...
```

---

## Running locally

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. On first start it creates `data/dispatcher.db` and the schema. No customers exist yet — add one with the script above before testing.

---

## Testing with curl

### Health check

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

### Test dispatch (in-radius)

Replace `1` with the `customer_id` returned by `add_customer.py`.

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-001",
    "customer_id": 1,
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

Expected: dispatch SMS lands on the on-call phone. Check the DB row:

```bash
curl http://localhost:8000/calls/recent?limit=1
```

### Test dispatch (out-of-radius — triggers caller confirmation)

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-002",
    "customer_id": 1,
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

Expected: caller confirmation SMS sent to `+12145559876`, no dispatch SMS.

### Simulate a Vapi webhook

```bash
curl -X POST http://localhost:8000/webhook/vapi \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "call": {
        "id": "vapi-test-001",
        "assistantId": "your-vapi-assistant-id-here"
      },
      "analysis": {
        "structuredData": {
          "caller_name": "Sarah Chen",
          "callback_number": "+15125551234",
          "property_address": "1247 Oak Street, Austin, TX 78704",
          "loss_type": "water",
          "is_active": true,
          "source_of_loss": "dishwasher supply line",
          "call_outcome": "emergency_dispatch",
          "call_summary": "Active water leak in kitchen."
        }
      }
    }
  }'
```

Unknown `assistantId` → returns `{"status":"ignored","reason":"unknown assistant '...'"}` (200, no dispatch).

### View recent calls

```bash
curl "http://localhost:8000/calls/recent?limit=10"
```

---

## Running the test suite

```bash
pytest app/tests/ -v
```

Tests cover the address validation and haversine distance logic. No real API calls are made.

---

## Deploying to Render

1. Push this repo to GitHub.
2. In Render: **New** → **Web Service** → connect the repo. Render detects `render.yaml` automatically.
3. In the Render dashboard, set these environment variables:
   - `GOOGLE_ADDRESS_VALIDATION_API_KEY`
   - `TWILIO_SID`
   - `TWILIO_AUTH`
   - `TWILIO_FROM_NUMBER`
   - `VAPI_WEBHOOK_SECRET` (if you set one in Vapi)
   - `DATABASE_PATH` → `/opt/render/project/src/data/dispatcher.db`
4. Deploy.
5. Add your first customer via the Render shell:
   ```bash
   python scripts/add_customer.py --name "Acme" --on-call-phone "+1..." \
     --lat 30.27 --lng -97.74 --radius 30 --assistant-id "vapi-id-here"
   ```

The `render.yaml` mounts a 1 GB persistent disk at `/opt/render/project/src/data/` so the SQLite file survives deploys and restarts.

---

## Pointing Vapi at the service

1. In your Vapi assistant settings, set the **Server URL** (webhook) to:
   ```
   https://your-render-url.onrender.com/webhook/vapi
   ```
2. Make sure the assistant ID in Vapi matches the `vapi_assistant_id` you inserted for that customer. The service routes webhooks by `message.call.assistantId`.
3. Trigger a test call. Watch logs in the Render dashboard.

---

## Ongoing maintenance

### Inspecting the database

```bash
# Local
sqlite3 data/dispatcher.db

# Render shell
sqlite3 /opt/render/project/src/data/dispatcher.db
```

Useful queries:

```sql
-- All customers
SELECT id, name, vapi_assistant_id FROM customers;

-- Recent calls with dispatch status
SELECT vapi_call_id, received_at, call_outcome, address_validation_status,
       dispatch_sent, caller_confirmation_sent, error
FROM calls ORDER BY received_at DESC LIMIT 20;

-- Calls with errors
SELECT vapi_call_id, received_at, error FROM calls
WHERE error IS NOT NULL ORDER BY received_at DESC;

-- Calls for a specific customer
SELECT * FROM calls WHERE customer_id = 1 ORDER BY received_at DESC LIMIT 10;
```

### Updating a customer's on-call phone

```sql
UPDATE customers SET on_call_phone = '+15125559999' WHERE id = 1;
```

### Changing a customer's service radius

```sql
UPDATE customers SET service_radius_mi = 40 WHERE id = 1;
```

### Address validation tiers

| Distance | Outcome |
|---|---|
| ≤ radius | Dispatch SMS sent to on-call tech |
| radius – 1.5× radius | Dispatch SMS sent with "OUTSIDE NORMAL SERVICE RADIUS" warning |
| > 1.5× radius | Caller confirmation SMS sent; no dispatch until address confirmed |
| Invalid/incomplete | Caller confirmation SMS sent; no dispatch |

### Logs

Render captures all stdout. Filter by call ID in the Render log viewer. Each step logs at INFO level with the call ID and customer name.
