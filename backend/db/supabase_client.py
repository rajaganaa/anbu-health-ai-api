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

