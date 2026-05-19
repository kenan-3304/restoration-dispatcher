import aiosqlite
import logging
import os
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
    vapi_assistant_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vapi_call_id TEXT UNIQUE NOT NULL,
    customer_id INTEGER,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    caller_name TEXT,
    callback_number TEXT,
    property_address TEXT,
    loss_type TEXT,
    is_active BOOLEAN,
    source_of_loss TEXT,
    call_outcome TEXT,
    call_summary TEXT,
    water_category TEXT,
    rooms_affected TEXT,
    insurance_carrier TEXT,
    life_safety_concern BOOLEAN,
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

    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_calls_vapi_call_id ON calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_calls_received_at ON calls(received_at);
"""


async def init_db():
    db_dir = os.path.dirname(settings.DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.executescript(SCHEMA)
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
                    property_address, loss_type, is_active, source_of_loss,
                    call_outcome, call_summary, water_category, rooms_affected,
                    insurance_carrier, life_safety_concern, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vapi_call_id,
                    customer_id,
                    data.caller_name,
                    data.callback_number,
                    data.property_address,
                    data.loss_type,
                    data.is_active,
                    data.source_of_loss,
                    data.call_outcome,
                    data.call_summary,
                    data.water_category,
                    data.rooms_affected,
                    data.insurance_carrier,
                    data.life_safety_concern,
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


async def get_recent_calls(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM calls ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
