"""
auth/otp.py — MSG91 Widget Token Verification
============================================
Uses MSG91's OTP Widget (Web SDK) for client-side OTP.
Server-side: verifies the access_token returned by the widget.

Widget config (from MSG91 dashboard):
  Widget ID  : 36666e6b574a373434323634
  Token Auth : 524030TatR4smVLrr6s2e96b7P1

Flow:
  1. Client opens MSG91 widget → user enters phone → receives OTP → enters OTP
  2. Widget returns access_token to client on success
  3. Client sends access_token to /api/auth/verify-widget-token
  4. Backend verifies token with MSG91 API → gets phone number
  5. Backend creates/updates user record and returns session info
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

# ── MSG91 Widget Verification Endpoint ────────────────────────────────────────
MSG91_WIDGET_VERIFY_URL = "https://api.msg91.com/api/v5/widget/verifyAccessToken"
MSG91_TOKEN_AUTH        = os.environ.get("MSG91_TOKEN_AUTH", "524030TatR4smVLrr6s2e96b7P1").strip()
MSG91_WIDGET_ID         = os.environ.get("MSG91_WIDGET_ID", "36666e6b574a373434323634").strip()


async def verify_widget_token(access_token: str) -> dict:
    """
    Verify the MSG91 widget access_token server-side.
    Returns dict: { success, phone, message }
    Raises ValueError on failure.
    """
    headers = {
        "authkey": MSG91_TOKEN_AUTH,
        "Content-Type": "application/json",
    }
    payload = {
        "access_token": access_token,
        "widget_id":    MSG91_WIDGET_ID,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(MSG91_WIDGET_VERIFY_URL, json=payload, headers=headers)
            data = r.json()
            logger.info(f"[MSG91 Widget] verify response: {data}")

            if r.status_code == 200 and data.get("type") == "success":
                phone = data.get("message", {})
                if isinstance(phone, dict):
                    phone = phone.get("mobile", "") or phone.get("phone", "")
                elif isinstance(phone, str):
                    # some versions return phone directly in message
                    phone = phone
                return {"success": True, "phone": str(phone).replace("+91", "").replace("+", "").strip()}

            raise ValueError(data.get("message") or "Token verification failed")

    except httpx.RequestError as e:
        logger.error(f"[MSG91 Widget] HTTP error: {e}")
        raise ValueError(f"Network error verifying token: {e}")


# ── Legacy helpers (kept for backward compatibility — not used by widget flow) ──
def send_otp(phone: str):
    """Stub — no longer used; widget handles OTP sending client-side."""
    logger.info(f"[OTP] send_otp called for {phone} — no-op (widget mode)")
    return {"type": "success", "message": "Widget handles OTP sending"}


def verify_otp(phone: str, otp: str):
    """Stub — no longer used; widget handles OTP verification client-side."""
    logger.info(f"[OTP] verify_otp called for {phone} — no-op (widget mode)")
    return {"type": "success", "message": "Widget handles OTP verification"}


def resend_otp(phone: str):
    """Stub — no longer used; widget handles OTP resend client-side."""
    logger.info(f"[OTP] resend_otp called for {phone} — no-op (widget mode)")
    return {"type": "success", "message": "Widget handles OTP resend"}
