# Data and memory

Two layers: **Supabase Postgres** for application data, **SQLite** on the backend for extracted
long-term memories. Migrations: `supabase/migrations/*.sql`.

## Supabase tables (all RLS: `user_id = auth.uid()`; profiles: `id = auth.uid()`)

### `profiles` (1 row per user, written by onboarding/settings from the browser)

| column | type | notes |
| ------ | ---- | ----- |
| id | uuid pk → auth.users | |
| preferred_name | text | |
| timezone | text | default `Asia/Kolkata` |
| age_range | text null | optional |
| occupation | text null | optional |
| primary_language | text | Prisma/Timbre code, e.g. `hi-IN` |
| extra_languages | text[] | |
| response_language | text | `auto` = mirror user |
| code_switching | boolean | default true (Hinglish ok) |
| interests | text[] | |
| style | text | `friendly` `playful` `calm` `thoughtful` `direct` |
| response_length | text | `short` / `detailed` |
| listen_first | boolean | listen vs suggest solutions |
| formality | text | `informal` / `formal` |
| voice | text null | Timbre voice id |
| memory_enabled | boolean | default **false** until consent |
| save_history | boolean | default true |
| onboarded_at | timestamptz null | null → show onboarding |
| created_at / updated_at | timestamptz | |

### `conversations`
`id uuid pk, user_id uuid, title text, mode text ('voice'|'text'), started_at, ended_at null`

### `messages`
`id bigint identity pk, conversation_id uuid fk cascade, user_id uuid, role text ('user'|'assistant'),
content text, interrupted boolean default false, created_at` — written by the backend (service role)
only when `profiles.save_history`.

### `consents`
`id bigint pk, user_id uuid, kind text ('memory'|'history'), granted boolean, created_at` — append-only
audit trail; the current value lives in `profiles`.

The backend uses the service-role key **only after** verifying the JWT, and always filters by the
verified user id. The browser uses the anon key + RLS.

## SQLite memory (`MEMORY_DB_PATH`, persistent volume)

```sql
memories(seq INTEGER PK AUTOINCREMENT, id TEXT UNIQUE, user_id TEXT, content TEXT, category TEXT,
         confidence REAL, source_conversation_id TEXT, created_at TEXT, updated_at TEXT)
memories_fts  -- FTS5 over content, external content = memories (content_rowid = seq), trigger-synced
memories_vec  -- sqlite-vec vec0(user_id text partition key, embedding float[N]), rowid = memories.seq
```

`seq` is the stable rowid shared by the indexes (the implicit rowid of a TEXT-PK table can change
on VACUUM); `id` is the public uuid. The vector table is optional: if sqlite-vec cannot load or no
embedder is configured, retrieval degrades to FTS5 only.

- Categories: `preference`, `interest`, `goal`, `project`, `personal_context`, `explicit`.
- Retrieval = hybrid: FTS5 bm25 top-k ∪ vector top-k → reciprocal-rank fusion → top 5; always
  `WHERE user_id = ?`.
- Extraction runs after `assistant.done` as a background task, only if `memory_enabled`; it asks
  the LLM for JSON candidates, keeps confidence ≥ 0.7, skips sensitive categories (health,
  religion, sexuality, finances, credentials) unless the user said "remember this".
- Users can list / edit / delete memories (REST: `GET/PATCH/DELETE /memories`), and disabling
  memory stops retrieval and extraction immediately.
