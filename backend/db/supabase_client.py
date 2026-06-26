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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_PLACEHOLDER = {"disabled", "placeholder", "changeme", "none", "null"}


def _configured(value: str) -> bool:
    return bool(value) and value.strip().lower() not in _PLACEHOLDER


SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MAX_TOKENS_PER_DAY = int(os.environ.get("MAX_TOKENS_PER_DAY", "50000"))
# Keep backward-compat alias used in older code paths
MAX_PROMPTS_PER_DAY = MAX_TOKENS_PER_DAY

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


# ── Compliance: DPDP Act 2023 — Consent (Section 6) ─────────────────────────────
# Tables created by infrastructure/compliance_schema.sql: user_consent,
# grievances, compliance_audit. These functions follow the same direct-REST
# pattern as the rest of this file (no supabase-py SDK dependency).
DOCUMENT_VAULT_TTL_HOURS = int(os.environ.get("DOCUMENT_VAULT_TTL_HOURS", "24"))


def save_document(phone: str, chat_id: str, file_key: str, mode: str,
                  vision_data: dict, file_name: str = None,
                  ttl_hours: int = None) -> bool:
    if not ENABLED or not phone or not vision_data:
        return False
    ttl = ttl_hours if ttl_hours is not None else DOCUMENT_VAULT_TTL_HOURS
    now = datetime.now(timezone.utc)
    try:
        headers = dict(_HEADERS)
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        httpx.post(
            _rest("document_vault"),
            headers=headers,
            params={"on_conflict": "phone,chat_id,file_key"},
            json={
                "phone": phone,
                "chat_id": chat_id or "default",
                "file_key": file_key,
                "mode": mode,
                "file_name": file_name,
                "vision_data": vision_data,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=ttl)).isoformat(),
            },
            timeout=10,
        )
        return True
    except Exception as e:
        logger.warning(f"[SUPABASE] save_document failed: {e}")
        return False


def get_document_vault(phone: str, chat_id: str) -> list:
    if not ENABLED or not phone:
        return []
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        r = httpx.get(_rest("document_vault"), params={
            "phone": f"eq.{phone}",
            "chat_id": f"eq.{chat_id or 'default'}",
            "expires_at": f"gt.{now_iso}",
            "select": "file_key,mode,file_name,vision_data,created_at",
            "order": "created_at.desc",
        }, headers=_HEADERS, timeout=10)
        return r.json() or []
    except Exception as e:
        logger.warning(f"[SUPABASE] get_document_vault failed: {e}")
        return []


def get_all_document_vaults(phone: str) -> dict:
    if not ENABLED or not phone:
        return {}
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        r = httpx.get(_rest("document_vault"), params={
            "phone": f"eq.{phone}",
            "expires_at": f"gt.{now_iso}",
            "select": "chat_id,file_key,mode,file_name,vision_data,created_at",
            "order": "created_at.desc",
        }, headers=_HEADERS, timeout=10)
        grouped = {}
        for row in r.json() or []:
            grouped.setdefault(row.get("chat_id") or "default", []).append(row)
        return grouped
    except Exception as e:
        logger.warning(f"[SUPABASE] get_all_document_vaults failed: {e}")
        return {}


def clear_document_vault(phone: str, chat_id: str, file_key: str = None) -> bool:
    if not ENABLED or not phone:
        return False
    try:
        params = {"phone": f"eq.{phone}", "chat_id": f"eq.{chat_id or 'default'}"}
        if file_key:
            params["file_key"] = f"eq.{file_key}"
        httpx.delete(_rest("document_vault"), params=params, headers=_HEADERS, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"[SUPABASE] clear_document_vault failed: {e}")
        return False


