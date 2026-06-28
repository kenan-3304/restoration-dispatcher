#!/usr/bin/env python3
"""
Add a customer to the dispatcher via the admin API.

Set these before running:
    DISPATCHER_URL  — base URL of your server (default: http://localhost:8000)
    ADMIN_API_KEY   — the key you set on the server

Usage (interactive):
    DISPATCHER_URL=https://your-service.onrender.com ADMIN_API_KEY=secret python scripts/add_customer.py

Or non-interactive (all flags):
    python scripts/add_customer.py \\
        --name "Acme Restoration" \\
        --on-call-phone "+15125550001" \\
        --lat 30.2672 --lng -97.7431 --radius 30 \\
        --assistant-id "your-vapi-assistant-id"
"""
import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("DISPATCHER_URL", "http://localhost:8000").rstrip("/")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")


def _prompt(label: str, default: str = "", required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print("  (required — please enter a value)")


def _collect_crm() -> tuple:
    print("\n--- CRM Integration (optional) ---")
    crm_type = _prompt("CRM type — servicetitan / jobnimbus / none", default="none").lower()
    if crm_type in ("none", ""):
        return None, None
    if crm_type == "servicetitan":
        config = {
            "client_id": _prompt("Client ID"),
            "client_secret": _prompt("Client Secret"),
            "app_key": _prompt("App Key"),
            "tenant_id": _prompt("Tenant ID"),
        }
        job_type_id = _prompt("Job Type ID (optional, Enter to skip)", required=False)
        if job_type_id:
            config["job_type_id"] = job_type_id
        biz_unit = _prompt("Business Unit ID (optional, Enter to skip)", required=False)
        if biz_unit:
            config["business_unit_id"] = biz_unit
        return "servicetitan", json.dumps(config)
    if crm_type == "jobnimbus":
        return "jobnimbus", json.dumps({"api_key": _prompt("API Key")})
    print(f"  Unknown CRM type '{crm_type}' — skipping.")
    return None, None


def _call_api(payload: dict):
    if not ADMIN_KEY:
        print("Error: ADMIN_API_KEY env var is not set.")
        sys.exit(1)
    r = httpx.post(
        f"{BASE}/admin/customers",
        json=payload,
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        print(f"\n✓ Customer added: id={data['id']}, name='{data['name']}'")
        print(f"  Use customer_id={data['id']} in /test/dispatch requests.")
    elif r.status_code == 409:
        print(f"\nError: A customer with that assistant_id already exists.")
        sys.exit(1)
    else:
        print(f"\nError {r.status_code}: {r.text}")
        sys.exit(1)


def run_interactive():
    print(f"\n=== Restoration Dispatcher — Add Customer ===")
    print(f"  Server: {BASE}\n")
    name = _prompt("Company name (e.g. Acme Restoration)")
    on_call_phone = _prompt("On-call phone in E.164 format (e.g. +15125550001)")
    owner_phone = _prompt("Owner/escalation phone (optional, Enter to skip)", required=False) or None
    lat = float(_prompt("Service hub latitude (decimal, e.g. 30.2672)"))
    lng = float(_prompt("Service hub longitude (decimal, e.g. -97.7431)"))
    radius = int(_prompt("Service radius in miles", default="30"))
    assistant_id = _prompt("Vapi assistant ID (from Vapi dashboard → Assistants → copy ID)")
    crm_type, crm_config = _collect_crm()
    _call_api({
        "name": name, "on_call_phone": on_call_phone, "owner_phone": owner_phone,
        "lat": lat, "lng": lng, "radius": radius, "assistant_id": assistant_id,
        "crm_type": crm_type, "crm_config": crm_config,
    })


def run_cli():
    parser = argparse.ArgumentParser(description="Add a customer via the dispatcher admin API")
    parser.add_argument("--name", required=True)
    parser.add_argument("--on-call-phone", required=True)
    parser.add_argument("--owner-phone", default=None)
    parser.add_argument("--lat", required=True, type=float)
    parser.add_argument("--lng", required=True, type=float)
    parser.add_argument("--radius", required=True, type=int)
    parser.add_argument("--assistant-id", required=True)
    parser.add_argument("--crm-type", default=None, choices=["servicetitan", "jobnimbus"])
    parser.add_argument("--crm-config", default=None, help='CRM credentials as JSON string')
    args = parser.parse_args()
    if args.crm_config:
        try:
            json.loads(args.crm_config)
        except json.JSONDecodeError as e:
            print(f"Error: --crm-config is not valid JSON: {e}")
            sys.exit(1)
    _call_api({
        "name": args.name, "on_call_phone": args.on_call_phone, "owner_phone": args.owner_phone,
        "lat": args.lat, "lng": args.lng, "radius": args.radius,
        "assistant_id": args.assistant_id, "crm_type": args.crm_type, "crm_config": args.crm_config,
    })


def main():
    if len(sys.argv) == 1:
        run_interactive()
    else:
        run_cli()


if __name__ == "__main__":
    main()
