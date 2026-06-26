-- ============================================================================
-- Anbu Health AI — DOCUMENT VAULT SCHEMA (fixes vision↔Groq context loss)
-- Run this in: Supabase Dashboard → SQL Editor → New query → Run
--
-- PROBLEM THIS FIXES:
--   GPT-4o vision extraction (lab/scan/medicine) was only ever held in the
--   browser tab's React state (fileVault). It was never written to Supabase,
--   so it disappeared on page reload, login on a new session, or switching
--   to an older chat restored from history — at which point follow-up
--   questions fell back to the GENERAL Groq prompt with zero knowledge of
--   the document, producing answers that look "inconsistent" with the
--   vision extraction.
--
-- FIX:
--   Persist each successful vision extraction here, scoped to (phone,
--   chat_id), with a wall-clock expiry (NOT a token counter — a single
--   extraction is already 500-1500 tokens, so a 500-token budget would
--   re-trigger the same bug). main.py now reloads this on every follow-up
--   request instead of trusting only what the browser tab happened to send.
--
-- SCOPING NOTE: rows are namespaced by `phone`, which is the verified
-- identity already used for chat_history/prompt_usage. Anonymous (no-phone)
-- users are NOT persisted here — chat_id alone is not a safe key (the
-- frontend's default chat id is not guaranteed unique across browsers), so
-- anonymous continuity is handled client-side via localStorage instead
-- (see App.js). This avoids any risk of one anonymous user's medical data
-- being served to a different anonymous user who happens to share a
-- chat_id.
-- ============================================================================

CREATE TABLE IF NOT EXISTS document_vault (
  id           uuid primary key default gen_random_uuid(),
  phone        text not null references users(phone) on delete cascade,
  chat_id      text not null,
  file_key     text not null,        -- client-generated uuid, unique per upload (NOT filename — avoids collisions when two uploads share a name)
  mode         text not null,        -- lab | scan | medicine
  file_name    text,                 -- original filename, for display only
  vision_data  jsonb not null,       -- raw extraction, same shape as vision/anbu_vision.py output
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null default (now() + interval '24 hours'),
  unique (phone, chat_id, file_key)
);

CREATE INDEX IF NOT EXISTS idx_document_vault_lookup
  ON document_vault (phone, chat_id, expires_at);

ALTER TABLE document_vault ENABLE ROW LEVEL SECURITY;

-- Upsert helper: same (phone, chat_id, file_key) overwrites in place
-- (re-uploading the same file_key refreshes data + expiry rather than
-- duplicating). Called from Python via:
--   POST {SUPABASE_URL}/rest/v1/document_vault
--   header: Prefer: resolution=merge-duplicates
-- which is what db.save_document() already uses — this index just makes
-- that upsert possible.

-- Optional: periodic cleanup of expired rows (Supabase pg_cron, if enabled).
-- Safe to skip — main.py already filters on expires_at at read time, so
-- expired rows are simply never served even if this never runs.
-- select cron.schedule('purge_expired_document_vault', '0 * * * *',
--   $$ delete from document_vault where expires_at < now() $$);
