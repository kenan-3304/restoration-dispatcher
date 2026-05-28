# Restoration Dispatcher

FastAPI service that receives end-of-call webhooks from Vapi, validates property addresses via Google Address Validation API, and dispatches emergency restoration jobs to on-call technicians via Twilio SMS. Multi-tenant: each customer has their own Vapi assistant ID; the webhook routes to the right customer automatically.

Optionally pushes dispatched calls to the customer's CRM (ServiceTitan or JobNimbus) after each successful dispatch.

---

## Table of contents

1. [First-time setup](#first-time-setup)
2. [Environment variables](#environment-variables)
3. [Adding a customer](#adding-a-customer)
4. [CRM integration](#crm-integration)
5. [Database](#database)
6. [Running locally](#running-locally)
7. [Testing](#testing)
8. [Deploying to Render](#deploying-to-render)
9. [Pointing Vapi at the service](#pointing-vapi-at-the-service)
10. [Configuring Twilio inbound SMS](#configuring-twilio-inbound-sms)
11. [Onboarding a client](#onboarding-a-client)
12. [Ongoing maintenance](#ongoing-maintenance)

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

All service credentials live in `.env` (locally) or in the Render dashboard (production). Customer config — including CRM credentials — lives in the database, not in env vars.

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_ADDRESS_VALIDATION_API_KEY` | Yes | Google Address Validation API key |
| `TWILIO_SID` | Yes | Twilio account SID |
| `TWILIO_AUTH` | Yes | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Yes | Twilio sending number (E.164, e.g. `+15125550000`) |
| `VAPI_WEBHOOK_SECRET` | No | Vapi webhook secret for signature verification |
| `DATABASE_PATH` | No | Path to SQLite file (default: `./data/dispatcher.db`) |

---

## Adding a customer

Run `scripts/add_customer.py` with no arguments for interactive mode — it walks you through every field:

```bash
python scripts/add_customer.py
```

```
=== Restoration Dispatcher — Add Customer ===
  Database: ./data/dispatcher.db

Company name (e.g. Acme Restoration): Acme Restoration
On-call phone in E.164 format (e.g. +15125550001): +15125550001
Owner/escalation phone (optional, Enter to skip):
Service hub latitude (decimal, e.g. 30.2672): 30.2672
Service hub longitude (decimal, e.g. -97.7431): -97.7431
Service radius in miles [30]:
Vapi assistant ID (from Vapi dashboard → Assistants → copy ID): abc-123-def

--- CRM Integration (optional) ---
CRM type — servicetitan / jobnimbus / none [none]:

✓ Customer added: id=1, name='Acme Restoration', assistant_id='abc-123-def'
  Use customer_id=1 in /test/dispatch requests.
```

**To add a second customer**, run the script again. Each customer gets their own Vapi assistant ID and can have their own CRM.

**To add via CLI flags** (useful for scripting):

```bash
python scripts/add_customer.py \
  --name "Acme Restoration" \
  --on-call-phone "+15125550001" \
  --lat 30.2672 --lng -97.7431 \
  --radius 30 \
  --assistant-id "your-vapi-assistant-id"
```

With CRM:
```bash
python scripts/add_customer.py \
  --name "Acme Restoration" \
  --on-call-phone "+15125550001" \
  --lat 30.2672 --lng -97.7431 \
  --radius 30 \
  --assistant-id "your-vapi-assistant-id" \
  --crm-type jobnimbus \
  --crm-config '{"api_key":"your-jobnimbus-api-key"}'
```

**To update a customer**, edit the row directly in SQLite:

```bash
sqlite3 data/dispatcher.db
UPDATE customers SET on_call_phone='+15125559999' WHERE id=1;
.quit
```

**On Render**, open the Render shell and run:

```bash
DATABASE_PATH=/opt/render/project/src/data/dispatcher.db python scripts/add_customer.py
```

---

## CRM integration

> **Status: built but not yet tested against live APIs.** The logic is implemented — do not enable CRM for a client until you have confirmed it works with their real credentials. CRM failures are caught and logged; they never interrupt the SMS dispatch.

Each customer can optionally have a CRM configured. When the dispatcher sends a dispatch SMS, it also creates a job/lead in the customer's CRM.

**What each integration does:**

| CRM | What gets created | Required credentials |
|---|---|---|
| ServiceTitan | Finds or creates a customer by phone, then creates a Job | `client_id`, `client_secret`, `app_key`, `tenant_id` |
| JobNimbus | Creates a Contact and a Job (status: New Lead) | `api_key` |

CRM credentials are stored per-customer in the database — each client uses their own account, no shared env vars.

---

### ServiceTitan setup

ServiceTitan uses OAuth2. Before you start, the client needs to create an app in their ServiceTitan developer portal.

**Credentials to collect from the client:**
- **Client ID** and **Client Secret** — from their ST developer portal app
- **App Key** — also from the developer portal app
- **Tenant ID** — their ServiceTitan account ID (visible in the URL when logged in: `go.servicetitan.com/#/.../{tenant_id}/...`)
- **Job Type ID** *(optional)* — from Settings → Job Types in ST admin. Needed if ST requires a job type on creation.
- **Business Unit ID** *(optional)* — from Settings → Business Units. Needed if ST requires a business unit on creation.

**Add to the customer record:**
```bash
python scripts/add_customer.py  # interactive mode will prompt for CRM fields
```
Or update an existing customer directly:
```sql
UPDATE customers SET
  crm_type = 'servicetitan',
  crm_config = '{"client_id":"...","client_secret":"...","app_key":"...","tenant_id":"12345"}'
WHERE id = 1;
```

**What happens on each dispatched call:**
1. Authenticates with OAuth using client credentials
2. Searches ServiceTitan for a customer matching the caller's phone number
3. If not found, creates a new Residential customer with their name and address
4. Creates a Job attached to that customer with Urgent or Medium priority

---

### JobNimbus setup

JobNimbus is simpler — just an API key.

**Credentials to collect from the client:**
- **API Key** — from JobNimbus → Settings → API

**Add to the customer record:**
```sql
UPDATE customers SET
  crm_type = 'jobnimbus',
  crm_config = '{"api_key":"your-key-here"}'
WHERE id = 1;
```

**What happens on each dispatched call:**
1. Creates a Contact with the caller's name, phone, and address
2. Creates a Job linked to that contact with status "New Lead" and a summary of the loss

---

### Testing CRM integration

**Before testing with a client's live CRM**, verify the credentials work at all:

```bash
# Update your own test customer (id=2) with real credentials temporarily
sqlite3 data/dispatcher.db "
UPDATE customers SET
  crm_type = 'jobnimbus',
  crm_config = '{\"api_key\":\"your-test-key\"}'
WHERE id = 2;"
```

Then trigger a test dispatch:
```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "crm-test-001",
    "customer_id": 2,
    "call_outcome": "emergency_dispatch",
    "caller_name": "CRM Test",
    "callback_number": "+17037750484",
    "address_full": "44110 Ashburn Shopping Plaza, Ashburn, VA 20147",
    "loss_type": "water",
    "is_active": true,
    "source_detail": "test",
    "call_summary": "CRM integration test call."
  }'
```

**Check if it worked:**
```bash
# crm_job_id will be populated if the push succeeded
curl "http://localhost:8000/calls/recent?limit=1"
```

Or in SQLite:
```sql
SELECT vapi_call_id, dispatch_sent, crm_job_id, error
FROM calls WHERE vapi_call_id = 'crm-test-001';
```

- `crm_job_id` is populated → CRM push succeeded, note the ID and verify the job appears in the CRM dashboard
- `crm_job_id` is NULL and `error` is NULL → CRM push failed silently; check Render logs for a `CRM push failed` line
- `crm_job_id` is NULL and `error` is set → the dispatch itself failed (unrelated to CRM)

**After testing**, remove CRM config from your test customer so it doesn't create junk records:
```sql
UPDATE customers SET crm_type = NULL, crm_config = NULL WHERE id = 2;
```

### No CRM

Leave CRM type blank — the dispatcher works exactly as before with SMS only.

---

## Database

The service uses **SQLite** stored on a 1 GB persistent disk on Render. This is the right choice for this workload:

- Low write volume (tens to hundreds of calls per day)
- Single-server deployment — no concurrent write contention
- No external DB to provision, pay for, or maintain
- Survives restarts and redeploys via the mounted disk

**When to switch to PostgreSQL:** If you need to run multiple server instances (horizontal scaling), or if you want managed point-in-time backups, switch to Render's managed PostgreSQL. The schema and queries are standard SQL — migration would be straightforward.

---

## Running locally

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. On first start it creates `data/dispatcher.db` and the schema. No customers exist yet — add one with the script above before testing.

---

## Testing

### Before you test anything

You need one customer row in the database. Your own phone number goes in two places — and that's it, you don't need to be in the database as anything else:

```bash
python scripts/add_customer.py
```

Fill it in like this for testing:
- **Company name:** anything (e.g. "Test Co")
- **On-call phone:** your mobile number — this is where dispatch SMS gets sent
- **Service hub lat/lng:** coordinates near your test addresses (e.g. your city center)
- **Service radius:** 30 miles
- **Vapi assistant ID:** copy this from the Vapi dashboard; required to route real calls

When you send test requests, you put your number as `callback_number` in the request body — that's the simulated caller's phone. If you trigger the out-of-radius flow, the address confirmation SMS goes to that number, and when you reply the inbound hook processes it.

---

### Unit tests (no credentials needed)

```bash
pytest app/tests/ -v
```

Covers address validation logic and haversine distance. No real API calls, no Twilio, no credentials needed.

---

### Local integration tests (real SMS)

Start the server:

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

#### Health check

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

#### Dispatch test — in-radius (SMS hits your phone)

Replace `2` with your `customer_id` and the phone numbers with your own.

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-001",
    "customer_id": 2,
    "call_outcome": "emergency_dispatch",
    "caller_name": "Sarah Chen",
    "callback_number": "+17037750484",
    "address_full": "44110 Ashburn Shopping Plaza, Ashburn, VA 20147",
    "address_confirmed": true,
    "loss_type": "water",
    "is_active": true,
    "source_detail": "dishwasher drain line",
    "water_clean_or_dirty": "clean",
    "access_notes": "Park in driveway, front door is unlocked",
    "insurance_carrier": "State Farm",
    "life_safety_concern": false,
    "call_summary": "Active water leak from dishwasher in kitchen. Homeowner is home. Two young kids present. Water reached dining room carpet."
  }'
```

Expected: dispatch SMS arrives on `+17037750484`.

#### Dispatch test — out-of-radius (triggers address confirmation loop)

```bash
curl -X POST http://localhost:8000/test/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "test-002",
    "customer_id": 2,
    "call_outcome": "emergency_dispatch",
    "caller_name": "John Doe",
    "callback_number": "+17037750484",
    "address_full": "500 Main St, Dallas, TX 75201",
    "address_confirmed": false,
    "loss_type": "fire",
    "is_active": false,
    "source_detail": "kitchen grease fire",
    "call_outcome": "emergency_dispatch",
    "call_summary": "Kitchen fire damage, no active flames. Homeowner is home alone.",
    "insurance_carrier": "Allstate",
    "life_safety_concern": false
  }'
```

Expected: you receive a confirmation SMS asking for your address. No dispatch yet.

#### Testing the address reply loop locally

To test the full reply flow locally, Twilio needs to reach your machine. Use [ngrok](https://ngrok.com):

```bash
# In a separate terminal
ngrok http 8000
# Copy the https URL, e.g. https://abc123.ngrok.io
```

In Twilio, set your inbound webhook to `https://abc123.ngrok.io/webhook/twilio/inbound`, then run the out-of-radius test above. When you receive the confirmation SMS, reply with a valid in-range address. You should receive a follow-up SMS confirming dispatch, and a dispatch SMS should arrive on the on-call phone.

You can also trigger the inbound hook directly without ngrok:

```bash
curl -X POST http://localhost:8000/webhook/twilio/inbound \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "From=%2B15125550001&Body=1247+Oak+Street+Austin+TX+78704"
```

Replace `%2B15125550001` with your URL-encoded phone number (the `callback_number` from the out-of-radius test). This simulates Twilio forwarding a reply.

#### Check what happened

```bash
curl "http://localhost:8000/calls/recent?limit=5"
```

Key fields to look at: `dispatch_sent`, `caller_confirmation_sent`, `pending_address_confirmation`, `address_validation_status`, `crm_job_id`, `error`.

---

### End-to-end test with the real Vapi agent

This tests the full production flow: real phone call → Vapi extracts structured data → webhook → dispatch.

**Prerequisites:**
- Service deployed on Render (or exposed via ngrok locally)
- Customer added to the database with the correct Vapi assistant ID
- Vapi webhook URL pointing at your service
- Twilio inbound webhook configured (for the address confirmation flow)

**Steps:**

1. **Call your Vapi phone number** — use the number attached to your assistant in the Vapi dashboard.

2. **Have the conversation** — tell the AI assistant you have an emergency (e.g. "I have a water leak at 1247 Oak Street, Austin"). Give your name and callback number when asked.

3. **After the call ends**, Vapi sends an end-of-call webhook to `/webhook/vapi`. This triggers the pipeline.

4. **Check your phone** — within a few seconds you should receive either:
   - A dispatch SMS (if the address was in range)
   - A confirmation SMS asking you to reply with your address (if it wasn't)

5. **Check the logs** in the Render dashboard — filter by your call ID. Each step is logged.

6. **Check the database:**
   ```bash
   # Render shell
   sqlite3 /opt/render/project/src/data/dispatcher.db \
     "SELECT vapi_call_id, caller_name, address_validation_status, dispatch_sent, error FROM calls ORDER BY received_at DESC LIMIT 3;"
   ```

**Simulating the Vapi webhook locally** (without making a real call):

```bash
curl -X POST http://localhost:8000/webhook/vapi \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "call": {
        "id": "vapi-test-001",
        "assistantId": "ace1775b-6b7a-44cd-a3e1-6612601e0e33"
      },
      "analysis": {
        "structuredData": {
          "call_outcome": "emergency_dispatch",
          "life_safety_concern": false,
          "caller_name": "Sarah Chen",
          "callback_number": "+17037750484",
          "address_full": "44110 Ashburn Shopping Plaza, Ashburn, VA 20147",
          "address_confirmed": true,
          "loss_type": "water",
          "is_active": true,
          "source_detail": "dishwasher drain line",
          "water_clean_or_dirty": "clean",
          "call_summary": "Active water leak from dishwasher. Homeowner is home."
        }
      }
    }
  }'
```

An unknown `assistantId` returns `{"status":"ignored"}` — that's how you know the routing is wrong.

---

## Onboarding a client

Follow these steps every time you sign up a new restoration company. Target time: 30 minutes.

### Step 1 — Create their Vapi assistant

1. In the Vapi dashboard, duplicate your existing assistant or create a new one.
2. In the system prompt, replace the company name with the client's name (e.g. "You are the after-hours dispatch assistant for **Acme Restoration**...").
3. In **Analysis → Structured Data Schema**, paste the schema below. This tells Vapi exactly what to extract from every call.
4. Under **Server URL**, set the webhook to:
   ```
   https://your-render-url.onrender.com/webhook/vapi
   ```
5. Save and copy the **Assistant ID** — you'll need it in Step 3.

<details>
<summary>Structured data schema (click to expand)</summary>

```json
{
  "type": "object",
  "properties": {
    "call_outcome": { "type": "string", "enum": ["emergency_dispatch", "non_emergency_callback"] },
    "life_safety_concern": { "type": "boolean" },
    "caller_name": { "type": "string" },
    "callback_number": { "type": "string" },
    "address_full": { "type": "string" },
    "address_confirmed": { "type": "boolean" },
    "loss_type": { "type": "string", "enum": ["water", "fire", "smoke", "mold", "other"] },
    "is_active": { "type": "boolean" },
    "source_detail": { "type": "string" },
    "water_clean_or_dirty": { "type": "string", "enum": ["clean", "dirty", "unknown", "not_applicable"] },
    "access_notes": { "type": "string" },
    "insurance_carrier": { "type": "string" },
    "callback_reason": { "type": "string" },
    "call_summary": { "type": "string" }
  },
  "required": ["call_outcome", "life_safety_concern", "caller_name", "callback_number", "address_full", "loss_type", "call_summary"]
}
```
</details>

### Step 2 — Set up a phone number

**Option A — Dedicated Twilio number (recommended):**
1. Buy a new number in Twilio.
2. In the Vapi dashboard → **Phone Numbers** → **Import**, connect the Twilio number and assign it to the client's assistant.
3. This becomes the client's after-hours line. Their main number forwards to this after hours (Step 4).

**Option B — Client keeps their number:**
Some VoIP providers (RingCentral, Grasshopper) support SIP forwarding to Vapi directly. The client's existing number stays as-is.

### Step 3 — Add the client to the dispatcher database

On the Render shell:
```bash
DATABASE_PATH=/opt/render/project/src/data/dispatcher.db python scripts/add_customer.py
```

Fill in:
- **Company name** — appears in SMS messages sent to callers ("Hi, this is **Acme Restoration**'s dispatch line...")
- **On-call phone** — the tech's mobile that receives the dispatch SMS
- **Service hub lat/lng** — their warehouse or main office (Google Maps → right-click → copy coordinates)
- **Service radius** — their normal service area in miles
- **Vapi assistant ID** — from Step 1
- **CRM** — skip for now; add once the core flow is confirmed working

### Step 4 — Configure after-hours call forwarding on the client's end

This is the one-time setup that ties everything together. The client sets their business line to forward to the Vapi number after hours:

| Phone system | Where to set it |
|---|---|
| RingCentral | Admin Portal → Phone System → Call Handling → After Hours → Forward to [Vapi number] |
| Google Voice | Settings → Calls → Forward calls → set schedule |
| Grasshopper | Settings → Call Forwarding → Business Hours |
| Standard cell / landline | Dial `*72[Vapi number]` to enable forwarding, `*73` to cancel |

**The result:** During business hours, calls ring their front desk as normal. After hours and weekends, calls automatically forward to the Vapi assistant. Their customers still call the same number they always have.

### Step 5 — Verify Twilio inbound SMS webhook

Make sure your Twilio number's inbound webhook is set (once, not per client):
```
https://your-render-url.onrender.com/webhook/twilio/inbound
```

### Step 6 — Test before going live

1. Call the Vapi number and report an active water loss at a nearby address.
2. Verify the dispatch SMS arrives on the on-call phone within seconds of the call ending.
3. Check Render logs for any errors.
4. Test the out-of-range path: give a far address, confirm you receive the address confirmation SMS, reply with a valid address, confirm dispatch fires.

```bash
curl "https://your-render-url.onrender.com/calls/recent?limit=1"
```

### Switching between business hours and after hours

Call forwarding runs on a schedule the client sets once — no manual action needed each day. Overrides:

- **Close early / go to after-hours now:** forward calls immediately from their phone system dashboard
- **Come in on a weekend:** disable forwarding from their phone system, or dial `*73`
- **Tech is busy but AI should still capture the job:** the AI always handles intake; the tech just gets the SMS whenever they're free

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
   DATABASE_PATH=/opt/render/project/src/data/dispatcher.db python scripts/add_customer.py
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

## Configuring Twilio inbound SMS

When a caller's address can't be verified (out of range or invalid), the service texts them asking for their correct address. When they reply, Twilio needs to forward that reply to this service — otherwise the reply goes nowhere.

**What happens end-to-end:**
1. Call comes in → address is out of range or invalid
2. Service texts caller: *"Can you reply with your full address including city and ZIP?"*
3. Caller replies with their address
4. Twilio forwards the reply to `/webhook/twilio/inbound`
5. Service re-validates the address
6. If in range → dispatches tech + texts caller *"We're sending a technician"*
7. If still out of range → texts caller *"Sorry, outside our service area"*
8. If can't verify → texts caller *"Please call us directly"*

**Steps to configure in Twilio:**

1. Log in to [console.twilio.com](https://console.twilio.com).
2. Go to **Phone Numbers** → **Manage** → **Active Numbers**.
3. Click the phone number you use as `TWILIO_FROM_NUMBER`.
4. Under **Messaging Configuration**, find **"A message comes in"**.
5. Set the webhook URL to:
   ```
   https://your-render-url.onrender.com/webhook/twilio/inbound
   ```
6. Set the HTTP method to **POST**.
7. Save.

That's it. No new env vars needed — the service already has your Twilio credentials.

**To test the inbound flow locally**, use [ngrok](https://ngrok.com) to expose your local server:
```bash
ngrok http 8000
# Copy the https URL, e.g. https://abc123.ngrok.io
# Set Twilio inbound webhook to https://abc123.ngrok.io/webhook/twilio/inbound
# Send an SMS to your Twilio number and watch the logs
```

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
-- All customers and their CRM config
SELECT id, name, vapi_assistant_id, crm_type FROM customers;

-- Recent calls with dispatch and CRM status
SELECT vapi_call_id, received_at, call_outcome, address_validation_status,
       dispatch_sent, crm_job_id, error
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

### Updating a customer's CRM config

```sql
UPDATE customers SET crm_type = 'jobnimbus',
  crm_config = '{"api_key":"new-key-here"}' WHERE id = 1;
```

### Address validation tiers

| Distance | Outcome |
|---|---|
| ≤ radius | Dispatch SMS sent to on-call tech |
| radius – 1.5× radius | Dispatch SMS sent with "OUTSIDE NORMAL SERVICE RADIUS" warning |
| > 1.5× radius | Caller confirmation SMS sent; no dispatch until address confirmed |
| Invalid/incomplete | Caller confirmation SMS sent; no dispatch |

### Logs

Render captures all stdout. Filter by call ID in the Render log viewer. Each step logs at INFO level with the call ID and customer name. CRM push results also appear here.
