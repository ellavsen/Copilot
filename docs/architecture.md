# Architecture

How the pieces fit, and why they are shaped this way. Written for someone reading the
repository for the first time.

## Request lifecycle

A message from an artist travels like this:

1. **`bot/handlers.py`** resolves the Telegram user to an `Artist` row (creating it on
   first contact) and wraps the rest of the turn in `artist_scope(...)`.
2. **`agents/graph.py`** awaits `graph.ainvoke(...)`. The supervisor reads the message,
   picks a specialist, and hands off using LangGraph's built-in handoff tools.
3. The specialist runs its ReAct loop, calling tools from **`tools/`**.
4. Tools open their own database session, read the artist from the context variable, and
   write through **`db/repo.py`**.
5. `extract_answer(...)` picks the specialist's reply out of the message list and the
   handler sends it back, split into Telegram-sized chunks.

There is exactly one LLM entry point and exactly one graph.

## Why the custom router was removed

The previous build did this:

```python
sup_result = GRAPH.invoke({"messages": [...]})       # synchronous, inside async
for msg in sup_result["messages"]:                   # hunt for {"next": "..."}
    ...
agent_res = agent.invoke({"messages": [...]})        # invoke the agent a second time
```

Three separate problems:

- `langgraph_supervisor` delegates through **handoff tools**, so it never emits
  `{"next": ...}`. `agent_name` stayed empty and the bot answered
  "🤔 Не понял запроса" for essentially every free-text message.
- When it did find a name, the work was done twice — the supervisor had already run the
  specialist, and its answer was discarded.
- `.invoke()` is synchronous. Called from an async handler it blocks the event loop, so
  one slow request froze the bot for every user at once.

The fix was deletion, not more code: use the framework's handoff mechanism and `await`.

### The supervisor's closing turn

After a specialist answers, control returns to the supervisor, which would normally
paraphrase the answer — a second LLM call that can only make the expert wording worse.
The supervisor prompt therefore instructs it to reply with a single sentinel word, and
`extract_answer` prefers the specialist's message and drops the sentinel. The artist gets
the specialist's exact words.

## Identity: a context variable, not a tool argument

Tools take no `user_id`. The bot binds an `ArtistContext` to the async task before the
graph runs:

```python
with artist_scope(artist):
    answer = await copilot.ask(text, thread_id=f"artist-{artist.artist_id}-{n}")
```

Every tool calls `current_artist()`. Consequences:

- The model cannot address someone else's data, because it never supplies the identity.
- The model cannot forget to pass an id, because there is nothing to pass.
- A tool invoked outside a scope raises `NoArtistContextError` instead of silently
  operating on the wrong row.

All tools are `async def`, which keeps them on the same task as the scope. A synchronous
tool could be dispatched to a thread pool where the context would not follow.

## Data model

```
Artist ──1:1── ArtistProfile          progressive personalisation
   │
   ├──1:N── Deliverable               report | strategy | plan
   │            └── based_on_id ──► Deliverable      provenance chain
   │
   ├──1:N── OpenCallSubscription ──► OpenCall
   └──1:N── Reminder ──────────────► OpenCall
```

**One table for three deliverables.** The portfolio report, strategy and plan share a
lifecycle — generated, stored, superseded — so they share a table with a `kind`
discriminator. Versioning and history come for free, and `based_on_id` records which
strategy a plan was actually derived from. That is what makes "the plan sees the strategy"
a fact in the schema rather than a hope in a prompt.

**`UtcDateTime`.** SQLite has no timestamp type, so values return naive. The type
decorator stores UTC and re-attaches `tzinfo` on load, so application code never handles a
naive datetime.

**`OpenCall.search_blob`.** SQLite's `lower()` only folds ASCII: `lower('Живопись')` comes
back unchanged, so `lower(title) LIKE '%живопись%'` matches nothing. Every open call
therefore stores a pre-lowercased haystack computed in Python. This keeps the query
standard SQL (it works identically on PostgreSQL, where `lower()` is Unicode-aware),
avoids depending on driver internals, and is cheaper than folding on every read.

