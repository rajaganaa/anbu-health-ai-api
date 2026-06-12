"""
auth/otp.py — Phone OTP authentication via MSG91

Real SMS OTP for Tamil Nadu village users (phone-only login).
Falls back to a DEV MODE (OTP printed in logs, accepts any 6 digits)
when MSG91_AUTH_KEY is not configured — so local/dev testing never breaks.

MSG91 docs: https://docs.msg91.com/p/tf9GTu0t/e/Vlhddqxh/MSG91

Env vars:
  MSG91_AUTH_KEY     — Authkey from msg91.com dashboard (required for real SMS)
  MSG91_TEMPLATE_ID  — OTP template ID created in MSG91 dashboard
  MSG91_SENDER_ID    — 6-char sender ID (e.g. "ANBUHL"), default ANBUHL
  OTP_TTL_SECONDS    — OTP validity window, default 300 (5 min)
"""

import os
import time
import random
import logging
import httpx

logger = logging.getLogger(__name__)

MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
MSG91_TEMPLATE_ID = os.environ.get("MSG91_TEMPLATE_ID", "")
MSG91_SENDER_ID = os.environ.get("MSG91_SENDER_ID") or "ANBUHL"
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "300"))
MSG91_BASE = "https://control.msg91.com/api/v5/otp"

# In-memory store: { phone: {"otp": "123456", "expires": ts, "verified": bool} }
# NOTE: this resets on container restart/scale-to-zero. For multi-replica
# production, move this to Supabase/Redis (see db/supabase_client.py — the
# `otp_codes` table is provided for that purpose).
_otp_store = {}

DEV_MODE = not (MSG91_AUTH_KEY and MSG91_TEMPLATE_ID)


def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def send_otp(phone: str) -> dict:
    """Send OTP to a 10-digit Indian phone number. Returns status dict."""
    if len(phone) != 10 or not phone.isdigit():
        return {"success": False, "error": "Invalid phone number — must be 10 digits"}

    otp = _gen_otp()
    _otp_store[phone] = {
        "otp": otp,
        "expires": time.time() + OTP_TTL_SECONDS,
        "verified": False,
        "attempts": 0,
    }

    if DEV_MODE:
        logger.warning(f"[OTP][DEV MODE] OTP for +91{phone} = {otp} (MSG91 not configured)")
        return {"success": True, "dev_mode": True, "message": "OTP sent (dev mode — check server logs)"}

    try:
        resp = httpx.get(
            MSG91_BASE,
            params={
                "template_id": MSG91_TEMPLATE_ID,
                "mobile": f"91{phone}",
                "authkey": MSG91_AUTH_KEY,
                "otp": otp,
                "sender": MSG91_SENDER_ID,
                "otp_expiry": OTP_TTL_SECONDS // 60,
            },
            timeout=10,
        )
        data = resp.json()
        logger.info(f"[OTP] MSG91 response for +91{phone}: {data.get('type')}")
        if data.get("type") == "success":
            return {"success": True, "message": "OTP sent via SMS"}
        return {"success": False, "error": data.get("message", "MSG91 send failed")}
    except Exception as e:
        logger.error(f"[OTP] MSG91 send failed: {e}")
        return {"success": False, "error": "SMS gateway error — try again"}


def verify_otp(phone: str, otp: str) -> dict:
    """Verify a previously sent OTP. Returns {success, error?}."""
    record = _otp_store.get(phone)
    if not record:
        return {"success": False, "error": "No OTP requested for this number. Send OTP first."}

    if time.time() > record["expires"]:
        del _otp_store[phone]
        return {"success": False, "error": "OTP expired. Request a new one."}

    record["attempts"] += 1
    if record["attempts"] > 5:
        del _otp_store[phone]
        return {"success": False, "error": "Too many attempts. Request a new OTP."}

    # DEV MODE: accept any 6-digit code so local testing keeps working
    if DEV_MODE:
        if len(otp) == 6 and otp.isdigit():
            record["verified"] = True
            return {"success": True, "dev_mode": True}
        return {"success": False, "error": "Enter a 6-digit code"}

    if otp != record["otp"]:
        return {"success": False, "error": "Wrong OTP. Try again."}

    record["verified"] = True
    return {"success": True}


def resend_otp(phone: str) -> dict:
    """Resend — same as send_otp but checks a short cooldown."""
    record = _otp_store.get(phone)
    if record and (record["expires"] - OTP_TTL_SECONDS + 30) > time.time():
        return {"success": False, "error": "Please wait 30s before resending"}
    return send_otp(phone)
