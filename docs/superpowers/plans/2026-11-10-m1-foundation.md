# M1: Foundation & Thin Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user can register, log in, create conversations and get a streamed reply from a local Ollama model through a LangGraph agent, in a React UI, with CI green.

**Architecture:** FastAPI backend (async SQLAlchemy + Alembic on Postgres/pgvector), JWT auth (access token in memory, refresh token in an httpOnly cookie), and a one-node LangGraph agent whose history is persisted by `AsyncPostgresSaver`. Replies stream over SSE. The Vite + React + TypeScript frontend proxies `/api` to the backend in dev, so cookies stay same-origin.

**Tech Stack:**

- **Backend:** Python 3.12, uv, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, pydantic-settings, PyJWT, pwdlib[argon2], langchain ≥1.0, langgraph ≥1.0, langchain-ollama, langgraph-checkpoint-postgres, psycopg[binary,pool], sse-starlette
- **Backend tests:** pytest, pytest-asyncio, httpx, asgi-lifespan; ruff and mypy for lint and types
- **Frontend:** React 19, Vite, TypeScript, Tailwind v4, shadcn/ui, React Router, TanStack Query, react-markdown + remark-gfm
- **Frontend tests:** Vitest, React Testing Library, MSW

**Spec:** `docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md`. Roadmap and cross-milestone decisions: `docs/superpowers/plans/2026-11-10-00-roadmap.md`. Read both.

## Global Constraints

- Python 3.12; dependencies managed with `uv` in `backend/`. Frontend uses `npm` in `frontend/`.
- All config comes from env vars via `app/config.py` `Settings`; `.env.example` lists every variable. No secrets committed.
- Default model is `qwen3:4b-instruct-2507-q4_K_M` via Ollama at `http://localhost:11434`. Switching the provider must not need code changes.
- Postgres image is `pgvector/pgvector:pg17` on host port `5433`. The dev DB is `chatbot` and the test DB is `chatbot_test`, with user and password `chatbot`/`chatbot`.
- Every query touching user data filters by the authenticated `user_id`. Another user's resource returns **404**.
- SSE event names and payloads follow the roadmap contract exactly.
- Unit tests never call a real LLM: they use `GenericFakeChatModel`.
- Code favours clarity: small modules, with docstrings on the LangGraph and streaming code.
- Commit after every task. Messages use conventional commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Email case.** If `Bob@Example.com` is registered, registering `bob@example.com` returns 409, and logging in with either case works. Tested in Task 4.
2. **Ollama down or model not pulled.** The stream ends with an `error` event carrying a friendly message, not a hung request or a 500 halfway through. Tested in Task 7 with a model that raises.
3. **A second message to a conversation that is already streaming** (for example from a second tab) returns 409 `"A reply is already in progress"` and doesn't corrupt history. Tested in Task 7. The lock is per process; this is acceptable for M1 and noted in the code.
4. **Access token expires mid-session.** The client refreshes once, retries the request transparently, and goes to `/login` only if refresh fails. Tested in Task 8.
5. **Assistant output containing HTML** (for example `<img src=x onerror=alert(1)>`) is rendered as text, never executed. Tested in Task 9.

---

### Task 1: Backend scaffold, settings, health endpoint, dev Postgres

**Files:**

- Create:
  - `.gitignore`
  - `.env.example`
  - `docker-compose.yml`
  - `infra/postgres/init.sql`
  - `backend/pyproject.toml`
  - `backend/app/__init__.py`
  - `backend/app/config.py`
  - `backend/app/main.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_health.py`

**Interfaces:**

