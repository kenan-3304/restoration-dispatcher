import aiosqlite
import logging
import os
import re
from typing import Optional

from app.config import settings
from app.models import StructuredData

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    on_call_phone TEXT NOT NULL,
    owner_phone TEXT,
    service_center_lat REAL NOT NULL,
    service_center_lng REAL NOT NULL,
    service_radius_mi INTEGER NOT NULL DEFAULT 30,
    vapi_assistant_id TEXT UNIQUE,
    crm_type TEXT,
    crm_config TEXT
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vapi_call_id TEXT UNIQUE NOT NULL,
    customer_id INTEGER,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    caller_name TEXT,
    callback_number TEXT,
    address_full TEXT,
    address_confirmed BOOLEAN,
    loss_type TEXT,
    is_active BOOLEAN,
    source_detail TEXT,
    water_clean_or_dirty TEXT,
    access_notes TEXT,
    call_outcome TEXT,
    call_summary TEXT,
    insurance_carrier TEXT,
    life_safety_concern BOOLEAN,
    callback_reason TEXT,
    raw_payload TEXT,

    address_validation_status TEXT,
    validated_address TEXT,
    distance_from_service_center_mi REAL,

    dispatch_sent BOOLEAN DEFAULT 0,
    dispatch_sent_at TIMESTAMP,
    dispatch_sid TEXT,

    caller_confirmation_sent BOOLEAN DEFAULT 0,
    caller_confirmation_sent_at TIMESTAMP,
    caller_confirmation_sid TEXT,
    pending_address_confirmation BOOLEAN DEFAULT 0,

    crm_job_id TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_calls_vapi_call_id ON calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_calls_received_at ON calls(received_at);
"""

# All schema migrations — safe to retry, errors are silently ignored (column already exists / already renamed)
_MIGRATIONS = [
    # CRM columns
    "ALTER TABLE customers ADD COLUMN crm_type TEXT",
    "ALTER TABLE customers ADD COLUMN crm_config TEXT",
    "ALTER TABLE calls ADD COLUMN crm_job_id TEXT",
    "ALTER TABLE calls ADD COLUMN pending_address_confirmation BOOLEAN DEFAULT 0",
    # Field renames (property_address→address_full, source_of_loss→source_detail, water_category→water_clean_or_dirty)
    "ALTER TABLE calls RENAME COLUMN property_address TO address_full",
    "ALTER TABLE calls RENAME COLUMN source_of_loss TO source_detail",
    "ALTER TABLE calls RENAME COLUMN water_category TO water_clean_or_dirty",
    # New fields
    "ALTER TABLE calls ADD COLUMN address_confirmed BOOLEAN",
    "ALTER TABLE calls ADD COLUMN access_notes TEXT",
    "ALTER TABLE calls ADD COLUMN callback_reason TEXT",
]


async def init_db():
    db_dir = os.path.dirname(settings.DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists
        await db.commit()
    logger.info("Database initialized at %s", settings.DATABASE_PATH)


async def get_customer_by_assistant_id(assistant_id: str) -> Optional[dict]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM customers WHERE vapi_assistant_id = ?",
            (assistant_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_customer_by_id(customer_id: int) -> Optional[dict]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM customers WHERE id = ?",
            (customer_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def insert_call(
    vapi_call_id: str,
    data: StructuredData,
    raw_payload: str,
    customer_id: Optional[int] = None,
) -> tuple[Optional[int], bool]:
    """Insert a call row. Returns (row_id, was_duplicate).
    If duplicate, returns (None, True)."""
    try:
        async with aiosqlite.connect(settings.DATABASE_PATH) as db:
            cursor = await db.execute(
                """INSERT INTO calls (
                    vapi_call_id, customer_id, caller_name, callback_number,
                    address_full, address_confirmed, loss_type, is_active,
                    source_detail, water_clean_or_dirty, access_notes,
                    call_outcome, call_summary, insurance_carrier,
                    life_safety_concern, callback_reason, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vapi_call_id,
                    customer_id,
                    data.caller_name,
                    data.callback_number,
                    data.address_full,
                    data.address_confirmed,
                    data.loss_type,
                    data.is_active,
                    data.source_detail,
                    data.water_clean_or_dirty,
                    data.access_notes,
                    data.call_outcome,
                    data.call_summary,
                    data.insurance_carrier,
                    data.life_safety_concern,
                    data.callback_reason,
                    raw_payload,
                ),
            )
            await db.commit()
            return cursor.lastrowid, False
    except aiosqlite.IntegrityError:
        logger.info("Duplicate call %s, skipping", vapi_call_id)
        return None, True


async def update_call(vapi_call_id: str, **kwargs):
    """Update arbitrary columns on a call row by vapi_call_id."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values())
    values.append(vapi_call_id)
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE calls SET {set_clause} WHERE vapi_call_id = ?",
            values,
        )
        await db.commit()


async def get_pending_call_by_phone(from_phone: str) -> Optional[dict]:
    """Find the most recent call awaiting address confirmation from this phone number.

    Matches on the last 10 digits so format differences (+1 prefix, spaces, etc.) don't matter.
    """
    from_digits = re.sub(r"\D", "", from_phone)
    if len(from_digits) < 10:
        return None
    from_tail = from_digits[-10:]

    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM calls
               WHERE pending_address_confirmation = 1 AND dispatch_sent = 0
               ORDER BY received_at DESC LIMIT 50"""
        )
        rows = await cursor.fetchall()

    for row in rows:
        stored = row["callback_number"] or ""
        stored_digits = re.sub(r"\D", "", stored)
        if stored_digits and stored_digits[-10:] == from_tail:
            return dict(row)
    return None


async def get_recent_calls(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM calls ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
