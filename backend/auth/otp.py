"""
auth/otp.py — Phone OTP authentication via Firebase Phone Auth (frontend)
Backend only validates Firebase ID tokens via firebase_auth.py.
This module is kept for any legacy /api/auth/send-otp or /api/auth/verify-otp
endpoints that still exist. In the current architecture, OTP is handled
entirely on the frontend via Firebase; this backend module acts as a
dev-mode fallback only.

Redis is used when available for cross-pod OTP storage (dev/legacy flows).
Falls back to in-memory if Redis is not configured.
"""

import os
import time
import random
import json
import logging

logger = logging.getLogger(__name__)

_PLACEHOLDER = {"disabled", "placeholder", "changeme", "none", "null", ""}

OTP_TTL_SECONDS  = int(os.environ.get("OTP_TTL_SECONDS", "300"))
MAX_OTP_ATTEMPTS = int(os.environ.get("MAX_OTP_ATTEMPTS", "5"))
REDIS_URL        = os.environ.get("REDIS_URL", "").strip()

# ── Redis setup (optional) ────────────────────────────────────────────────────
_redis = None
if REDIS_URL and REDIS_URL.lower() not in _PLACEHOLDER:
    try:
        import redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        logger.info("[OTP] Redis connected ✅")
    except Exception as e:
        logger.warning(f"[OTP] Redis connection failed, using in-memory: {e}")
        _redis = None

# Fallback in-memory store
_otp_store = {}


# Always dev mode — OTP is handled by Firebase on the frontend
DEV_MODE = True


def refresh_dev_mode() -> bool:
    return DEV_MODE


def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


# ── Redis or memory store helpers ─────────────────────────────────────────────

def _store_otp(phone: str, otp: str):
    data = {"otp": otp, "sent_at": time.time(), "expires": time.time() + OTP_TTL_SECONDS, "attempts": 0}
    if _redis:
        _redis.setex(f"otp:{phone}", OTP_TTL_SECONDS + 60, json.dumps(data))
    else:
        _otp_store[phone] = data


def _get_otp(phone: str) -> dict | None:
    if _redis:
        raw = _redis.get(f"otp:{phone}")
        return json.loads(raw) if raw else None
    return _otp_store.get(phone)


def _delete_otp(phone: str):
    if _redis:
        _redis.delete(f"otp:{phone}")
    else:
        _otp_store.pop(phone, None)


def _record_failed_attempt(phone: str, record: dict):
    record["attempts"] = record.get("attempts", 0) + 1
    ttl_remaining = max(1, int(record.get("expires", time.time()) - time.time()))
    if _redis:
        _redis.setex(f"otp:{phone}", ttl_remaining + 60, json.dumps(record))
    else:
        _otp_store[phone] = record


# ── Public API ────────────────────────────────────────────────────────────────

def send_otp(phone: str) -> dict:
    """Dev-mode OTP sender. In production, OTP is handled by Firebase on the
    frontend. This endpoint is retained for legacy compatibility."""
    phone = phone.strip().lstrip("+")
    if phone.startswith("91"):
        phone = phone[2:]
    if len(phone) != 10 or not phone.isdigit():
        return {"success": False, "error": "Invalid phone number — must be 10 digits"}

    record = _get_otp(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}

    otp = _gen_otp()
    _store_otp(phone, otp)
    logger.warning(f"[OTP][DEV] +91{phone} = {otp} | Redis={'yes' if _redis else 'no'}")
    return {"success": True, "dev_mode": True, "message": "OTP sent (dev mode — Firebase handles production OTP)"}


def verify_otp(phone: str, otp: str) -> dict:
    phone = phone.strip().lstrip("+")
    if phone.startswith("91"):
        phone = phone[2:]

    if len(otp) != 6 or not otp.isdigit():
        return {"success": False, "error": "Enter a 6-digit OTP"}

    record = _get_otp(phone)
    if not record:
        return {"success": False, "error": "No OTP requested. Send OTP first."}
    if time.time() > record.get("expires", 0):
        _delete_otp(phone)
        return {"success": False, "error": "OTP expired. Request a new one."}

    if record.get("attempts", 0) >= MAX_OTP_ATTEMPTS:
        _delete_otp(phone)
        logger.warning(f"[OTP] +91{phone} locked out after {MAX_OTP_ATTEMPTS} failed attempts")
        return {"success": False, "error": "Too many wrong attempts. Request a new OTP."}

    # Dev mode — always accept in dev
    _delete_otp(phone)
    return {"success": True, "dev_mode": True}


def resend_otp(phone: str) -> dict:
    phone = phone.strip().lstrip("+")
    if phone.startswith("91"):
        phone = phone[2:]
    record = _get_otp(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}
    return send_otp(phone)