- Produces:
  - `Settings` (pydantic-settings, env prefix none, reads `.env`). The fields and their defaults:

    | Field                      | Type        | Default                                                         |
    | -------------------------- | ----------- | --------------------------------------------------------------- |
    | `database_url`             | `str`       | `"postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot"` |
    | `jwt_secret`               | `str`       | `"change-me"`                                                   |
    | `access_token_ttl_minutes` | `int`       | `15`                                                            |
    | `refresh_token_ttl_days`   | `int`       | `7`                                                             |
    | `cookie_secure`            | `bool`      | `False`                                                         |
    | `cors_origins`             | `list[str]` | `["http://localhost:5173"]`                                     |
    | `llm_provider`             | `str`       | `"ollama"`                                                      |
    | `llm_model`                | `str`       | `"qwen3:4b-instruct-2507-q4_K_M"`                               |
    | `llm_temperature`          | `float`     | `0.2`                                                           |
    | `ollama_base_url`          | `str`       | `"http://localhost:11434"`                                      |

  - `get_settings() -> Settings` (lru-cached)
  - `create_app(settings: Settings | None = None) -> FastAPI`. All routers mount under `/api`.
  - `client` fixture in `tests/conftest.py`: an `httpx.AsyncClient` over `ASGITransport`, wrapped in `asgi_lifespan.LifespanManager`
  - `test_settings` fixture: `Settings` with `database_url` from env `TEST_DATABASE_URL`, defaulting to `postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot_test`, and `jwt_secret="test-secret"`

- [ ] **Step 1: Write compose, the init SQL, `.env.example` and `.gitignore`.**
  - Compose has one service, `postgres`, with a named volume and a healthcheck (`pg_isready`).
  - `init.sql` runs `CREATE DATABASE chatbot_test;`.
  - `.gitignore` covers `.env`, `.venv`, `__pycache__`, `node_modules`, `dist` and `.pytest_cache`.
- [ ] **Step 2: Start Postgres.** Start Docker Desktop if it isn't running, then run `docker compose up -d postgres && docker compose ps`. Expected: postgres `healthy`.
- [ ] **Step 3: Scaffold the backend.** Run `uv init --package` in `backend/`. Add the runtime and dev dependencies from the Tech Stack. Configure `[tool.pytest.ini_options] asyncio_mode = "auto"`, `[tool.ruff]` line-length 100, and `[tool.mypy] strict = true` for `app`.
- [ ] **Step 4: Write the failing test.**

```python
async def test_health_returns_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 5: Run it and check it fails.** Run `cd backend && uv run pytest tests/test_health.py -v`. Expected: FAIL (import error, no `create_app`).
- [ ] **Step 6: Implement `Settings`, `get_settings` and `create_app`** with CORS middleware (`allow_credentials=True`) and `GET /api/health`.
- [ ] **Step 7: Run the checks.** Run `uv run pytest -v && uv run ruff check . && uv run mypy app`. Expected: all pass.
- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(backend): scaffold FastAPI app, settings and dev Postgres"`

---

### Task 2: Database layer, models and migrations

**Files:**

- Create:
  - `backend/app/db.py`
  - `backend/app/auth/__init__.py`
  - `backend/app/auth/models.py`
  - `backend/app/conversations/__init__.py`
  - `backend/app/conversations/models.py`
  - `backend/app/models.py`, which imports all models so Alembic sees them
  - `backend/alembic.ini`
  - `backend/alembic/env.py` (async)
  - `backend/alembic/versions/0001_initial.py`
