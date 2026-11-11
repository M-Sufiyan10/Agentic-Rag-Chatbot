# Agentic Chatbot

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A multi-user LLM chatbot built the way a production AI app is built: a **LangGraph agent** behind a **FastAPI** API, conversations persisted in **Postgres**, tokens **streamed** to a **React** UI over Server-Sent Events, and a test suite and CI around all of it. It runs fully locally on a laptop with Ollama, and moving to a hosted model is a configuration change, not a rewrite.

The goal is more than "a chat box". Over the roadmap below the agent grows tools, document Q&A (RAG), MCP servers, tracing, and a measured evaluation, because the engineering around the LLM is the point.

> **Status:** Milestones 1 (foundation) and 2 (agent tools) are complete. See the [roadmap](#roadmap).

## Screenshots

<!-- TODO (M7): add a screenshot and a short GIF of a streamed conversation here. -->

_Screenshots and a demo GIF will be added in Milestone 7._

## What works today (M1 and M2)

- **Auth:** register and log in with email and password (argon2 hashes). Short-lived JWT access tokens, with a refresh token held in an httpOnly cookie.
- **Conversations:** create, list, rename and delete. Every query is scoped to the signed-in user, and another user's conversation returns `404`.
- **Streamed chat:** a LangGraph agent (a model node and a tool node in a loop) replies token by token over SSE, with `tool_start`/`tool_end` events when it uses a tool. History lives in a Postgres-backed LangGraph checkpointer, so it survives restarts.
- **Swappable model:** the LLM is chosen by environment variables (Ollama by default).
- **React UI:** login/register, conversation sidebar, and a chat pane with live streaming and Markdown rendering.
- **Agent tools (M2):** the agent loops between the model and tools until it can answer. It has a safe `calculator` (an AST evaluator, never `eval`), `current_datetime`, and `web_search` (DuckDuckGo, no API key). The UI shows a live chip per tool call ("Searching the web...") and keeps them on the saved message. A max-rounds guard stops a looping model, long histories are trimmed for the model, a failed or abandoned reply is rolled back so the thread stays valid, and Markdown images in replies are shown as links instead of being fetched.
- **Quality gates:** pytest (the LLM is always faked in unit tests), ruff, mypy strict, Vitest, oxlint and `tsc`, all run in GitHub Actions.

## Roadmap

The full design is in the [spec](docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md); the milestone breakdown and cross-milestone decisions (including the SSE event contract) are in the [roadmap](docs/superpowers/plans/2026-11-10-00-roadmap.md).

| #   | Milestone               | Delivers                                                                                              |
| --- | ----------------------- | ----------------------------------------------------------------------------------------------------- |
| M1  | Foundation & thin slice | Postgres, auth, conversations, streamed chat with a LangGraph agent, React UI, CI (**done**)          |
| M2  | Agent tools             | Tool loop; calculator, current time, web search; tool-status events and chips in the UI (**done**)    |
| M3  | Documents & RAG         | Upload PDF/DOCX/TXT/MD, ingestion into pgvector, per-user document search, citations                  |
| M4  | MCP                     | A custom FastMCP server plus a third-party one, loaded by the agent from config                       |
| M5  | Observability           | Langfuse tracing tagged by user and conversation, thumbs feedback as scores                           |
| M6  | Evaluation              | Eval dataset and runner, LLM judge, tool-selection / retrieval / faithfulness metrics, 4B vs 8B model |
| M7  | Production polish       | Dockerfiles, one-command `docker compose up`, rate limits, architecture diagram, screenshots          |

## Architecture

```
React (Vite) --POST /api/conversations/{id}/messages--> FastAPI --> LangGraph agent <--> chat model
     ^                                                      |             |   ^
     |                                                      |             |   '--> tools: calculator,
     |                                                      |             |        current_datetime, web_search
     '-- SSE: token, tool_start, tool_end, ..., done -------'             '--> Postgres checkpointer
                                                                              (chat history per thread)
```

- `backend/app/auth/`: registration, login, refresh, JWT helpers, the `get_current_user` dependency.
- `backend/app/conversations/`: conversation CRUD (SQLAlchemy 2 async, Alembic migrations).
- `backend/app/agent/`: the LangGraph graph (`graph.py`), the model factory (`llm.py`), the Postgres checkpointer (`checkpointer.py`), the prompts, and the tools (`tools/`).
- `backend/app/chat/`: the send-message endpoint and the graph-to-SSE translation (`streaming.py`).
- `frontend/src/`: the API client with transparent token refresh (`api/client.ts`), an SSE parser (`api/sse.ts`), and the chat UI (`chat/`).

New to the stack? [`docs/learning/01-foundation.md`](docs/learning/01-foundation.md) walks through how these pieces fit together, and [`docs/learning/02-agent-tools.md`](docs/learning/02-agent-tools.md) explains the tool loop.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (for Postgres)
- [uv](https://docs.astral.sh/uv/) (it also installs Python 3.12)
- Node.js 22 or newer (Vite, Vitest and jsdom require 22.12 or newer; CI uses Node 22)
- [Ollama](https://ollama.com/), with the default model pulled:

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ```

## Quickstart

From the repository root:

```bash
# 1. Start Postgres (pgvector/pgvector:pg17, host port 5433). The first start
#    also creates the chatbot_test database used by the tests.
docker compose up -d postgres

# 2. Backend. It reads its config from a .env file in backend/; the template
#    lives at the repository root. Change JWT_SECRET before sharing the app.
cd backend
cp ../.env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload   # http://localhost:8000

# 3. Frontend (continuing from backend/; in a fresh terminal at the repository
#    root, use "cd frontend" instead)
cd ../frontend
npm ci
npm run dev                      # http://localhost:5173, proxies /api to :8000
```

Open http://localhost:5173, register an account, and start chatting. Make sure Ollama is running (`ollama serve`, or the desktop app); if it is not, the UI shows a friendly error instead of hanging. The interactive API docs are at http://localhost:8000/docs.

## Switching the LLM provider

The model is built in [`backend/app/agent/llm.py`](backend/app/agent/llm.py) with LangChain's `init_chat_model`, driven only by settings in `.env`:

```bash
LLM_PROVIDER=ollama                         # a provider name init_chat_model understands
LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M
LLM_TEMPERATURE=0.2
OLLAMA_BASE_URL=http://localhost:11434      # only used when LLM_PROVIDER=ollama
```

No application code changes, but each provider ships as its own LangChain package, and only `langchain-ollama` is installed by default. For example, for OpenAI:

```bash
cd backend
uv add langchain-openai
export OPENAI_API_KEY=sk-...
# then in .env:  LLM_PROVIDER=openai  and  LLM_MODEL=gpt-4o-mini
```

The provider's API key is read from its usual environment variable (such as `OPENAI_API_KEY`) by the LangChain integration, not by this app's `Settings`. An unsupported provider, or a missing provider package, fails at startup with a clear error.

## Agent settings

These optional variables (all in [`.env.example`](.env.example)) tune the agent:

| Variable                     | Default | Meaning                                                              |
| ---------------------------- | ------- | -------------------------------------------------------------------- |
| `WEB_SEARCH_MAX_RESULTS`     | `5`     | Results the `web_search` tool returns per query                      |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `10`    | Timeout for one web search                                           |
| `AGENT_MAX_TOOL_ROUNDS`      | `5`     | Tool-calling rounds allowed per message before the model must answer |
| `LLM_MAX_HISTORY_TOKENS`     | `6000`  | Token budget for the history the model sees (the stored one is full) |

## Running the tests

Backend tests need Postgres (the `docker compose` one above is fine). They read `TEST_DATABASE_URL`, which defaults to `postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot_test`. No test calls a real LLM.

```bash
cd backend
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

```bash
cd frontend
npm test -- --run
npm run typecheck
npm run lint
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs these same commands on every push and pull request, with a `pgvector/pgvector:pg17` service container for the backend job.

## Tech stack

Python 3.12, FastAPI, SQLAlchemy 2 (async) and Alembic, Postgres 17 with pgvector, LangChain and LangGraph, Ollama, sse-starlette, pytest. React 19, TypeScript, Vite, TanStack Query, Tailwind CSS with shadcn/ui, Vitest, Testing Library and MSW.
