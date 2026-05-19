#!/usr/bin/env python3
"""
Add a customer row to the database.

Usage:
    python scripts/add_customer.py \
        --name "Acme Restoration" \
        --on-call-phone "+15125550001" \
        --owner-phone "+15125550002" \
        --lat 30.2672 \
        --lng -97.7431 \
        --radius 30 \
        --assistant-id "your-vapi-assistant-id"
"""
import argparse
import os
import sqlite3
import sys

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Add a customer to the dispatcher DB")
    parser.add_argument("--name", required=True, help="Company name, e.g. 'Acme Restoration'")
    parser.add_argument("--on-call-phone", required=True, help="On-call tech phone (E.164, e.g. +15125550001)")
    parser.add_argument("--owner-phone", default=None, help="Owner phone for escalations (E.164)")
    parser.add_argument("--lat", required=True, type=float, help="Service center latitude")
    parser.add_argument("--lng", required=True, type=float, help="Service center longitude")
    parser.add_argument("--radius", required=True, type=int, help="Service radius in miles")
    parser.add_argument("--assistant-id", required=True, help="Vapi assistant ID (from Vapi dashboard)")
    args = parser.parse_args()

    db_path = os.getenv("DATABASE_PATH", "./data/dispatcher.db")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO customers
               (name, on_call_phone, owner_phone,
                service_center_lat, service_center_lng,
                service_radius_mi, vapi_assistant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                args.name,
                args.on_call_phone,
                args.owner_phone,
                args.lat,
                args.lng,
                args.radius,
                args.assistant_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, vapi_assistant_id FROM customers WHERE vapi_assistant_id = ?",
            (args.assistant_id,),
        ).fetchone()
        print(f"Customer added: id={row[0]}, name='{row[1]}', assistant_id='{row[2]}'")
        print(f"Use customer_id={row[0]} in /test/dispatch requests.")
    except sqlite3.IntegrityError as e:
        print(f"Error: {e}")
        print("A customer with that vapi_assistant_id may already exist.")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
