"""
db/supabase_client.py — Supabase integration

Uses Supabase's PostgREST API directly via httpx (no extra SDK dependency —
keeps the Docker image small). Handles:

  - users           — one row per phone number
  - prompt_usage    — per-day prompt counter (server-side enforced 20/day)
  - chat_history    — persisted chat messages per user

Env vars:
  SUPABASE_URL       — e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY — service_role key (Settings → API). Backend-only,
                         never expose to frontend — bypasses RLS.

If these are not set, all functions degrade gracefully (return safe
defaults) so the app keeps working with the old localStorage-only counter.

SQL schema to run once in Supabase SQL editor: see infrastructure/supabase_schema.sql
"""

import os
import logging
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_PLACEHOLDER = {"disabled", "placeholder", "changeme", "none", "null"}


def _configured(value: str) -> bool:
    return bool(value) and value.strip().lower() not in _PLACEHOLDER


SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MAX_PROMPTS_PER_DAY = int(os.environ.get("MAX_PROMPTS_PER_DAY", "20"))

ENABLED = _configured(SUPABASE_URL) and _configured(SUPABASE_KEY)

_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _rest(path: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{path}"


def is_enabled() -> bool:
    return ENABLED


# ── Users ────────────────────────────────────────────────────────────────────
def get_or_create_user(phone: str) -> dict:
    """Return user row, creating it if it doesn't exist."""
    if not ENABLED:
        return {"phone": phone, "id": None}
    try:
        r = httpx.get(_rest("users"), params={"phone": f"eq.{phone}", "select": "*"},
                       headers=_HEADERS, timeout=10)
        rows = r.json()
        if rows:
            # touch last_seen
            httpx.patch(_rest("users"), params={"phone": f"eq.{phone}"},
                        headers=_HEADERS,
                        json={"last_seen": datetime.now(timezone.utc).isoformat()}, timeout=10)
            return rows[0]

        r = httpx.post(_rest("users"), headers=_HEADERS,
                        json={"phone": phone,
                              "created_at": datetime.now(timezone.utc).isoformat(),
                              "last_seen": datetime.now(timezone.utc).isoformat()},
                        timeout=10)
        rows = r.json()
        return rows[0] if rows else {"phone": phone, "id": None}
    except Exception as e:
        logger.warning(f"[SUPABASE] get_or_create_user failed: {e}")
        return {"phone": phone, "id": None}


# ── Prompt limit (server-side, resets daily) ───────────────────────────────────
def get_prompt_status(phone: str) -> dict:
    """Return {count, remaining, limit, allowed} for today."""
    if not ENABLED:
        return {"count": 0, "remaining": MAX_PROMPTS_PER_DAY, "limit": MAX_PROMPTS_PER_DAY, "allowed": True}

    today = date.today().isoformat()
    try:
        r = httpx.get(_rest("prompt_usage"),
                       params={"phone": f"eq.{phone}", "usage_date": f"eq.{today}", "select": "count"},
                       headers=_HEADERS, timeout=10)
        rows = r.json()
        count = rows[0]["count"] if rows else 0
    except Exception as e:
        logger.warning(f"[SUPABASE] get_prompt_status failed: {e}")
        count = 0

    return {
        "count": count,
        "remaining": max(0, MAX_PROMPTS_PER_DAY - count),
        "limit": MAX_PROMPTS_PER_DAY,
        "allowed": count < MAX_PROMPTS_PER_DAY,
    }


def increment_prompt_count(phone: str) -> dict:
    """Atomically increment today's prompt count via a Postgres RPC function
    (increment_prompt_atomic — see infrastructure/supabase_schema.sql).

    This replaces the old read-then-write approach (GET count, +1 in Python,
    then UPSERT), which had a race condition: concurrent requests from the
    same phone could both read the same count and both get "allowed",
    letting users exceed MAX_PROMPTS_PER_DAY under load. The RPC does the
    read+increment+check as a single atomic DB operation.
    """
    if not ENABLED:
        return {"count": 1, "remaining": MAX_PROMPTS_PER_DAY - 1, "limit": MAX_PROMPTS_PER_DAY, "allowed": True}

    try:
        r = httpx.post(
            _rest("rpc/increment_prompt_atomic"),
            headers=_HEADERS,
            json={"p_phone": phone, "p_max_per_day": MAX_PROMPTS_PER_DAY},
            timeout=10,
        )
        rows = r.json()
        if rows:
            row = rows[0]
            return {
                "count": row["count"],
                "remaining": row["remaining"],
                "limit": row["limit"],
                "allowed": row["allowed"],
            }
        logger.warning(f"[SUPABASE] increment_prompt_atomic returned no rows: {r.text}")
    except Exception as e:
        logger.warning(f"[SUPABASE] increment_prompt_atomic failed: {e}")

    # Fail-open fallback: if the RPC call itself errors (network/Supabase
    # outage), don't block the user — fall back to a best-effort local read.
    status = get_prompt_status(phone)
    new_count = status["count"] + 1
    return {
        "count": new_count,
        "remaining": max(0, MAX_PROMPTS_PER_DAY - new_count),
        "limit": MAX_PROMPTS_PER_DAY,
        "allowed": new_count <= MAX_PROMPTS_PER_DAY,
    }


# ── Chat history ─────────────────────────────────────────────────────────────
def save_message(phone: str, role: str, content: str, mode: str = "general",
                  structured: dict = None, chat_id: str = None) -> None:
    if not ENABLED:
        return
    try:
        httpx.post(_rest("chat_history"), headers=_HEADERS, json={
            "phone": phone,
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "mode": mode,
            "structured": structured,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, timeout=10)
    except Exception as e:
        logger.warning(f"[SUPABASE] save_message failed: {e}")


def get_history(phone: str, limit: int = 50) -> list:
    if not ENABLED:
        return []
    try:
        r = httpx.get(_rest("chat_history"),
                       params={"phone": f"eq.{phone}", "select": "*",
                               "order": "created_at.desc", "limit": str(limit)},
                       headers=_HEADERS, timeout=10)
        rows = r.json()
        return list(reversed(rows))
    except Exception as e:
        logger.warning(f"[SUPABASE] get_history failed: {e}")
        return []
