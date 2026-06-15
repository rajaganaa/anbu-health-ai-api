"""
auth/routes.py — Authentication Routes for MSG91 Widget
=======================================================
Adds POST /api/auth/verify-widget-token

This is the ONLY endpoint needed for the Widget flow.
The client-side MSG91 widget handles:
  - Displaying the phone input
  - Sending OTP to the user
  - Displaying OTP input
  - Verifying OTP
  - Returning an access_token to our JS callback

We then call this endpoint to:
  - Verify the access_token server-side with MSG91
  - Get the authenticated phone number
  - Create / update the user in Supabase
  - Return session info to the frontend
"""

import logging
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse

from auth.otp import verify_widget_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/verify-widget-token")
async def verify_widget_token_endpoint(access_token: str = Form(...)):
    """
    Verify MSG91 Widget access_token server-side.

    Body (form-data):
      access_token: str  — the token returned by window.initSendOTP success callback

    Returns:
      { success: true, phone: "9876543210", user_id: "...", prompts: {...} }
    """
    if not access_token or not access_token.strip():
        raise HTTPException(status_code=400, detail="access_token is required")

    try:
        result = await verify_widget_token(access_token.strip())
        phone = result.get("phone", "")

        # Try to upsert user in Supabase (optional — graceful fallback)
        user_id = None
        prompts = None
        try:
            from db import supabase_client as db
            if phone:
                user = await db.upsert_user(phone)
                user_id = user.get("id") if user else None
                status = await db.get_user_status(phone)
                if status:
                    prompts = {
                        "count":     status.get("prompt_count", 0),
                        "remaining": max(0, 20 - status.get("prompt_count", 0)),
                        "limit":     20,
                        "allowed":   status.get("prompt_count", 0) < 20,
                    }
        except Exception as db_err:
            logger.warning(f"[AUTH] DB upsert skipped: {db_err}")

        return JSONResponse({
            "success":  True,
            "phone":    phone,
            "user_id":  user_id,
            "prompts":  prompts,
        })

    except ValueError as e:
        logger.warning(f"[AUTH] Widget token verification failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"[AUTH] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
