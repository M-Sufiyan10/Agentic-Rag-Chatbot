# M5 Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every chat turn is traced in a self-hosted Langfuse (prompt, LLM calls, tool calls, retrieval, tokens, latency), tagged with the user and the conversation, and thumbs up/down under an answer is saved and recorded as a Langfuse score on that turn's trace. With Langfuse off, down or unconfigured, the app works exactly as before.

**Architecture:**

- **`app/observability.py`** owns everything Langfuse: a small `Tracing` protocol plus `LangfuseTracing`, built only when both keys are configured. It hands out a per-turn LangChain `CallbackHandler` bound to a trace id we choose, and records or deletes feedback scores. Nothing it does may raise into a request.
- **Trace id = turn id.** `stream_agent_events` already gives each user message a uuid4 id. The turn's trace id is that uuid's 32-character hex form, so feedback on a turn can address its trace without storing anything extra, and failed (rolled-back) turns have no transcript id and so no feedback.
- **Feedback** is stored in our own Postgres (`message_feedback`), because Langfuse only overwrites a score when its id, name _and timestamp_ match: every resend reuses the first vote's timestamp. The transcript returns each assistant message's turn `id` and `feedback`; the UI shows thumbs only on saved messages (after `done` the UI already reloads the transcript), so **the SSE contract does not change**.
- **Langfuse server** runs as a Compose profile (`--profile observability`): web, worker, and its own Postgres, ClickHouse, Redis and MinIO, with a project and API keys created on first start (headless initialization), so the app connects with the documented dev keys.

**Tech Stack:** `langfuse` Python SDK **>=5.2,<6** (OpenTelemetry-based; `from langfuse import Langfuse`, `from langfuse.langchain import CallbackHandler`), Langfuse server **v4** images (`langfuse/langfuse:4`, `langfuse/langfuse-worker:4`), React + TanStack Query, lucide icons.

**Spec:** `docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md` (goal 7 "Observability", anti-goal "The app breaks when Langfuse is down", decisions "Observability" and "Langfuse hosting")
**Roadmap:** `docs/superpowers/plans/2026-11-10-00-roadmap.md`
**Branch:** `feat/m5-observability`, cut from `fix/auto-retrieval` (unmerged; stacked as before).

## Rulings made while planning

- **Langfuse v4 server + Python SDK v5, not v3.** The spec says "Langfuse v3"; since then the official compose file moved to v4 (tag `4`, must be newer than 4.75.0) and SDK v5 requires server >= 4.59.0 (SDK v4 is the last that supports a v3 server). Cost if wrong: a version pin.
- **Web UI on host port 3100**, not 3000: 3000 is in use on the dev machine. Cost if wrong: one port mapping.
- **No new SSE event.** Feedback attaches to saved transcript messages, which the UI shows right after `done`. Cost if wrong: thumbs appear a moment after the answer, not during streaming.

## Global Constraints

