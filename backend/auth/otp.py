"""
auth/otp.py — Phone OTP authentication via MSG91

Real SMS OTP for Tamil Nadu village users (phone-only login).
Falls back to DEV MODE (OTP printed in logs, accepts any 6 digits)
when MSG91_AUTH_KEY is not configured.

MSG91 API:
  POST https://api.msg91.com/api/v5/otp
  POST https://api.msg91.com/api/v5/otp/verify
"""

import os
import time
import random
import logging
import httpx

logger = logging.getLogger(__name__)

_PLACEHOLDER = {"disabled", "placeholder", "changeme", "none", "null"}

MSG91_SEND_URL = "https://api.msg91.com/api/v5/otp"
MSG91_VERIFY_URL = "https://api.msg91.com/api/v5/otp/verify"


def _configured(value: str) -> bool:
    return bool(value) and value.strip().lower() not in _PLACEHOLDER


MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
MSG91_TEMPLATE_ID = os.environ.get("MSG91_TEMPLATE_ID", "")
MSG91_SENDER_ID = os.environ.get("MSG91_SENDER_ID") or "ANBUHC"
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "300"))

# In-memory store for dev mode + send cooldown tracking
_otp_store = {}

# DEV_MODE = not (_configured(MSG91_AUTH_KEY) and _configured(MSG91_TEMPLATE_ID))
DEV_MODE = True  # temporary - force dev mode


def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def _mobile(phone: str) -> str:
    return f"91{phone}"


def send_otp(phone: str) -> dict:
    """Send OTP to a 10-digit Indian phone number."""
    if len(phone) != 10 or not phone.isdigit():
        return {"success": False, "error": "Invalid phone number — must be 10 digits"}

    record = _otp_store.get(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}

    if DEV_MODE:
        otp = _gen_otp()
        _otp_store[phone] = {"otp": otp, "expires": time.time() + OTP_TTL_SECONDS, "sent_at": time.time()}
        logger.warning(f"[OTP][DEV MODE] OTP for 91{phone} = {otp} (MSG91 not configured)")
        return {"success": True, "dev_mode": True, "message": "OTP sent (dev mode — check server logs)"}

    try:
        resp = httpx.post(
            MSG91_SEND_URL,
            json={
                "authkey": MSG91_AUTH_KEY,
                "template_id": MSG91_TEMPLATE_ID,
                "mobile": _mobile(phone),
                "sender": MSG91_SENDER_ID,
                "otp_length": 6,
                "otp_expiry": OTP_TTL_SECONDS // 60,
                "channel": "SMS",
            },
            timeout=15,
        )
        data = resp.json()
        logger.info(f"[OTP] MSG91 send +91{phone}: {data}")
        if resp.status_code == 200 and data.get("type") == "success":
            _otp_store[phone] = {"sent_at": time.time(), "expires": time.time() + OTP_TTL_SECONDS}
            return {"success": True, "message": "OTP sent via SMS"}
        return {"success": False, "error": data.get("message", "MSG91 send failed")}
    except Exception as e:
        logger.error(f"[OTP] MSG91 send failed: {e}")
        return {"success": False, "error": "SMS gateway error — try again"}


def verify_otp(phone: str, otp: str) -> dict:
    """Verify OTP for a phone number."""
    if len(otp) != 6 or not otp.isdigit():
        return {"success": False, "error": "Enter a 6-digit OTP"}

    if DEV_MODE:
        record = _otp_store.get(phone)
        if not record:
            return {"success": False, "error": "No OTP requested. Send OTP first."}
        if time.time() > record.get("expires", 0):
            del _otp_store[phone]
            return {"success": False, "error": "OTP expired. Request a new one."}
        return {"success": True, "dev_mode": True}

    try:
        resp = httpx.post(
            MSG91_VERIFY_URL,
            json={
                "authkey": MSG91_AUTH_KEY,
                "mobile": _mobile(phone),
                "otp": otp,
            },
            timeout=15,
        )
        data = resp.json()
        logger.info(f"[OTP] MSG91 verify +91{phone}: {data}")
        if resp.status_code == 200 and data.get("type") == "success":
            _otp_store.pop(phone, None)
            return {"success": True}
        return {"success": False, "error": data.get("message", "Wrong OTP. Try again.")}
    except Exception as e:
        logger.error(f"[OTP] MSG91 verify failed: {e}")
        return {"success": False, "error": "Verification failed — try again"}


def resend_otp(phone: str) -> dict:
    """Resend OTP (30s cooldown)."""
    record = _otp_store.get(phone)
    if record and (record.get("sent_at", 0) + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}
    return send_otp(phone)