- Modify: `backend/app/main.py` (engine created in lifespan, disposed on shutdown) and `backend/tests/conftest.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**

- Consumes: `Settings.database_url`
- Produces:
  - `Base(DeclarativeBase)`
  - `get_session() -> AsyncIterator[AsyncSession]`, a FastAPI dependency that uses the session factory stored on `app.state.sessionmaker`
  - `User`:
    - `id: UUID` (pk, default uuid4)
    - `email: str` (unique, stored lowercased, max 320)
    - `password_hash: str`
    - `created_at: datetime` (tz-aware, server default now)
  - `Conversation`:
    - `id: UUID` (pk)
    - `user_id: UUID` (FK `users.id` `ON DELETE CASCADE`, indexed)
    - `title: str` (max 200, default `"New chat"`)
    - `created_at`
    - `updated_at` (tz-aware; app sets it on write)
  - Migration `0001` creates both tables and runs `CREATE EXTENSION IF NOT EXISTS vector` (used in M3).
  - Fixtures in `conftest.py`:
    - session-scoped: runs `alembic upgrade head` against the test DB
    - autouse: `TRUNCATE users CASCADE` after each test
    - `db_session` fixture

- [ ] **Step 1: Write the failing tests.**

```python
async def test_user_email_is_unique(db_session):
    db_session.add(User(email="a@example.com", password_hash="x"))
    await db_session.commit()
    db_session.add(User(email="a@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        await db_session.commit()

async def test_deleting_user_cascades_conversations(db_session):
    u = User(email="b@example.com", password_hash="x")
    db_session.add(u); await db_session.flush()
    db_session.add(Conversation(user_id=u.id)); await db_session.commit()
    await db_session.delete(u); await db_session.commit()
    assert (await db_session.scalar(select(func.count()).select_from(Conversation))) == 0
```

- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/test_models.py -v`. Expected: FAIL (no models).
- [ ] **Step 3: Implement `db.py`, the models, async Alembic `env.py` and migration `0001`.** Use `passive_deletes=True` on the relationship.
- [ ] **Step 4: Run the tests and apply the migration.** Run `uv run pytest -v && uv run alembic upgrade head` (dev DB). Expected: tests PASS, and the migration applies cleanly.
- [ ] **Step 5: Commit.** `git commit -am "feat(backend): add DB layer, User/Conversation models and initial migration"`, after `git add` for the new files.

---

### Task 3: Password hashing and JWT helpers

**Files:**

- Create: `backend/app/auth/security.py`
- Test: `backend/tests/auth/test_security.py`

**Interfaces:**

- Produces:
  - `hash_password(plain: str) -> str`, using pwdlib argon2
  - `verify_password(plain: str, hashed: str) -> bool`
  - `TokenKind = Literal["access", "refresh"]`
  - `create_token(user_id: UUID, kind: TokenKind, settings: Settings) -> str`. HS256, with claims `sub`, `type` and `exp`. The TTL comes from settings.
  - `decode_token(token: str, expected: TokenKind, settings: Settings) -> UUID`. Raises `InvalidTokenError` (re-exported from `jwt`) on a bad signature, an expired token or the wrong `type`.

- [ ] **Step 1: Write the failing tests.**
  - `test_hash_roundtrip`: verify true for the right password and false for a wrong one
  - `test_hash_is_salted`: two hashes of the same password differ
  - `test_token_roundtrip_returns_user_id`
  - `test_refresh_token_rejected_as_access`: `create_token(..., "refresh")` then `decode_token(..., "access")` raises `InvalidTokenError`
  - `test_expired_token_rejected`: settings with `access_token_ttl_minutes=-1` raise `InvalidTokenError`
  - `test_token_signed_with_other_secret_rejected`
- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/auth/test_security.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `security.py`.**
- [ ] **Step 4: Run them and check they pass.** Run `uv run pytest tests/auth -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(auth): password hashing and JWT helpers"`

---

### Task 4: Auth endpoints

**Files:**

- Create:
  - `backend/app/auth/schemas.py`
  - `backend/app/auth/router.py`
  - `backend/app/auth/deps.py`
- Modify:
  - `backend/app/main.py`, to include the router
  - `backend/tests/conftest.py`, to add the helper
- Test: `backend/tests/auth/test_auth_api.py`

**Interfaces:**

- Consumes: Task 3 helpers, `User`, `get_session`
- Produces:
  - Schemas:
    - `RegisterIn` / `LoginIn`: `email: EmailStr`, `password: str` (8–128 characters)
    - `UserOut`: `id: UUID`, `email: str`
    - `TokenOut`: `access_token: str`, `token_type: Literal["bearer"]`, `user: UserOut`
  - Endpoints:

    | Endpoint                  | Success                            | Errors                                                                                            |
    | ------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------- |
    | `POST /api/auth/register` | 201 `TokenOut` + refresh cookie    | 409 `{"detail": "Email already registered"}`                                                      |
    | `POST /api/auth/login`    | 200 `TokenOut` + refresh cookie    | 401 `{"detail": "Invalid email or password"}`, the same for an unknown email and a wrong password |
    | `POST /api/auth/refresh`  | 200 `TokenOut`, rotates the cookie | 401 if the cookie is missing or invalid                                                           |
    | `POST /api/auth/logout`   | 204, clears the cookie             | —                                                                                                 |
    | `GET /api/auth/me`        | 200 `UserOut`                      | —                                                                                                 |

  - Refresh cookie: `name="refresh_token"`, `httponly=True`, `samesite="lax"`, `path="/api/auth"`, `secure=settings.cookie_secure`, `max_age` set to the refresh TTL.
  - `get_current_user(...) -> User`, a dependency that reads `Authorization: Bearer`. It returns 401 with `WWW-Authenticate: Bearer` when the token is missing or invalid, is the wrong kind, or belongs to a user that no longer exists.
  - Test helper `register_user(client, email="alice@example.com", password="correct-horse-1") -> dict[str, str]` returns the auth headers.
  - Emails are lowercased before storing and before lookup.

- [ ] **Step 1: Write the failing tests.** Names and assertions:
  - `test_register_returns_token_and_sets_cookie`: 201, `"refresh_token" in r.cookies`
  - `test_register_duplicate_email_case_insensitive`: register `Bob@Example.com`, then `bob@example.com` returns 409
  - `test_register_rejects_short_password`: 422
  - `test_login_success_any_email_case`: login with `BOB@example.com` returns 200
  - `test_login_wrong_password_and_unknown_email_same_401`: both 401 with the identical detail
  - `test_me_requires_token`: 401
  - `test_me_returns_current_user`
  - `test_refresh_issues_new_access_token`: uses the cookie set by login
  - `test_refresh_without_cookie_401`
  - `test_access_token_cannot_be_used_as_refresh`: put the access token in the cookie; expect 401
  - `test_logout_clears_cookie_then_refresh_401`
- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/auth/test_auth_api.py -v`. Expected: FAIL (404s).
- [ ] **Step 3: Implement the schemas, `deps.get_current_user` and the router.**
- [ ] **Step 4: Run the checks.** Run `uv run pytest -v && uv run ruff check . && uv run mypy app`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(auth): register, login, refresh, logout and me endpoints"`

---

### Task 5: Conversations CRUD

**Files:**

- Create:
  - `backend/app/conversations/schemas.py`
  - `backend/app/conversations/service.py`
  - `backend/app/conversations/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/conversations/test_conversations_api.py`

**Interfaces:**

- Consumes: `get_current_user`, `Conversation`, `register_user`
- Produces:
  - Schemas:
    - `ConversationOut`: `id`, `title`, `created_at`, `updated_at`
    - `ConversationCreate`: `title: str | None` (1–200)
    - `ConversationUpdate`: `title: str` (1–200, stripped)
  - `service.get_owned_conversation(session: AsyncSession, user_id: UUID, conversation_id: UUID) -> Conversation`. Raises `HTTPException(404, "Conversation not found")`.
  - Endpoints:
    - `POST /api/conversations` → 201
    - `GET /api/conversations` → `list[ConversationOut]`, own only, ordered `updated_at desc`
    - `PATCH /api/conversations/{id}` → 200
    - `DELETE /api/conversations/{id}` → 204

- [ ] **Step 1: Write the failing tests.**
  - `test_create_defaults_title_new_chat`
  - `test_list_only_returns_own_conversations`: alice and bob each create one, and each sees exactly one
  - `test_list_ordered_by_updated_at_desc`
  - `test_rename_conversation`
  - `test_rename_rejects_blank_title`: `"   "` returns 422
  - `test_other_users_conversation_is_404`: bob's PATCH and DELETE on alice's id both return 404
  - `test_delete_conversation`: a following GET list excludes it
- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/conversations -v`. Expected: FAIL.
- [ ] **Step 3: Implement the schemas, service and router.**
- [ ] **Step 4: Run them and check they pass.** Run `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(conversations): CRUD endpoints with per-user isolation"`

---

### Task 6: Provider-agnostic LLM factory and the LangGraph agent

**Files:**

- Create:
  - `backend/app/agent/__init__.py`
  - `backend/app/agent/llm.py`
  - `backend/app/agent/prompts.py`
  - `backend/app/agent/graph.py`
- Test: `backend/tests/agent/test_llm.py`, `backend/tests/agent/test_graph.py`

**Interfaces:**

- Consumes: `Settings.llm_*`, `Settings.ollama_base_url`
- Produces:
  - `build_chat_model(settings: Settings) -> BaseChatModel`. It uses `langchain.chat_models.init_chat_model(model=settings.llm_model, model_provider=settings.llm_provider, temperature=settings.llm_temperature)`, plus `base_url=settings.ollama_base_url` when the provider is `"ollama"`. An unsupported provider raises `ValueError` whose message names the provider.
  - `SYSTEM_PROMPT: str` in `prompts.py`. It's a short, friendly general assistant prompt that says to say so when unsure rather than invent facts.
  - `build_graph(model: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`:
    - The state is `MessagesState`.
    - The single node `"agent"` invokes `model` with `[SystemMessage(SYSTEM_PROMPT), *state["messages"]]` and returns `{"messages": [response]}`.
    - Edges: `START → agent → END`.
    - The system message is **not** stored in state.
    - The module docstring explains the node/edge/state concepts for the learner.

- [ ] **Step 1: Write the failing tests.**
  - `test_build_chat_model_ollama`: the result is an instance of `ChatOllama`, `.model` equals the settings model, and `.base_url` equals the settings URL
  - `test_build_chat_model_unknown_provider_raises`: `llm_provider="nope"` raises `ValueError` matching `"nope"`
  - `test_graph_replies_and_persists_history`:

```python
model = GenericFakeChatModel(messages=iter([AIMessage("Hi!"), AIMessage("Again!")]))
graph = build_graph(model, InMemorySaver())
cfg = {"configurable": {"thread_id": "t1"}}
await graph.ainvoke({"messages": [HumanMessage("hello")]}, cfg)
out = await graph.ainvoke({"messages": [HumanMessage("and again")]}, cfg)
assert [m.content for m in out["messages"]] == ["hello", "Hi!", "and again", "Again!"]
```

- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/agent -v`. Expected: FAIL.
- [ ] **Step 3: Implement `llm.py`, `prompts.py` and `graph.py`.**
- [ ] **Step 4: Run them and check they pass.** Run `uv run pytest tests/agent -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(agent): provider-agnostic LLM factory and one-node LangGraph agent"`

---

### Task 7: Checkpointer lifecycle, streaming chat endpoint and history

**Files:**

- Create:
  - `backend/app/agent/checkpointer.py`
  - `backend/app/chat/__init__.py`
  - `backend/app/chat/streaming.py`
  - `backend/app/chat/router.py`
  - `backend/app/chat/schemas.py`
- Modify:
  - `backend/app/main.py`: the lifespan builds the checkpointer and graph and stores them on `app.state.graph`
  - `backend/app/conversations/router.py`: DELETE also deletes the thread
  - `backend/tests/conftest.py`
- Test:
  - `backend/tests/chat/test_streaming.py`
  - `backend/tests/chat/test_chat_api.py`
  - `backend/tests/agent/test_checkpointer.py`

**Interfaces:**

- Consumes: `build_graph`, `build_chat_model`, `get_owned_conversation`, `get_current_user`
- Produces:
  - `create_app(settings=None, *, chat_model: BaseChatModel | None = None, checkpointer: BaseCheckpointSaver | None = None) -> FastAPI`. If `chat_model` or `checkpointer` is given, it's used instead of the real one (test seam).
  - `open_checkpointer(database_url: str) -> AsyncContextManager[AsyncPostgresSaver]`. It converts `postgresql+asyncpg://` to `postgresql://`, opens an `AsyncConnectionPool` (`autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`) and calls `await saver.setup()`.
  - `get_graph(request: Request) -> CompiledStateGraph`, a dependency
  - `stream_agent_events(graph, thread_id: str, user_text: str) -> AsyncIterator[ServerSentEvent]`, where `ServerSentEvent` is the type from `sse_starlette`:
    - It uses `graph.astream(..., stream_mode="messages")`.
    - It yields `token` for each non-empty `AIMessageChunk` text, then `done` with the joined content.
    - On any exception it logs with the traceback and yields `error` `{"message": "The assistant couldn't respond. Is the model server running? Please try again."}`.
  - `MessageIn`: `content: str` (1–8000 characters, stripped, non-empty)
  - `MessageOut`: `role: Literal["user", "assistant"]`, `content: str`
  - `POST /api/conversations/{id}/messages` returns an `EventSourceResponse`:
    - The ownership check happens before streaming starts.
    - If the title is `"New chat"`, it becomes the first 60 characters of the content.
    - `updated_at` is bumped.
    - It returns 409 `{"detail": "A reply is already in progress"}` if the conversation id is in the module-level in-progress set. The id is removed in `finally`.
  - `GET /api/conversations/{id}/messages` returns `list[MessageOut]` from `graph.aget_state(...)`. It includes only `HumanMessage`/`AIMessage` with non-empty string content, and returns `[]` for a new thread.
  - `DELETE /api/conversations/{id}` additionally calls `await graph.checkpointer.adelete_thread(str(id))`.
  - Test helper `read_sse(response) -> list[tuple[str, dict]]` in `conftest.py`. It parses the `event:`/`data:` lines of the response body.

- [ ] **Step 1: Write the failing tests.**
  - `test_stream_yields_tokens_then_done` (unit, on `stream_agent_events` with `GenericFakeChatModel(messages=iter([AIMessage("Hello there")]))`): the concatenated `token` texts equal `"Hello there"`, and the last event is `("done", {"content": "Hello there"})`
  - `test_stream_model_failure_yields_error_event`: the model's `_astream`/`_agenerate` raises `ConnectionError`; the events end with exactly one `error` event with the message above
  - `test_post_message_streams_and_sets_title`: the API returns 200 with `text/event-stream`, and the conversation title afterwards equals the first 60 characters of the message
  - `test_history_after_one_exchange`: GET messages returns `[{"role": "user", ...}, {"role": "assistant", "content": "Hello there"}]`
  - `test_post_message_to_other_users_conversation_404`
  - `test_post_empty_message_422`: `"   "` returns 422
  - `test_concurrent_message_returns_409`: put the id into the in-progress set via the module, then POST and expect 409
  - `test_delete_conversation_deletes_thread`: after DELETE, `aget_state` for that thread has no messages
  - `test_open_checkpointer_against_test_db` (integration): enter the context; `setup()` succeeds and a put/get round-trip on a thread works
- [ ] **Step 2: Run them and check they fail.** Run `uv run pytest tests/chat tests/agent/test_checkpointer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `checkpointer.py`, `streaming.py`, the router and the lifespan wiring.**
- [ ] **Step 4: Run the checks.** Run `uv run pytest -v && uv run ruff check . && uv run mypy app`. Expected: PASS.
- [ ] **Step 5: Manual smoke test against real Ollama.**
  1. Start the server with `uv run alembic upgrade head && uv run uvicorn app.main:create_app --factory --reload`.
  2. Register with curl, create a conversation, then `curl -N -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"content":"Say hi in 5 words"}' localhost:8000/api/conversations/$C/messages`.

  Expected: `event: token` lines arrive incrementally, then `event: done`.

- [ ] **Step 6: Commit.** `git commit -m "feat(chat): SSE streaming endpoint with Postgres-checkpointed history"`

---

### Task 8: Frontend scaffold, API client and auth flow

**Files:**

- Create:
  - `frontend/` (Vite react-ts template)
  - `frontend/vite.config.ts`: proxy `/api` to `http://localhost:8000`, Tailwind plugin, Vitest `jsdom` environment, `setupFiles: ["src/test/setup.ts"]`
  - `frontend/src/test/setup.ts`: jest-dom and an MSW server
  - `frontend/src/api/client.ts`
  - `frontend/src/auth/AuthProvider.tsx`
  - `frontend/src/auth/RequireAuth.tsx`
  - `frontend/src/pages/LoginPage.tsx`
  - `frontend/src/pages/RegisterPage.tsx`
  - `frontend/src/App.tsx`: routes `/login`, `/register`, and `/` (protected)
  - shadcn components: `button`, `input`, `label`, `card`
- Test:
  - `frontend/src/api/client.test.ts`
  - `frontend/src/pages/LoginPage.test.tsx`
  - `frontend/src/auth/RequireAuth.test.tsx`

**Interfaces:**

- Consumes: the Task 4 endpoints
- Produces:
  - `setAccessToken(token: string | null): void`
  - `setOnAuthFailure(cb: () => void): void`
  - `apiFetch(path: string, init?: RequestInit): Promise<Response>`:
    - It adds the `Authorization` header and `credentials: "include"`.
    - On a 401 from a non-`/api/auth/*` path, it calls `POST /api/auth/refresh` **once**, shared across concurrent callers through a single in-flight promise. Then it either retries with the new token, or calls `onAuthFailure` and returns the 401.
  - `apiJson<T>(path, init?): Promise<T>`. It throws `ApiError { status: number; detail: string }` on a non-2xx response.
  - `useAuth(): { user: User | null; status: "loading" | "authenticated" | "anonymous"; login(email, password): Promise<void>; register(email, password): Promise<void>; logout(): Promise<void> }`. On mount it tries `/api/auth/refresh` to restore the session.
  - `type User = { id: string; email: string }`, in `src/api/types.ts`

- [ ] **Step 1: Scaffold.**
  1. `npm create vite@latest frontend -- --template react-ts`
  2. Install Tailwind v4 (`@tailwindcss/vite`), then run `npx shadcn@latest init`.
  3. `npm i react-router @tanstack/react-query react-markdown remark-gfm`
  4. Dev dependencies: `vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom msw`
  5. Add the scripts `test`, `typecheck` (`tsc -b --noEmit`) and `lint`.
- [ ] **Step 2: Write the failing tests.**
  - `apiFetch retries once after a successful refresh`: MSW returns 401 on the first `/api/conversations`, refresh returns 200 `{access_token: "new"}`, and the second call sees `Authorization: Bearer new`; the result is 200
  - `apiFetch calls onAuthFailure when refresh fails`
  - `concurrent 401s trigger a single refresh call`: two parallel calls; the refresh handler is hit once
  - `LoginPage shows server error on 401`: the text `Invalid email or password` is visible
  - `LoginPage navigates to / on success`
  - `RequireAuth redirects anonymous users to /login`
- [ ] **Step 3: Run them and check they fail.** Run `cd frontend && npm test -- --run`. Expected: FAIL.
- [ ] **Step 4: Implement the client, AuthProvider, RequireAuth and the pages.**
  - Forms use shadcn `Card` + `Input`.
  - The register page validates password ≥ 8 characters on the client.
- [ ] **Step 5: Run the checks.** Run `npm test -- --run && npm run typecheck && npm run lint`. Expected: PASS.
- [ ] **Step 6: Commit.** `git commit -m "feat(frontend): scaffold Vite React app with auth flow and refreshing API client"`

---

### Task 9: SSE parser and chat UI

**Files:**

- Create:
  - `frontend/src/api/sse.ts`
  - `frontend/src/chat/api.ts`
  - `frontend/src/chat/useChatStream.ts`
  - `frontend/src/chat/ConversationSidebar.tsx`
  - `frontend/src/chat/ChatPane.tsx`
  - `frontend/src/chat/MessageBubble.tsx`
  - `frontend/src/pages/ChatPage.tsx` (mounted at `/` and `/c/:conversationId`)
- Test:
  - `frontend/src/api/sse.test.ts`
  - `frontend/src/chat/ChatPane.test.tsx`
  - `frontend/src/chat/MessageBubble.test.tsx`

**Interfaces:**

- Consumes:
  - `apiFetch` and `apiJson` from Task 8
  - the SSE contract (roadmap)
  - the Task 5 and Task 7 endpoints
- Produces:
  - `parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: unknown }>`. It handles chunks split mid-line or mid-event, several events per chunk, and `\r\n` line endings.
  - `chat/api.ts`, built with TanStack Query:
    - `listConversations()`
    - `createConversation()`
    - `renameConversation(id, title)`
    - `deleteConversation(id)`
    - `getMessages(id)`
    - query keys `["conversations"]` and `["messages", id]`
  - `useChatStream(conversationId): { streamingText: string; status: "idle" | "waiting" | "streaming" | "error"; error: string | null; send(content: string): Promise<void> }`:
    - The status is `waiting` until the first token arrives.
    - On `done` it invalidates `["messages", id]` and `["conversations"]`.
    - On a 409 it sets the error `"A reply is already in progress"`.
  - `MessageBubble({ role, content })` renders markdown with `react-markdown` + `remark-gfm`, with **no** `rehype-raw`.
  - `ChatPane` behaviour:
    - Enter sends; Shift+Enter inserts a newline.
    - Send is disabled while `waiting`/`streaming` or when the input is blank.
    - It shows "Thinking…" while `waiting`.
    - An error shows inline with a "Retry" button that resends the last content.
  - `ConversationSidebar`: new chat, list, rename (inline), delete (with confirm), and logout.

- [ ] **Step 1: Write the failing tests.**
  - `parseSSE handles an event split across chunks`: chunks `"event: tok"`, `"en\ndata: {\"text\":\"Hi\"}\n"`, `"\n"` yield `[{event: "token", data: {text: "Hi"}}]`
  - `parseSSE handles two events in one chunk and CRLF`
  - `ChatPane shows Thinking then streamed tokens then final message`: MSW streams `token` Hel, `token` lo, `done`; "Thinking…" appears first, then `Hello`
  - `ChatPane shows error event with Retry`: MSW streams `error` `{message: "The assistant couldn't respond..."}`; the message and the Retry button are visible
  - `ChatPane disables send while streaming`
  - `MessageBubble renders HTML as text`: the content `<img src=x onerror=alert(1)>` produces no `img` element in the document
- [ ] **Step 2: Run them and check they fail.** Run `npm test -- --run`. Expected: FAIL.
- [ ] **Step 3: Implement `sse.ts`, `chat/api.ts`, `useChatStream`, the components and `ChatPage`.**
  - Layout: sidebar on the left, chat on the right, a responsive single column under 768px.
  - Auto-scroll to the bottom while streaming.
- [ ] **Step 4: Run the checks.** Run `npm test -- --run && npm run typecheck && npm run lint`. Expected: PASS.
- [ ] **Step 5: Manual end-to-end check.** Run the backend (Task 7, Step 5) and `npm run dev`, then open http://localhost:5173:
  1. Register, start a chat and send "Explain LangGraph in two sentences".
  2. Check that tokens stream, the title updates in the sidebar, and history survives a page reload and a backend restart.
  3. Stop Ollama and send a message: the friendly error and Retry appear.
- [ ] **Step 6: Commit.** `git commit -m "feat(frontend): streaming chat UI with conversation sidebar"`

---

### Task 10: CI, README and learning notes

**Files:**

- Create:
  - `.github/workflows/ci.yml`
  - `README.md`
  - `docs/learning/01-foundation.md`

**Interfaces:**

- Consumes: the commands from Tasks 1–9

- [ ] **Step 1: Write `ci.yml`.** It runs on push and pull_request, with two jobs:
  - **`backend`**:
    - service `pgvector/pgvector:pg17` on port 5432, with `POSTGRES_USER`/`POSTGRES_PASSWORD` set to `chatbot`/`chatbot` and `POSTGRES_DB=chatbot_test`
    - `TEST_DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot_test`
    - `astral-sh/setup-uv`
    - `uv sync`, `ruff check`, `ruff format --check`, `mypy app`, `pytest`
  - **`frontend`**: Node 22, `npm ci`, `lint`, `typecheck`, `test -- --run`
- [ ] **Step 2: Verify locally what CI runs.** Run `cd backend && uv run ruff format --check . && cd ../frontend && npm ci && npm test -- --run`. Expected: all pass. Fix any formatting before committing.
- [ ] **Step 3: Write the README.**
  - What the project is: one paragraph from spec §1
  - The M1 feature list and the roadmap table, linked
  - Prerequisites: Docker, uv, Node 22+, Ollama, and `ollama pull qwen3:4b-instruct-2507-q4_K_M`
  - Quickstart: compose up, `.env`, migrate, backend, frontend
  - How to switch provider via env vars
  - The test commands
- [ ] **Step 4: Write `docs/learning/01-foundation.md`.** It's about 1–2 pages for the owner:
  - how JWT access and refresh tokens work here, and why the refresh token is in an httpOnly cookie
  - LangGraph state, nodes, edges and checkpointers, with file pointers
  - how a message flows from the browser through SSE to tokens
  - why `init_chat_model` makes the provider swappable
  - "try it yourself" exercises, for example adding a second node
- [ ] **Step 5: Commit.** `git commit -m "chore: add CI workflow, README and M1 learning notes"`