def record_consent(phone: str, consent_given: bool, consent_version: str = "1.0") -> bool:
    """Record a user's consent decision. DPDP Act 2023, Section 6
    (consent must be free, specific, informed, unconditional)."""
    if not ENABLED:
        return False
    try:
        httpx.post(_rest("user_consent"), headers=_HEADERS, json={
            "phone": phone,
            "consent_given": consent_given,
            "consent_version": consent_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, timeout=10)
        return True
    except Exception as e:
        logger.error(f"[SUPABASE] record_consent failed: {e}")
        return False


def has_consent(phone: str) -> bool:
    """Check the most recent consent record for this phone.
    Fails open (returns True) if Supabase is unreachable or unconfigured,
    so a transient DB issue never blocks a user who already has the app —
    but a phone with no consent record on file is treated as not consented."""
    if not ENABLED:
        return True
    try:
        r = httpx.get(_rest("user_consent"),
                       params={"phone": f"eq.{phone}", "select": "consent_given",
                               "order": "created_at.desc", "limit": "1"},
                       headers=_HEADERS, timeout=10)
        rows = r.json()
        if rows:
            return bool(rows[0]["consent_given"])
        return False
    except Exception as e:
        logger.warning(f"[SUPABASE] has_consent failed: {e}")
        return True


# ── Compliance: DPDP Act 2023 — Right to Erasure (Section 12) ──────────────────
def delete_all_user_data(phone: str) -> bool:
    """Delete every row for this phone across all tables (child tables first,
    then the parent `users` row). Returns False if any deletion failed, but
    still attempts the rest so a single table outage doesn't block the others."""
    if not ENABLED:
        return False
    ok = True
    child_tables = ["compliance_audit", "chat_history", "document_vault", "prompt_usage", "user_consent", "grievances", "otp_codes"]
    for table in child_tables:
        try:
            httpx.delete(_rest(table), params={"phone": f"eq.{phone}"}, headers=_HEADERS, timeout=10)
            logger.info(f"[SUPABASE] Deleted {table} rows for phone={phone[-4:]}****")
        except Exception as e:
            logger.warning(f"[SUPABASE] delete from {table} failed: {e}")
            ok = False
    try:
        httpx.delete(_rest("users"), params={"phone": f"eq.{phone}"}, headers=_HEADERS, timeout=10)
        logger.info(f"[SUPABASE] User record deleted for phone={phone[-4:]}****")
    except Exception as e:
        logger.error(f"[SUPABASE] delete users failed: {e}")
        ok = False
    return ok


# ── Compliance: IT Rules 2021 — Grievance redressal (Rule 4(2)) ────────────────
def save_grievance(grievance_id: str, phone: Optional[str], category: str, complaint: str) -> bool:
    """Persist a grievance complaint. IT Rules 2021, Rule 4(2) requires
    intermediaries to provide a grievance redressal mechanism."""
    if not ENABLED:
        return False
    try:
        httpx.post(_rest("grievances"), headers=_HEADERS, json={
            "grievance_id": grievance_id,
            "phone": phone,
            "category": category,
            "complaint": complaint,
            "status": "received",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, timeout=10)
        return True
    except Exception as e:
        logger.error(f"[SUPABASE] save_grievance failed: {e}")
        return False


# ── Compliance: audit trail (Telemedicine Guidelines 2020 + DPDP Act) ──────────
def log_compliance(request_id: str, phone: Optional[str], mode: str,
                    violations_found: bool, violation_types: list,
                    emergency_triggered: bool) -> bool:
    """Best-effort audit log of compliance gate outcomes per request. Never
    raises — a logging failure must not break the user-facing response."""
    if not ENABLED:
        return False
    try:
        httpx.post(_rest("compliance_audit"), headers=_HEADERS, json={
            "request_id": request_id,
            "phone": phone,
            "mode": mode,
            "violations_found": violations_found,
            "violation_types": violation_types,
            "emergency_triggered": emergency_triggered,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, timeout=10)
        return True
    except Exception as e:
        logger.debug(f"[SUPABASE] log_compliance failed (non-critical): {e}")
        return False


# ── Token-based quota aliases (Issue 4 fix) ────────────────────────────────────
def get_token_status(phone: str) -> dict:
    """Alias for get_prompt_status — checks daily token/prompt usage."""
    return get_prompt_status(phone)


def increment_token_count(phone: str, tokens_used: int = 0) -> dict:
    """Alias for increment_prompt_count that also accepts a token count.
    The token amount is logged but the underlying DB still tracks prompt rows.
    To track actual tokens, add a tokens_used column to prompt_usage and
    update the increment_prompt_atomic RPC accordingly."""
    result = increment_prompt_count(phone)
    if tokens_used:
        logger.info(f"[SUPABASE] +{tokens_used} tokens for {phone} (session total tracked locally)")
    return result
