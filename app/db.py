import asyncpg
import logging
import re
from typing import Optional

from app.config import settings
from app.models import StructuredData

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        on_call_phone TEXT NOT NULL,
        owner_phone TEXT,
        service_center_lat REAL NOT NULL,
        service_center_lng REAL NOT NULL,
        service_radius_mi INTEGER NOT NULL DEFAULT 30,
        vapi_assistant_id TEXT UNIQUE,
        crm_type TEXT,
        crm_config TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calls (
        id SERIAL PRIMARY KEY,
        vapi_call_id TEXT UNIQUE NOT NULL,
        customer_id INTEGER,
        received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
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
        dispatch_sent BOOLEAN DEFAULT FALSE,
        dispatch_sent_at TIMESTAMPTZ,
        dispatch_sid TEXT,
        caller_confirmation_sent BOOLEAN DEFAULT FALSE,
        caller_confirmation_sent_at TIMESTAMPTZ,
        caller_confirmation_sid TEXT,
        pending_address_confirmation BOOLEAN DEFAULT FALSE,
        crm_job_id TEXT,
        error TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calls_vapi_call_id ON calls(vapi_call_id)",
    "CREATE INDEX IF NOT EXISTS idx_calls_received_at ON calls(received_at)",
]

_MIGRATIONS = [
    "ALTER TABLE calls ALTER COLUMN received_at TYPE TIMESTAMPTZ USING received_at AT TIME ZONE 'UTC'",
    "ALTER TABLE calls ALTER COLUMN dispatch_sent_at TYPE TIMESTAMPTZ USING dispatch_sent_at AT TIME ZONE 'UTC'",
    "ALTER TABLE calls ALTER COLUMN caller_confirmation_sent_at TYPE TIMESTAMPTZ USING caller_confirmation_sent_at AT TIME ZONE 'UTC'",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS crm_type TEXT",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS crm_config TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS crm_job_id TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS pending_address_confirmation BOOLEAN DEFAULT FALSE",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS address_confirmed BOOLEAN",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS access_notes TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS callback_reason TEXT",
]


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        for stmt in _SCHEMA_STMTS:
            await conn.execute(stmt)
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(stmt)
            except Exception:
                pass
    logger.info("Database initialized")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()


async def get_customer_by_assistant_id(assistant_id: str) -> Optional[dict]:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM customers WHERE vapi_assistant_id = $1",
            assistant_id,
        )
        return dict(row) if row else None


async def get_customer_by_id(customer_id: int) -> Optional[dict]:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM customers WHERE id = $1",
            customer_id,
        )
        return dict(row) if row else None


async def create_customer(
    name: str,
    on_call_phone: str,
    owner_phone: Optional[str],
    lat: float,
    lng: float,
    radius: int,
    assistant_id: str,
    crm_type: Optional[str] = None,
    crm_config: Optional[str] = None,
) -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO customers
               (name, on_call_phone, owner_phone,
                service_center_lat, service_center_lng, service_radius_mi,
                vapi_assistant_id, crm_type, crm_config)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING *""",
            name, on_call_phone, owner_phone, lat, lng, radius,
            assistant_id, crm_type, crm_config,
        )
        return dict(row)


async def list_customers() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, on_call_phone, owner_phone,
                      service_center_lat, service_center_lng, service_radius_mi,
                      vapi_assistant_id, crm_type
               FROM customers ORDER BY id"""
        )
        return [dict(row) for row in rows]


async def insert_call(
    vapi_call_id: str,
    data: StructuredData,
    raw_payload: str,
    customer_id: Optional[int] = None,
) -> tuple[Optional[int], bool]:
    try:
        async with _pool.acquire() as conn:
            row_id = await conn.fetchval(
                """INSERT INTO calls (
                    vapi_call_id, customer_id, caller_name, callback_number,
                    address_full, address_confirmed, loss_type, is_active,
                    source_detail, water_clean_or_dirty, access_notes,
                    call_outcome, call_summary, insurance_carrier,
                    life_safety_concern, callback_reason, raw_payload
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                RETURNING id""",
                vapi_call_id, customer_id, data.caller_name, data.callback_number,
                data.address_full, data.address_confirmed, data.loss_type, data.is_active,
                data.source_detail, data.water_clean_or_dirty, data.access_notes,
                data.call_outcome, data.call_summary, data.insurance_carrier,
                data.life_safety_concern, data.callback_reason, raw_payload,
            )
            return row_id, False
    except asyncpg.UniqueViolationError:
        logger.info("Duplicate call %s, skipping", vapi_call_id)
        return None, True


async def update_call(vapi_call_id: str, **kwargs):
    if not kwargs:
        return
    set_clauses = [f"{k} = ${i}" for i, k in enumerate(kwargs.keys(), start=1)]
    values = list(kwargs.values())
    values.append(vapi_call_id)
    async with _pool.acquire() as conn:
        await conn.execute(
            f"UPDATE calls SET {', '.join(set_clauses)} WHERE vapi_call_id = ${len(values)}",
            *values,
        )


async def get_pending_call_by_phone(from_phone: str) -> Optional[dict]:
    from_digits = re.sub(r"\D", "", from_phone)
    if len(from_digits) < 10:
        return None
    from_tail = from_digits[-10:]

    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM calls
               WHERE pending_address_confirmation = TRUE AND dispatch_sent = FALSE
               ORDER BY received_at DESC LIMIT 50"""
        )

    for row in rows:
        stored = row["callback_number"] or ""
        stored_digits = re.sub(r"\D", "", stored)
        if stored_digits and stored_digits[-10:] == from_tail:
            return dict(row)
    return None


async def get_recent_calls(limit: int = 20) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM calls ORDER BY received_at DESC LIMIT $1",
            limit,
        )
        return [dict(row) for row in rows]
