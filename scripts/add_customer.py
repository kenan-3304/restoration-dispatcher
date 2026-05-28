#!/usr/bin/env python3
"""
Add a customer to the dispatcher database.

Run with no arguments for interactive mode (recommended):
    python scripts/add_customer.py

Or pass all flags for scripting:
    python scripts/add_customer.py \
        --name "Acme Restoration" \
        --on-call-phone "+15125550001" \
        --lat 30.2672 --lng -97.7431 \
        --radius 30 \
        --assistant-id "your-vapi-assistant-id"
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    return os.getenv("DATABASE_PATH", "./data/dispatcher.db")


def _ensure_db(db_path: str):
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


def _migrate(conn: sqlite3.Connection):
    """Add any columns introduced after the initial schema."""
    for stmt in (
        "ALTER TABLE customers ADD COLUMN crm_type TEXT",
        "ALTER TABLE customers ADD COLUMN crm_config TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _insert(conn: sqlite3.Connection, name, on_call_phone, owner_phone,
            lat, lng, radius, assistant_id, crm_type, crm_config):
    conn.execute(
        """INSERT INTO customers
           (name, on_call_phone, owner_phone,
            service_center_lat, service_center_lng,
            service_radius_mi, vapi_assistant_id,
            crm_type, crm_config)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, on_call_phone, owner_phone, lat, lng, radius,
         assistant_id, crm_type, crm_config),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, name, vapi_assistant_id FROM customers WHERE vapi_assistant_id = ?",
        (assistant_id,),
    ).fetchone()
    print(f"\n✓ Customer added: id={row[0]}, name='{row[1]}', assistant_id='{row[2]}'")
    if crm_type:
        print(f"  CRM: {crm_type}")
    print(f"  Use customer_id={row[0]} in /test/dispatch requests.")


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

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


def _collect_crm() -> tuple[str | None, str | None]:
    print("\n--- CRM Integration (optional) ---")
    crm_type = _prompt("CRM type — servicetitan / jobnimbus / none", default="none").lower()

    if crm_type in ("none", ""):
        return None, None

    if crm_type == "servicetitan":
        print("\n--- ServiceTitan Credentials ---")
        print("  Find these in your ServiceTitan developer portal.\n")
        config: dict = {
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
        print("\n--- JobNimbus Credentials ---")
        print("  Find your API key in JobNimbus → Settings → API.\n")
        api_key = _prompt("API Key")
        return "jobnimbus", json.dumps({"api_key": api_key})

    print(f"  Unknown CRM type '{crm_type}' — skipping CRM setup.")
    return None, None


def run_interactive():
    db_path = _get_db_path()
    print("\n=== Restoration Dispatcher — Add Customer ===")
    print(f"  Database: {db_path}\n")

    name = _prompt("Company name (e.g. Acme Restoration)")
    on_call_phone = _prompt("On-call phone in E.164 format (e.g. +15125550001)")
    owner_phone = _prompt("Owner/escalation phone (optional, Enter to skip)", required=False) or None
    lat = float(_prompt("Service hub latitude (decimal, e.g. 30.2672)"))
    lng = float(_prompt("Service hub longitude (decimal, e.g. -97.7431)"))
    radius = int(_prompt("Service radius in miles", default="30"))
    assistant_id = _prompt("Vapi assistant ID (from Vapi dashboard → Assistants → copy ID)")

    crm_type, crm_config = _collect_crm()

    _ensure_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        _migrate(conn)
        _insert(conn, name, on_call_phone, owner_phone, lat, lng, radius,
                assistant_id, crm_type, crm_config)
    except sqlite3.IntegrityError as e:
        print(f"\nError: {e}")
        print("A customer with that vapi_assistant_id may already exist.")
        sys.exit(1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI flag mode
# ---------------------------------------------------------------------------

def run_cli():
    parser = argparse.ArgumentParser(description="Add a customer to the dispatcher DB")
    parser.add_argument("--name", required=True)
    parser.add_argument("--on-call-phone", required=True)
    parser.add_argument("--owner-phone", default=None)
    parser.add_argument("--lat", required=True, type=float)
    parser.add_argument("--lng", required=True, type=float)
    parser.add_argument("--radius", required=True, type=int)
    parser.add_argument("--assistant-id", required=True)
    parser.add_argument("--crm-type", default=None, choices=["servicetitan", "jobnimbus"],
                        help="CRM to integrate with (optional)")
    parser.add_argument("--crm-config", default=None,
                        help='CRM credentials as JSON string, e.g. \'{"api_key":"abc"}\'')
    args = parser.parse_args()

    crm_config = args.crm_config
    if crm_config:
        try:
            json.loads(crm_config)  # validate
        except json.JSONDecodeError as e:
            print(f"Error: --crm-config is not valid JSON: {e}")
            sys.exit(1)

    db_path = _get_db_path()
    _ensure_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        _migrate(conn)
        _insert(conn, args.name, args.on_call_phone, args.owner_phone,
                args.lat, args.lng, args.radius, args.assistant_id,
                args.crm_type, crm_config)
    except sqlite3.IntegrityError as e:
        print(f"Error: {e}")
        print("A customer with that vapi_assistant_id may already exist.")
        sys.exit(1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) == 1:
        run_interactive()
    else:
        run_cli()


if __name__ == "__main__":
    main()
