"""
Firebase Authentication helpers.

The browser signs in with Firebase Phone Auth and sends an ID token here.
The backend verifies the token with Firebase Admin before trusting the phone
number for prompt limits and chat history.
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_firebase_auth():
    import firebase_admin
    from firebase_admin import auth, credentials

    if not firebase_admin._apps:
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip() or None

        if service_account_json:
            cred = credentials.Certificate(json.loads(service_account_json))
            firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)
        else:
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)

        logger.info("[FIREBASE] Admin SDK initialized")

    return auth


def enabled() -> bool:
    return bool(os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))


def verify_id_token(id_token: str) -> dict:
    decoded = _get_firebase_auth().verify_id_token(id_token)
    phone = decoded.get("phone_number")
    if not phone:
        raise ValueError("Firebase token does not contain a verified phone number")
    return {
        "uid": decoded.get("uid"),
        "phone": phone.lstrip("+"),
        "phone_e164": phone,
        "claims": decoded,
    }


def bearer_token(request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    return auth_header.split(" ", 1)[1].strip()
