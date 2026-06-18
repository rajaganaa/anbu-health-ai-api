-- Anbu Health AI — Supabase schema
-- Run this once in Supabase Dashboard → SQL Editor → New query → Run

-- 1. Users (one row per phone number)
create table if not exists users (
  id          uuid primary key default gen_random_uuid(),
  phone       text unique not null,
  created_at  timestamptz default now(),
  last_seen   timestamptz default now()
);

-- 2. Prompt usage — server-side enforced 20/day limit
create table if not exists prompt_usage (
  id          uuid primary key default gen_random_uuid(),
  phone       text not null references users(phone) on delete cascade,
  usage_date  date not null default current_date,
  count       int not null default 0,
  unique (phone, usage_date)
);

-- 3. Chat history
create table if not exists chat_history (
  id          uuid primary key default gen_random_uuid(),
  phone       text not null references users(phone) on delete cascade,
  chat_id     text,
  role        text not null,           -- 'user' | 'assistant'
  content     text,
  mode        text,                    -- lab | scan | medicine | general
  structured  jsonb,
  created_at  timestamptz default now()
);

create index if not exists idx_chat_history_phone on chat_history (phone, created_at);
create index if not exists idx_prompt_usage_phone_date on prompt_usage (phone, usage_date);

-- 4. (Optional) OTP codes — only needed if you want OTPs persisted across
--    container restarts / multiple replicas instead of in-memory storage.
create table if not exists otp_codes (
  phone       text primary key,
  otp         text not null,
  expires_at  timestamptz not null,
  attempts    int default 0,
  verified    boolean default false
);

-- Row Level Security: backend uses the service_role key which bypasses RLS,
-- so RLS can stay enabled with no public policies (frontend never talks to
-- Supabase directly).
alter table users enable row level security;
alter table prompt_usage enable row level security;
alter table chat_history enable row level security;
alter table otp_codes enable row level security;

-- =============================================================================
-- ATOMIC prompt counter (fixes race condition)
--
-- Old approach in Python was: SELECT count -> count+1 in app code -> UPSERT.
-- Under concurrent requests from the same phone (e.g. double-tap, retry,
-- multiple devices), two requests can both read count=19, both decide
-- "allowed", and both write count=20 — but TWO prompts went through past
-- the limit. This function does the read+check+increment as ONE atomic
-- database operation, so Postgres serializes concurrent calls correctly.
--
-- Call from Python via Supabase RPC:
--   POST {SUPABASE_URL}/rest/v1/rpc/increment_prompt_atomic
--   body: {"p_phone": "...", "p_max_per_day": 20}
-- =============================================================================
create or replace function increment_prompt_atomic(
  p_phone text,
  p_max_per_day int default 20
) returns table (
  count int,
  remaining int,
  "limit" int,
  allowed boolean
) language plpgsql as $$
declare
  new_count int;
begin
  insert into prompt_usage (phone, usage_date, count)
  values (p_phone, current_date, 1)
  on conflict (phone, usage_date)
  do update set count = prompt_usage.count + 1
  returning prompt_usage.count into new_count;

  return query select
    new_count,
    greatest(0, p_max_per_day - new_count),
    p_max_per_day,
    (new_count <= p_max_per_day);
end;
$$;
