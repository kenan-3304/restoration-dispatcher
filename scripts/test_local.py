#!/usr/bin/env python3
"""
Local integration test runner.

Usage:
    python scripts/test_local.py

Runs every dispatch path against the local server and prints a clear pass/fail
for each test. Sends real SMS via Twilio and real address validation via Google,
so make sure your .env is loaded and the server is running:

    uvicorn app.main:app --reload
"""
import json
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx
from dotenv import load_dotenv
load_dotenv()

BASE = "http://localhost:8000"
CUSTOMER_ID = 1  # Kenan / Ashburn VA test customer
PHONE = os.getenv("TEST_PHONE", "+17037760484")  # override with TEST_PHONE=+1... python scripts/test_local.py

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m→\033[0m"


def check(label: str, call_id: str, expected: dict):
    time.sleep(1.5)  # let background task finish
    r = httpx.get(f"{BASE}/calls/recent?limit=20")
    calls = {c["vapi_call_id"]: c for c in r.json()["calls"]}
    if call_id not in calls:
        print(f"  {FAIL} DB row missing for {call_id}")
        return
    row = calls[call_id]
    failures = []
    for field, want in expected.items():
        got = row.get(field)
        if got != want:
            failures.append(f"{field}: expected {want!r}, got {got!r}")
    if failures:
        for f in failures:
            print(f"  {FAIL} {f}")
        if row.get("error"):
            print(f"  {INFO} error column: {row['error']}")
    else:
        print(f"  {PASS} {label}")


def post(path: str, body: dict) -> dict:
    r = httpx.post(f"{BASE}{path}", json=body, timeout=10)
    return r.json()


def _clear_test_rows():
    import asyncio
    import asyncpg

    async def _clear():
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            print("  (DATABASE_URL not set — skipping test row cleanup)")
            return 0
        conn = await asyncpg.connect(db_url)
        result = await conn.execute("DELETE FROM calls WHERE vapi_call_id LIKE 't-%'")
        await conn.close()
        return int(result.split()[-1])

    n = asyncio.run(_clear())
    if n:
        print(f"  (cleared {n} leftover test rows from previous run)\n")


def run():
    print("\n=== Restoration Dispatcher — Local Test Suite ===\n")
    _clear_test_rows()

    # ------------------------------------------------------------------
    print("1. Health check")
    r = httpx.get(f"{BASE}/healthz")
    if r.json() == {"status": "ok"}:
        print(f"  {PASS} server is up")
    else:
        print(f"  {FAIL} unexpected response: {r.text}")
        sys.exit(1)

    # ------------------------------------------------------------------
    print("\n2. In-radius dispatch (expect dispatch SMS on your phone)")
    post("/test/dispatch", {
        "call_id": "t-in-radius",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "emergency_dispatch",
        "caller_name": "Sarah Chen",
        "caller_phone": PHONE,
        "address_full": "21000 Dulles Town Circle, Dulles, VA 20166",
        "loss_type": "water",
        "is_active": "yes",
        "source_detail": "dishwasher drain line",
        "call_summary": "Active water leak from dishwasher in kitchen. Homeowner present. Two kids home. Water reached dining room carpet.",
    })
    check("dispatch_sent=True, in_radius", "t-in-radius", {
        "dispatch_sent": True,
        "address_validation_status": "in_radius",
        "caller_confirmation_sent": False,
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n3. Non-emergency (expect no SMS)")
    post("/test/dispatch", {
        "call_id": "t-non-emergency",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "non_emergency",
        "caller_name": "Bob Smith",
        "caller_phone": PHONE,
        "address_full": "21000 Dulles Town Circle, Dulles, VA 20166",
        "loss_type": "mold",
        "is_active": "no",
        "call_summary": "Non-urgent mold issue. Caller wants a callback during business hours.",
    })
    check("no dispatch, no confirmation", "t-non-emergency", {
        "dispatch_sent": False,
        "caller_confirmation_sent": False,
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n4. Borderline address (expect dispatch SMS with radius warning)")
    post("/test/dispatch", {
        "call_id": "t-borderline",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "emergency_dispatch",
        "caller_name": "Mike Johnson",
        "caller_phone": PHONE,
        "address_full": "15 N Loudoun St, Winchester, VA 22601",
        "loss_type": "fire",
        "is_active": "no",
        "source_detail": "kitchen grease fire",
        "call_summary": "Fire damage to kitchen, no active flames. Homeowner alone.",
    })
    check("dispatch_sent=True, borderline", "t-borderline", {
        "dispatch_sent": True,
        "address_validation_status": "borderline",
        "caller_confirmation_sent": False,
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n5. Life safety redirect (expect no dispatch — caller was redirected to 911)")
    post("/test/dispatch", {
        "call_id": "t-life-safety",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "life_safety_redirect",
        "caller_name": "Janet Liu",
        "caller_phone": PHONE,
        "address_full": "21000 Dulles Town Circle, Dulles, VA 20166",
        "loss_type": "fire",
        "is_active": "yes",
        "source_detail": "electrical panel fire",
        "call_summary": "Active electrical fire near panel. Elderly resident alone, unclear if she has exited. Redirected to 911.",
    })
    check("no dispatch, no confirmation", "t-life-safety", {
        "dispatch_sent": False,
        "caller_confirmation_sent": False,
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n6. No address provided (expect dispatch SMS with NO ADDRESS)")
    post("/test/dispatch", {
        "call_id": "t-no-address",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "emergency_dispatch",
        "caller_name": "Unknown Caller",
        "caller_phone": PHONE,
        "address_full": "",
        "loss_type": "water",
        "is_active": "yes",
        "source_detail": "unknown",
        "call_summary": "Caller reported active flooding but could not confirm address before call dropped.",
    })
    # error="No property address provided" is expected — the system notes it but dispatches anyway
    check("dispatch_sent=True, address skipped", "t-no-address", {
        "dispatch_sent": True,
        "address_validation_status": "skipped",
    })

    # ------------------------------------------------------------------
    print("\n7. Out-of-radius (expect confirmation SMS on your phone — do NOT reply yet)")
    post("/test/dispatch", {
        "call_id": "t-out-of-radius",
        "customer_id": CUSTOMER_ID,
        "call_outcome": "emergency_dispatch",
        "caller_name": "Tom Davis",
        "caller_phone": PHONE,
        "address_full": "100 N Charles St, Baltimore, MD 21201",
        "loss_type": "water",
        "is_active": "yes",
        "source_detail": "burst pipe",
        "call_summary": "Active burst pipe, significant flooding. Homeowner is home.",
    })
    check("confirmation SMS sent, pending reply", "t-out-of-radius", {
        "dispatch_sent": False,
        "caller_confirmation_sent": True,
        "pending_address_confirmation": True,
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n8. Address reply loop (simulating your reply to the confirmation SMS)")
    r = httpx.post(
        f"{BASE}/webhook/twilio/inbound",
        data={"From": PHONE, "Body": "21000 Dulles Town Circle Dulles VA 20166"},
        timeout=10,
    )
    print(f"  {INFO} TwiML ack received (status {r.status_code})")
    time.sleep(2)
    check("dispatched after address reply", "t-out-of-radius", {
        "dispatch_sent": True,
        "pending_address_confirmation": False,
        "address_validation_status": "in_radius",
        "error": None,
    })

    # ------------------------------------------------------------------
    print("\n9. Duplicate call ID (expect no second SMS, idempotency check)")
    post("/test/dispatch", {
        "call_id": "t-in-radius",  # same ID as test 2
        "customer_id": CUSTOMER_ID,
        "call_outcome": "emergency_dispatch",
        "caller_name": "Sarah Chen",
        "caller_phone": PHONE,
        "address_full": "21000 Dulles Town Circle, Dulles, VA 20166",
        "loss_type": "water",
        "call_summary": "Duplicate — should be ignored.",
    })
    time.sleep(1)
    r = httpx.get(f"{BASE}/calls/recent?limit=50")
    duplicates = [c for c in r.json()["calls"] if c["vapi_call_id"] == "t-in-radius"]
    if len(duplicates) == 1:
        print(f"  {PASS} only one row exists for t-in-radius (duplicate ignored)")
    else:
        print(f"  {FAIL} found {len(duplicates)} rows for t-in-radius (expected 1)")

    # ------------------------------------------------------------------
    print("\n=== Summary ===")
    r = httpx.get(f"{BASE}/calls/recent?limit=20")
    rows = {c["vapi_call_id"]: c for c in r.json()["calls"]}
    for call_id, row in sorted(rows.items()):
        if not call_id.startswith("t-"):
            continue
        status = "dispatched" if row["dispatch_sent"] else ("confirmed→pending" if row["pending_address_confirmation"] else ("confirmation sent" if row["caller_confirmation_sent"] else "logged only"))
        err = f" ⚠ {row['error']}" if row.get("error") else ""
        print(f"  {call_id:<22} {row['address_validation_status'] or 'n/a':<14} {status}{err}")

    print()


if __name__ == "__main__":
    run()
