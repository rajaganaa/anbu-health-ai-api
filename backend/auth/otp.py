"""
auth/otp.py — Phone OTP authentication via MSG91 + Redis
Redis for production OTP storage (survives restarts, works across pods)
Falls back to in-memory if Redis not configured (DEV MODE)
"""

import os
import time
import random
import json
import logging
import httpx

logger = logging.getLogger(__name__)

_PLACEHOLDER = {"disabled", "placeholder", "changeme", "none", "null", ""}

MSG91_SEND_URL   = "https://api.msg91.com/api/v5/otp"
MSG91_VERIFY_URL = "https://api.msg91.com/api/v5/otp/verify"

OTP_TTL_SECONDS  = int(os.environ.get("OTP_TTL_SECONDS", "300"))
MSG91_AUTH_KEY   = os.environ.get("MSG91_AUTH_KEY", "").strip()
MSG91_TEMPLATE_ID= os.environ.get("MSG91_TEMPLATE_ID", "").strip()
MSG91_SENDER_ID  = os.environ.get("MSG91_SENDER_ID", "ANBUHC").strip()
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


def _configured(value: str) -> bool:
    return bool(value) and value.strip().lower() not in _PLACEHOLDER


def _is_dev_mode() -> bool:
    key = os.environ.get("MSG91_AUTH_KEY", "").strip()
    tid = os.environ.get("MSG91_TEMPLATE_ID", "").strip()
    return not (_configured(key) and _configured(tid))


def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def _mobile(phone: str) -> str:
    digits = phone.strip().lstrip("+")
    if digits.startswith("91") and len(digits) == 12:
        return digits
    return f"91{digits[-10:]}"


# ── Redis or memory store helpers ─────────────────────────────────────────────

def _store_otp(phone: str, otp: str):
    data = {"otp": otp, "sent_at": time.time(), "expires": time.time() + OTP_TTL_SECONDS}
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


# ── Public API ────────────────────────────────────────────────────────────────

def send_otp(phone: str) -> dict:
    phone = phone.strip().lstrip("+")
    if phone.startswith("91"):
        phone = phone[2:]
    if len(phone) != 10 or not phone.isdigit():
        return {"success": False, "error": "Invalid phone number — must be 10 digits"}

    record = _get_otp(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}

    otp = _gen_otp()

    if _is_dev_mode():
        _store_otp(phone, otp)
        logger.warning(f"[OTP][DEV] +91{phone} = {otp} | Redis={'yes' if _redis else 'no'}")
        return {"success": True, "dev_mode": True, "message": "OTP sent (dev mode)"}

    try:
        resp = httpx.post(
            MSG91_SEND_URL,
            json={
                "authkey": os.environ.get("MSG91_AUTH_KEY", "").strip(),
                "template_id": os.environ.get("MSG91_TEMPLATE_ID", "").strip(),
                "mobile": _mobile(phone),
                "sender": os.environ.get("MSG91_SENDER_ID", "ANBUHC").strip(),
                "otp_length": 6,
                "otp_expiry": OTP_TTL_SECONDS // 60,
                "channel": "SMS",
            },
            timeout=15,
        )
        data = resp.json()
        logger.info(f"[OTP] MSG91 +91{phone}: {data}")
        if resp.status_code == 200 and data.get("type") == "success":
            _store_otp(phone, otp)
            return {"success": True, "message": "OTP sent via SMS"}
        return {"success": False, "error": data.get("message", "MSG91 send failed")}
    except Exception as e:
        logger.error(f"[OTP] MSG91 failed: {e}")
        return {"success": False, "error": "SMS gateway error — try again"}


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

    if _is_dev_mode():
        _delete_otp(phone)
        return {"success": True, "dev_mode": True}

    if record.get("otp") != otp:
        return {"success": False, "error": "Wrong OTP. Try again."}

    _delete_otp(phone)
    return {"success": True}


def resend_otp(phone: str) -> dict:
    phone = phone.strip().lstrip("+")
    if phone.startswith("91"):
        phone = phone[2:]
    record = _get_otp(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}
    return send_otp(phone)
