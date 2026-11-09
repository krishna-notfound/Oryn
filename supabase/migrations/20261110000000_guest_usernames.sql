-- Guest mode: every guest (Supabase anonymous user) picks a unique, public handle.
-- Uniqueness is case-insensitive; the format keeps handles URL- and speech-safe.
alter table public.profiles
  add column username text
  check (username ~ '^[A-Za-z0-9_]{3,20}$');

create unique index profiles_username_unique on public.profiles (lower(username));
