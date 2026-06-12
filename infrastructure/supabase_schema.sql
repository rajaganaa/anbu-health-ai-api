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