## Reminders

`ReminderRepository.schedule_for_call` writes one row per lead time, skipping any that are
already in the past, and is idempotent per `(artist, call, lead_days)`.

`ReminderService` sweeps for rows where `sent_at IS NULL AND fire_at <= now`, delivers
them, then marks them sent. Delivery is an injected callback, so the service is tested
without Telegram, and a send failure leaves the row unsent so the next sweep retries it.

Keeping the schedule in the database rather than in an in-process job queue is the whole
point: a restart or deploy cannot drop a deadline.

### Honesty about dates

Recurring competitions move their dates yearly, so the bundled catalogue resolves a
*typical* month and day to the next future occurrence and marks it `deadline_verified =
False`. Everywhere such a date appears it is rendered as an estimate with a link, and its
reminders say "time to go check" rather than "hurry up". Calls added by the artist are
verified, because the artist read them from the source.

## Retrieval

`rag/store.py` holds one lazily-constructed `KnowledgeBase`. The embedding model and FAISS
index load on first search, inside `asyncio.to_thread`, and results carry `file_name` and
`path_str` so answers can cite sources.

The heavy dependencies live in the `[rag]` extra. `KnowledgeBase.available()` checks both
that they are importable and that an index exists, and the tool returns an explanation
rather than failing when they are not — which is why the whole test suite runs without
torch.

`rag/ingest.py` reads a local folder by default. Google Drive is an optional source whose
folder ids and service-account path come from environment variables, so no credential and
no private folder id is ever in the source tree.

## Configuration

`config.py` is a `pydantic-settings` model. Nothing raises at import time — tests and
tooling can import it with no secrets present. `require_bot_token()` and
`require_openai_key()` fail loudly, with instructions, only when the value is actually
needed.

## Logging

The old build called `basicConfig` twice; the first call, at DEBUG, won, so production
logs contained full prompts, raw model output and the text of artists' CVs.
`logging_setup.py` configures the root logger once with `force=True`, pins chatty HTTP and
model libraries to WARNING, and prints a privacy warning if someone opts into DEBUG.

## Testing strategy

92 tests, no network, no API key, no `[rag]` extra.

- **Repositories** — versioning, per-artist isolation, idempotent upserts, reminder
  scheduling arithmetic.
- **Tools** — invoked through their real LangChain interface (`.ainvoke`), including the
  case where a tool must refuse: saving a plan with no strategy persists nothing.
- **Reminders** — delivery, no double-send, retry after a failed send, and a full
  simulated process restart against the same database file.
- **Graph wiring** — `extract_answer` against realistic message lists, plus assertions
  that the supervisor prompt does not ask for the JSON the framework never emits.
- **Catalogue** — every seeded deadline is unverified, every entry has a source link, and
  Cyrillic search is case-insensitive.

Building the graph in tests uses a real `ChatOpenAI` object with a dummy key. It is never
invoked, so no request is made, but tool binding — which fake chat models do not
support — is exercised for real.

## Extending it

| Goal | Where to start |
|---|---|
| Add an open-call source | `OpenCallRepository.upsert` is the seam; write an adapter that produces the same dict shape as `seeding.load_catalogue`. |
| Add a specialist | Add a prompt in `agents/prompts.py`, a tool module, and one entry in `build_agents`. Mention it in `SUPERVISOR_PROMPT` — a test asserts every agent is named there. |
| Move to PostgreSQL | Change `DATABASE_URL` and install `asyncpg`. Only `db/` should need attention; `search_blob` already behaves correctly there. |
| Analyse images | Give `PortfolioAnalyzerAgent` a vision-capable model and a tool that accepts Telegram photo file ids. The persistence layer already stores whatever report comes back. |
