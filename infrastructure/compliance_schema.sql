-- ============================================================================
-- Anbu Health AI — COMPLIANCE SCHEMA ADDITIONS
-- Run this in: Supabase Dashboard → SQL Editor → New query → Run
-- 
-- Adds tables required by:
--   DPDP Act 2023        → user_consent, compliance_audit
--   IT Rules 2021        → grievances
--   Telemedicine 2020    → compliance_audit
-- ============================================================================

-- 1. USER CONSENT TABLE
--    DPDP Act 2023, Section 6: Consent must be free, specific, informed.
--    Record every time a user gives/withdraws consent.
--    NOTE: phone has NO foreign key to users(phone) — the consent screen is
--    shown BEFORE login/OTP verification (frontend posts phone="anonymous"
--    at that point), so the referenced user row doesn't exist yet. A FK here
--    would make every anonymous consent insert fail silently.
CREATE TABLE IF NOT EXISTS user_consent (
  id               uuid primary key default gen_random_uuid(),
  phone            text not null,
  consent_given    boolean not null,
  consent_version  text not null default '1.0',
  ip_address       text,
  created_at       timestamptz default now()
);

CREATE INDEX IF NOT EXISTS idx_user_consent_phone ON user_consent (phone, created_at DESC);
ALTER TABLE user_consent ENABLE ROW LEVEL SECURITY;

-- 2. GRIEVANCES TABLE  
--    IT Rules 2021, Rule 4(2): Grievance redressal mechanism required.
--    Acknowledge within 24 hours, resolve within 15 working days.
CREATE TABLE IF NOT EXISTS grievances (
  id            uuid primary key default gen_random_uuid(),
  grievance_id  text unique not null,       -- e.g. AHA-A1B2C3D4
  phone         text,                        -- may be null (anonymous complaint)
  category      text not null default 'general',  -- harmful_advice | data_privacy | wrong_info | other
  complaint     text not null,
  status        text not null default 'received', -- received | under_review | resolved
  resolved_at   timestamptz,
  created_at    timestamptz default now()
);

CREATE INDEX IF NOT EXISTS idx_grievances_status ON grievances (status, created_at DESC);
ALTER TABLE grievances ENABLE ROW LEVEL SECURITY;

-- 3. COMPLIANCE AUDIT LOG
--    Telemedicine Guidelines 2020 + DPDP Act: Keep audit trail.
CREATE TABLE IF NOT EXISTS compliance_audit (
  id                  uuid primary key default gen_random_uuid(),
  request_id          text not null,
  phone               text,
  mode                text,
  violations_found    boolean not null default false,
  violation_types     text[],
  emergency_triggered boolean not null default false,
  created_at          timestamptz default now()
);

CREATE INDEX IF NOT EXISTS idx_compliance_audit_phone ON compliance_audit (phone, created_at DESC);
ALTER TABLE compliance_audit ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- ADD THESE FUNCTIONS to supabase_client.py (Python side):
-- ============================================================================
-- db.record_consent(phone, consent_given, consent_version)
--   → INSERT INTO user_consent (phone, consent_given, consent_version)
--
-- db.delete_all_user_data(phone)
--   → DELETE FROM chat_history WHERE phone = ?
--   → DELETE FROM prompt_usage WHERE phone = ?
--   → DELETE FROM user_consent WHERE phone = ?
--   → DELETE FROM otp_codes WHERE phone = ?
--   → DELETE FROM users WHERE phone = ?
--
-- db.save_grievance(grievance_id, phone, category, complaint)
--   → INSERT INTO grievances (grievance_id, phone, category, complaint)
--
-- db.log_compliance(request_id, phone, mode, violations_found, violation_types, emergency_triggered)
--   → INSERT INTO compliance_audit (...)
-- ============================================================================
