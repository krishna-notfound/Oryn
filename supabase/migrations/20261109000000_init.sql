-- Oryn initial schema. Spec: docs/context/data-and-memory.md
-- RLS is enabled on every table. The browser (anon/authenticated roles) is limited to its own
-- rows; the backend uses the service-role key, which bypasses RLS, only after verifying the JWT.

-- ---------------------------------------------------------------- helpers

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------- profiles

create table public.profiles (
  id                uuid primary key references auth.users (id) on delete cascade,
  preferred_name    text,
  timezone          text        not null default 'Asia/Kolkata',
  age_range         text,
  occupation        text,
  primary_language  text        not null default 'en-IN',
  extra_languages   text[]      not null default '{}',
  response_language text        not null default 'auto',
  code_switching    boolean     not null default true,
  interests         text[]      not null default '{}',
  style             text        not null default 'friendly'
                    check (style in ('friendly', 'playful', 'calm', 'thoughtful', 'direct')),
  response_length   text        not null default 'short'
                    check (response_length in ('short', 'detailed')),
  listen_first      boolean     not null default true,
  formality         text        not null default 'informal'
                    check (formality in ('informal', 'formal')),
  voice             text,
  memory_enabled    boolean     not null default false,
  save_history      boolean     not null default true,
  onboarded_at      timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- Every new auth user gets an empty profile; onboarding fills it in.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id) values (new.id) on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- conversations

create table public.conversations (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid        not null references auth.users (id) on delete cascade,
  title      text,
  mode       text        not null check (mode in ('voice', 'text')),
  started_at timestamptz not null default now(),
  ended_at   timestamptz
);

create index conversations_user_started_idx on public.conversations (user_id, started_at desc);

-- ---------------------------------------------------------------- messages

create table public.messages (
  id              bigint generated always as identity primary key,
  conversation_id uuid        not null references public.conversations (id) on delete cascade,
  user_id         uuid        not null references auth.users (id) on delete cascade,
  role            text        not null check (role in ('user', 'assistant')),
  content         text        not null,
  interrupted     boolean     not null default false,
  created_at      timestamptz not null default now()
);

create index messages_conversation_created_idx on public.messages (conversation_id, created_at);
create index messages_user_idx on public.messages (user_id);

-- ---------------------------------------------------------------- consents (append-only audit trail)

create table public.consents (
  id         bigint generated always as identity primary key,
  user_id    uuid        not null references auth.users (id) on delete cascade,
  kind       text        not null check (kind in ('memory', 'history')),
  granted    boolean     not null,
  created_at timestamptz not null default now()
);

create index consents_user_created_idx on public.consents (user_id, created_at desc);

-- ---------------------------------------------------------------- row level security

alter table public.profiles      enable row level security;
alter table public.conversations enable row level security;
alter table public.messages      enable row level security;
alter table public.consents      enable row level security;

-- profiles: full control over the own row.
create policy profiles_select_own on public.profiles
  for select to authenticated using ((select auth.uid()) = id);
create policy profiles_insert_own on public.profiles
  for insert to authenticated with check ((select auth.uid()) = id);
create policy profiles_update_own on public.profiles
  for update to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);
create policy profiles_delete_own on public.profiles
  for delete to authenticated using ((select auth.uid()) = id);

-- conversations: full control over own rows.
create policy conversations_select_own on public.conversations
  for select to authenticated using ((select auth.uid()) = user_id);
create policy conversations_insert_own on public.conversations
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy conversations_update_own on public.conversations
  for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy conversations_delete_own on public.conversations
  for delete to authenticated using ((select auth.uid()) = user_id);

-- messages: readable and deletable by the owner. No insert/update policy: only the backend
-- (service role) writes messages, and only when profiles.save_history is on.
create policy messages_select_own on public.messages
  for select to authenticated using ((select auth.uid()) = user_id);
create policy messages_delete_own on public.messages
  for delete to authenticated using ((select auth.uid()) = user_id);

-- consents: append-only from the browser (no update/delete policy).
create policy consents_select_own on public.consents
  for select to authenticated using ((select auth.uid()) = user_id);
create policy consents_insert_own on public.consents
  for insert to authenticated with check ((select auth.uid()) = user_id);