- **Optional at runtime.** Tracing is on only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are both set. Unset → no Langfuse client is created and no callback is attached. Set but unreachable → chat and feedback still work; failures are logged at WARNING, never raised; startup waits at most `LANGFUSE_TIMEOUT_SECONDS` (default **5**) for the reachability check.
- **Settings** (in `app/config.py`): `langfuse_public_key: str | None = None`, `langfuse_secret_key: SecretStr | None = None`, `langfuse_base_url: str = "http://localhost:3100"`, `langfuse_timeout_seconds: float = 5`. Pass them to `Langfuse(...)` explicitly; never rely on the SDK reading `os.environ`.
- **Trace tags:** run config `metadata` gets `langfuse_user_id` (the user id string), `langfuse_session_id` (the conversation id string) and `langfuse_tags: ["chat"]`; `run_name` is `"chat-turn"`.
- **Score:** name `"user_feedback"`, `data_type="BOOLEAN"`, value `1` for up and `0` for down, `trace_id` = the turn's trace id, `score_id` = `f"feedback-{turn_id}"`, `timestamp` = the feedback row's `created_at` (the first vote's time, unchanged on later votes). Clearing a vote deletes the score with `api.scores.delete(score_id)`.
- **SSE contract unchanged.** No new events, no new fields on existing ones.
- **Cross-user access returns 404**, never 403.
- **Tests never touch the network.** The `test_settings` fixture sets both Langfuse keys to `None` explicitly (a developer's `.env` may set them). Tracing tests use the SDK's `span_exporter=` seam with OpenTelemetry's `InMemorySpanExporter`.
- **Checks:** backend `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest`; frontend `npm run lint`, `npm run typecheck`, `npm test`.
- **Commits** are authored `M-Sufiyan10 <m.sufiyanyounis@gmail.com>` (already the repo's git config) and carry **no** `Co-Authored-By` trailer or any other AI attribution.
- **Never** read or print `.env` files, never touch the developer's servers on ports 8000 and 5173, and kill only processes you started.

## Review Focus

1. **Langfuse configured but down.** Expect: chat streams normally, feedback is saved and returns 200, startup finishes within the timeout. Pinned by `test_unreachable_langfuse_reports_unavailable_quickly` and `test_feedback_calls_never_raise` (Task 1) and `test_feedback_saved_without_tracing` (Task 3).
2. **Feedback on another user's conversation, an unknown turn, or a turn that never completed.** Expect 404 every time, and nothing saved. Pinned by Task 3's 404 tests.
3. **Changing a vote (up → down) later.** Expect the same Langfuse score overwritten, not a second score. Pinned by `test_changing_vote_reuses_first_timestamp` (Task 3).
4. **A failed turn (rolled back) or a turn still streaming.** Expect no `id` in the transcript, so no thumbs. Pinned by `test_transcript_ids_only_on_completed_turns` (Task 3).
5. **A developer `.env` with Langfuse keys.** Expect the test suite to stay offline. Pinned by the `test_settings` change and `test_tests_run_without_tracing` (Task 1).

---

### Task 1: Langfuse client wrapper, settings and startup

**Files:**

- Create: `backend/app/observability.py`
- Modify: `backend/app/config.py`, `backend/app/main.py`, `backend/pyproject.toml` (+ `uv.lock`), `backend/tests/conftest.py`
- Test: `backend/tests/test_observability.py`, `backend/tests/test_health.py`

**Interfaces:**

- Produces (in `app/observability.py`):
  - `FEEDBACK_SCORE_NAME = "user_feedback"`
  - `def trace_id_for_turn(turn_id: str) -> str` → `uuid.UUID(turn_id).hex` (raises `ValueError` for a non-uuid)
  - `class Tracing(Protocol)` with: `status: str` (`"ok"` or `"unavailable"`); `def callbacks(self, trace_id: str) -> list[BaseCallbackHandler]`; `async def check(self) -> str` (sets and returns `status`); `async def record_feedback(self, *, turn_id: str, positive: bool, first_voted_at: datetime) -> None`; `async def delete_feedback(self, *, turn_id: str) -> None`; `def shutdown(self) -> None`.
  - `class LangfuseTracing` implementing it: `__init__(self, settings: Settings, *, span_exporter: SpanExporter | None = None)` creates `Langfuse(public_key=..., secret_key=..., base_url=..., timeout=..., span_exporter=span_exporter)`; `callbacks` returns `[CallbackHandler(public_key=..., trace_context={"trace_id": trace_id})]`; `check` runs `auth_check` in a worker thread under `asyncio.wait_for(..., settings.langfuse_timeout_seconds)`; `record_feedback` calls `create_score` with the Global Constraints values; `delete_feedback` runs `api.scores.delete` in a worker thread with the same timeout. `record_feedback`, `delete_feedback`, `check` and `shutdown` catch every `Exception`, log WARNING, and return normally.
  - `def build_tracing(settings: Settings) -> LangfuseTracing | None` → `None` unless both keys are set.
- Produces (app): `app.state.tracing: Tracing | None`; `/api/health` returns `{"status": "ok", "mcp": {...}, "langfuse": "off" | "ok" | "unavailable"}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_observability.py`:
  - `test_trace_id_is_the_turn_uuid_hex`: `trace_id_for_turn("6f1c...uuid")` equals the uuid's `.hex` (32 lowercase hex chars); a non-uuid raises `ValueError`.
  - `test_build_tracing_needs_both_keys`: `None` with neither key, with only the public key, with only the secret key; a `LangfuseTracing` with both.
  - `test_callback_traces_into_the_chosen_trace`: build `LangfuseTracing(settings_with_keys, span_exporter=InMemorySpanExporter())`, run a `GenericFakeChatModel` with `config={"callbacks": tracing.callbacks(tid), "metadata": {"langfuse_user_id": "u1", "langfuse_session_id": "s1"}, "run_name": "chat-turn"}`, call `tracing.shutdown()` (flush), then assert every exported span's `context.trace_id == int(tid, 16)` and that `"u1"` and `"s1"` appear among the spans' attribute values.
  - `test_unreachable_langfuse_reports_unavailable_quickly`: `base_url="http://127.0.0.1:9"`, `langfuse_timeout_seconds=1`; `await tracing.check()` returns `"unavailable"` in under 3 s and does not raise.
  - `test_feedback_calls_never_raise`: with the client's `create_score` and `api.scores.delete` monkeypatched to raise, `record_feedback` and `delete_feedback` return `None`.
  - `test_tests_run_without_tracing(test_settings)`: `build_tracing(test_settings) is None`.
    And in `tests/test_health.py`: `test_health_reports_langfuse_off` → `response.json()["langfuse"] == "off"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_observability.py tests/test_health.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement.** `uv add "langfuse>=5.2,<6"`; add the four settings; write `app/observability.py` per Interfaces; in `test_settings` pass `langfuse_public_key=None, langfuse_secret_key=None`. In the lifespan: `app.state.tracing = build_tracing(settings)`; if not `None`, `await app.state.tracing.check()` and push `tracing.shutdown` (via `asyncio.to_thread`) onto the exit stack; health reports `"off"` when `app.state.tracing is None`, else its `status`. Set `app.state.tracing = None` before startup too, like `mcp_servers`.
- [ ] **Step 4: Run** the same tests → PASS; then the full backend checks.
- [ ] **Step 5: Commit** `feat(observability): optional Langfuse client with per-turn trace callbacks`.

### Task 2: Trace every chat turn

**Files:**

- Modify: `backend/app/chat/streaming.py`, `backend/app/chat/router.py`
- Test: `backend/tests/chat/test_streaming.py`, `backend/tests/chat/test_chat_api.py`

**Interfaces:**

- Consumes: `Tracing`, `trace_id_for_turn` (Task 1).
- Produces: `stream_agent_events(graph, thread_id, user_text, user_id, documents=None, tracing: Tracing | None = None)`. With `tracing`, the run config gains `"callbacks": tracing.callbacks(trace_id_for_turn(human_message.id))`, `"metadata"` with the three Global Constraints keys, and `"run_name": "chat-turn"`. Without it, the config is exactly as today. `stream_and_release` gains the same keyword and the POST endpoint passes `request.app.state.tracing`.

- [ ] **Step 1: Write the failing tests.** Use a fake `Tracing` whose `callbacks(trace_id)` records `trace_id` and returns one recording `BaseCallbackHandler` (records `on_chain_start` `metadata` and `name`).
  - `test_turn_is_traced_with_user_and_session` (streaming): after a run, the fake saw exactly one trace id, equal to `uuid.UUID(<the saved HumanMessage id>).hex`; the handler saw `metadata["langfuse_user_id"] == user_id`, `["langfuse_session_id"] == thread_id`, `["langfuse_tags"] == ["chat"]`, and a root chain named `"chat-turn"`.
  - `test_no_tracing_adds_no_callbacks` (streaming): wrap the compiled graph's `astream` in a spy that records the config; with `tracing=None` the config has no `"callbacks"`, `"metadata"` or `"run_name"` key.
  - `test_events_unchanged_with_tracing` (streaming): the event names for a tool-calling run are identical with and without the fake tracing.
  - `test_chat_endpoint_passes_app_tracing` (API): set `app.state.tracing = fake` and post a message; the fake saw one trace id.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement** per Interfaces. The rollback helper keeps using the same `config`.
- [ ] **Step 4: Run** → PASS; full backend checks.
- [ ] **Step 5: Commit** `feat(chat): trace each turn in Langfuse with user and conversation ids`.

### Task 3: Feedback API and transcript ids

**Files:**

- Create: `backend/app/feedback/__init__.py`, `models.py`, `schemas.py`, `router.py`; `backend/app/chat/transcript.py`; `backend/alembic/versions/0003_message_feedback.py`
- Modify: `backend/app/models.py`, `backend/app/main.py`, `backend/app/chat/router.py`, `backend/app/chat/schemas.py`
- Test: `backend/tests/feedback/__init__.py`, `backend/tests/feedback/test_feedback_api.py`, `backend/tests/chat/test_chat_api.py`

**Interfaces:**

- Consumes: `Tracing.record_feedback` / `delete_feedback` (Task 1).
- Produces:
  - Table `message_feedback`: `id` uuid PK; `conversation_id` uuid FK → `conversations.id` `ON DELETE CASCADE`; `user_id` uuid FK → `users.id` `ON DELETE CASCADE`; `turn_id` uuid not null; `positive` bool not null; `created_at`, `updated_at` timestamptz not null; unique `(conversation_id, turn_id)`. Model `MessageFeedback`, registered in `app/models.py`.
  - `app/chat/transcript.py`: `def completed_turn_ids(messages: Sequence[BaseMessage]) -> set[str]` (ids of `HumanMessage`s whose turn ends in an `AIMessage` without `tool_calls` and with text) and `def build_transcript(messages: Sequence[BaseMessage], feedback: Mapping[str, bool]) -> list[MessageOut]` (the current `get_messages` loop moved here, plus the two new fields).
  - `MessageOut` gains `id: str | None = None` (assistant messages: the turn's `HumanMessage` id; user messages: `None`) and `feedback: Literal["up", "down"] | None = None`.
  - `PUT /api/conversations/{conversation_id}/messages/{turn_id}/feedback`, body `{"value": "up" | "down"}` → `200 {"value": ...}`; upserts the row (keeping `created_at`), commits, then `await tracing.record_feedback(turn_id=..., positive=..., first_voted_at=row.created_at)` if tracing is configured.
  - `DELETE` same path → `204`, idempotent; deletes the row if any, then `await tracing.delete_feedback(turn_id=...)` if configured.
  - Both: `404` when the conversation isn't the caller's, or `turn_id` isn't in `completed_turn_ids` of the thread's saved state.

- [ ] **Step 1: Write the failing tests.** Build a completed turn by posting a message through the API with the default fake model, then read its id from `GET .../messages`.
  - `test_transcript_has_turn_ids_and_no_feedback_yet`: the assistant message has an `id` equal to a uuid string and `feedback is None`; user messages have `id is None`.
  - `test_transcript_ids_only_on_completed_turns`: a thread whose last turn failed (model raises) shows no assistant entry for it; `completed_turn_ids` on a turn ending in an AI tool call returns no id for it (unit test on `transcript.py`).
  - `test_put_then_get_shows_feedback`: PUT `up` → 200 `{"value": "up"}`; the transcript shows `feedback == "up"`; PUT `down` → transcript shows `"down"`.
  - `test_delete_clears_feedback`: PUT then DELETE → 204 and transcript `feedback is None`; DELETE again → 204.
  - `test_feedback_404s`: other user's conversation → 404; random uuid turn → 404; non-uuid turn path → 422; a missing conversation → 404. No row is written in any case.
  - `test_feedback_records_score` (fake tracing on `app.state.tracing`): PUT `up` → one `record_feedback(turn_id=<id>, positive=True, first_voted_at=<datetime>)`; DELETE → one `delete_feedback(turn_id=<id>)`.
  - `test_changing_vote_reuses_first_timestamp`: PUT `up`, PUT `down` → the two `first_voted_at` values are equal.
  - `test_feedback_saved_without_tracing`: with `app.state.tracing = None`, PUT returns 200 and the row exists (tracing failures can't reach the endpoint: `LangfuseTracing` never raises, which Task 1 pins).
  - `test_deleting_conversation_deletes_feedback`: PUT, delete the conversation, and no `message_feedback` row remains.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per Interfaces; the migration creates the table and its unique constraint and drops it on downgrade. Move the transcript loop into `transcript.py` unchanged apart from the new fields; `get_messages` loads the conversation's feedback rows in one query.
- [ ] **Step 4: Run** → PASS; `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` against the dev database succeed; full backend checks.
- [ ] **Step 5: Commit** `feat(feedback): thumbs feedback API stored per turn and sent to Langfuse`.

### Task 4: Thumbs up/down in the chat UI

**Files:**

- Create: `frontend/src/chat/FeedbackButtons.tsx`, `frontend/src/chat/FeedbackButtons.test.tsx`
- Modify: `frontend/src/chat/api.ts`, `frontend/src/chat/ChatPane.tsx`, `frontend/src/chat/ChatPane.test.tsx`

**Interfaces:**

- Consumes: Task 3's endpoints and transcript fields.
- Produces: `ChatMessage` gains `id?: string | null` and `feedback?: 'up' | 'down' | null`; `setFeedback(conversationId: string, turnId: string, value: 'up' | 'down'): Promise<{ value: 'up' | 'down' }>`; `clearFeedback(conversationId: string, turnId: string): Promise<void>`; `useFeedback(conversationId: string)` — a mutation taking `{ turnId, value: 'up' | 'down' | null }` (`null` clears) that updates `messagesKey(conversationId)` optimistically and restores the previous data on error. `FeedbackButtons` props: `{ conversationId: string; turnId: string; value: 'up' | 'down' | null }`.

- [ ] **Step 1: Write the failing tests** (Vitest + MSW, existing patterns):
  - `FeedbackButtons`: renders two buttons with accessible names "Good response" and "Bad response" and `aria-pressed` matching `value`; clicking "Good response" when `value` is `null` sends `PUT .../feedback {"value":"up"}`; clicking it when `value` is `'up'` sends `DELETE`; on a 500 the pressed state returns to its previous value and the text "Couldn't save feedback" appears.
  - `ChatPane`: a saved assistant message with an `id` shows the two buttons; one without an `id` (and the live streaming bubble) shows none.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement** with lucide `ThumbsUp` / `ThumbsDown`, small ghost buttons under the citations, matching the existing shadcn styling.
- [ ] **Step 4: Run** `npm run lint && npm run typecheck && npm test` → PASS.
- [ ] **Step 5: Commit** `feat(frontend): thumbs up/down feedback on assistant replies`.

### Task 5: Langfuse compose profile, docs and live check

**Files:**

- Modify: `docker-compose.yml`, `.env.example`, `README.md`, `docs/superpowers/plans/2026-11-10-00-roadmap.md`
- Create: `docs/learning/06-observability.md`

**Interfaces:**

- Produces: services `langfuse-web`, `langfuse-worker`, `langfuse-postgres`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-minio`, all with `profiles: ["observability"]`, so `docker compose up -d postgres` is unaffected. Images as upstream: `docker.langfuse.com/langfuse/langfuse:${LANGFUSE_IMAGE_TAG:-4}`, `docker.langfuse.com/langfuse/langfuse-worker:${LANGFUSE_IMAGE_TAG:-4}`, `docker.io/postgres:17`, `docker.io/clickhouse/clickhouse-server:26.3.62.3`, `docker.io/redis:7`, `cgr.dev/chainguard/minio`. Only `langfuse-web` is published, as `127.0.0.1:3100:3000`, with `NEXTAUTH_URL=http://localhost:3100`. Upstream's healthchecks and `depends_on: condition: service_healthy` for the data stores. Headless init on `langfuse-web`: org `chatbot`, project `chatbot`, keys `pk-lf-chatbot-local` / `sk-lf-chatbot-local`, user `admin@chatbot.local` / `chatbot-admin-password`. Every secret is `${VAR:-dev-default}` and marked as a local-development default.

- [ ] **Step 1: Validate** `docker compose config -q` and `docker compose --profile observability config -q` both exit 0, and `docker compose config --services` (no profile) does not list any `langfuse-*` service.
- [ ] **Step 2: Docs.** `.env.example`: the four `LANGFUSE_*` settings, commented, with the dev keys. README "Observability" section: start the profile, open `http://localhost:3100`, sign in, set the keys, what a trace shows, feedback, Langfuse Cloud as an alternative (same three variables), resource cost (six containers). Learning note 06: what a trace/observation/score is, how the per-turn trace id links feedback to traces, why feedback is stored locally (the overwrite rule), how to read a trace for the slow-answer and missed-tool problems from note 5. Roadmap M5 row → this plan's file name.
- [ ] **Step 3: Live check** (controller): bring the profile up, run the backend on a spare port with the dev keys, send one document question and one web question, and confirm through Langfuse's public API that the trace exists with the expected user and session ids, an LLM generation with token usage, and the tool or prefetch spans; give feedback and confirm the `user_feedback` score; change the vote and confirm there is still one score. Record the results in learning note 06.
- [ ] **Step 4: Commit** `feat(compose): optional Langfuse profile; M5 docs and learning note`.
