# Agentic Chatbot

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A multi-user LLM chatbot built the way a production AI app is built: a **LangGraph agent** behind a **FastAPI** API, conversations persisted in **Postgres**, tokens **streamed** to a **React** UI over Server-Sent Events, and a test suite and CI around all of it. It runs fully locally on a laptop with Ollama, and moving to a hosted model is a configuration change, not a rewrite.

The goal is more than "a chat box". Over the roadmap below the agent grows tools, document Q&A (RAG), MCP servers, tracing, and a measured evaluation, because the engineering around the LLM is the point.

> **Status:** Milestones 1 (foundation), 2 (agent tools) and 3 (documents and RAG) are complete. See the [roadmap](#roadmap).

## Screenshots

<!-- TODO (M7): add a screenshot and a short GIF of a streamed conversation here. -->

_Screenshots and a demo GIF will be added in Milestone 7._

## What works today (M1 to M3)

- **Auth:** register and log in with email and password (argon2 hashes). Short-lived JWT access tokens, with a refresh token held in an httpOnly cookie.
- **Conversations:** create, list, rename and delete. Every query is scoped to the signed-in user, and another user's conversation returns `404`.
- **Streamed chat:** a LangGraph agent (a model node and a tool node in a loop) replies token by token over SSE, with `tool_start`/`tool_end` events when it uses a tool. History lives in a Postgres-backed LangGraph checkpointer, so it survives restarts.
- **Swappable model:** the LLM is chosen by environment variables (Ollama by default).
- **React UI:** login/register, conversation sidebar, and a chat pane with live streaming and Markdown rendering.
- **Agent tools (M2):** the agent loops between the model and tools until it can answer. It has a safe `calculator` (an AST evaluator, never `eval`), `current_datetime`, and `web_search` (keyless metasearch via ddgs; see the caveat under Agent settings). The UI shows a live chip per tool call ("Searching the web...") and keeps them on the saved message. A max-rounds guard stops a looping model, long histories are trimmed for the model, a failed or abandoned reply is rolled back so the thread stays valid, and Markdown images in replies are shown as links instead of being fetched.
- **Documents and RAG (M3):** upload PDF, DOCX, TXT or MD files in a Documents panel. They are ingested in the background (`processing`, then `ready` or `failed` with a reason), chunked, embedded with `nomic-embed-text` and stored in pgvector. The agent's `search_documents` tool searches only the signed-in user's documents (the user id comes from the server, never from the model), and answers show expandable source citations (file, chunk and snippet) that also survive a reload. Another user's document returns `404`.
- **Quality gates:** pytest (the LLM is always faked in unit tests), ruff, mypy strict, Vitest, oxlint and `tsc`, all run in GitHub Actions.

## Roadmap

The full design is in the [spec](docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md); the milestone breakdown and cross-milestone decisions (including the SSE event contract) are in the [roadmap](docs/superpowers/plans/2026-11-10-00-roadmap.md).

| #   | Milestone               | Delivers                                                                                              |
| --- | ----------------------- | ----------------------------------------------------------------------------------------------------- |
| M1  | Foundation & thin slice | Postgres, auth, conversations, streamed chat with a LangGraph agent, React UI, CI (**done**)          |
| M2  | Agent tools             | Tool loop; calculator, current time, web search; tool-status events and chips in the UI (**done**)    |
| M3  | Documents & RAG         | Upload PDF/DOCX/TXT/MD, ingestion into pgvector, per-user document search, citations (**done**)       |
| M4  | MCP                     | A custom FastMCP server plus a third-party one, loaded by the agent from config                       |
| M5  | Observability           | Langfuse tracing tagged by user and conversation, thumbs feedback as scores                           |
| M6  | Evaluation              | Eval dataset and runner, LLM judge, tool-selection / retrieval / faithfulness metrics, 4B vs 8B model |
| M7  | Production polish       | Dockerfiles, one-command `docker compose up`, rate limits, architecture diagram, screenshots          |

## Architecture

```
React (Vite) --POST /api/conversations/{id}/messages--> FastAPI --> LangGraph agent <--> chat model
     ^                                                      |             |   ^
     |                                                      |             |   '--> tools: calculator,
     |                                                      |             |        current_datetime, web_search,
     |                                                      |             |        search_documents (pgvector)
     '-- SSE: token, tool_start, tool_end, ..., done -------'             '--> Postgres checkpointer
                                                                              (chat history per thread)
```

- `backend/app/auth/`: registration, login, refresh, JWT helpers, the `get_current_user` dependency.
- `backend/app/conversations/`: conversation CRUD (SQLAlchemy 2 async, Alembic migrations).
- `backend/app/agent/`: the LangGraph graph (`graph.py`), the model factory (`llm.py`), the Postgres checkpointer (`checkpointer.py`), the prompts, and the tools (`tools/`).
- `backend/app/documents/`: upload API, background ingestion (extract, chunk, embed), pgvector retrieval, and the `reindex` command.
- `backend/app/chat/`: the send-message endpoint and the graph-to-SSE translation (`streaming.py`).
- `frontend/src/`: the API client with transparent token refresh (`api/client.ts`), an SSE parser (`api/sse.ts`), and the chat UI (`chat/`).

New to the stack? [`docs/learning/01-foundation.md`](docs/learning/01-foundation.md) walks through how these pieces fit together, [`docs/learning/02-agent-tools.md`](docs/learning/02-agent-tools.md) explains the tool loop, and [`docs/learning/03-documents-rag.md`](docs/learning/03-documents-rag.md) covers embeddings, ingestion, pgvector and per-user isolation.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (for Postgres)
- [uv](https://docs.astral.sh/uv/) (it also installs Python 3.12)
- Node.js 22 or newer (Vite, Vitest and jsdom require 22.12 or newer; CI uses Node 22)
- [Ollama](https://ollama.com/), with the default model pulled:

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull nomic-embed-text      # embeddings for document search
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

| Variable                     | Default   | Meaning                                                                         |
| ---------------------------- | --------- | ------------------------------------------------------------------------------- |
| `WEB_SEARCH_MAX_RESULTS`     | `5`       | Results the `web_search` tool returns per query                                 |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `10`      | Timeout and overall deadline for one web search                                 |
| `WEB_SEARCH_BACKEND`         | see below | ddgs engine(s), comma list allowed; keyless, but see the caveat below           |
| `OLLAMA_NUM_CTX`             | `8192`    | Ollama context window; `LLM_MAX_HISTORY_TOKENS` must be at most this minus 1500 |
| `AGENT_MAX_TOOL_ROUNDS`      | `5`       | Tool-calling rounds allowed per message before the model must answer            |
| `LLM_MAX_HISTORY_TOKENS`     | `6000`    | Token budget for the history the model sees (the stored one is full)            |

**Web search caveat:** `web_search` relies on a third-party, keyless metasearch library (ddgs) scraping public engines, so it can be slow, rate-limited or fail; the agent then tells the user the search failed. Some engines return nothing from some networks, so try another `WEB_SEARCH_BACKEND` (default `duckduckgo,brave,yahoo,startpage`; unknown engine names are rejected at startup) if results are empty.

## Documents settings

Supported uploads are `.pdf`, `.docx`, `.txt` and `.md`, up to `MAX_UPLOAD_MB` (default 10; larger files get `413`, unsupported or mismatched files `415`). Scanned PDFs with no text layer are marked `failed`. Uploaded files are stored under `UPLOAD_DIR` (gitignored) and never served back. These optional variables are in [`.env.example`](.env.example):

| Variable               | Default            | Meaning                                                      |
| ---------------------- | ------------------ | ------------------------------------------------------------ |
| `EMBEDDING_PROVIDER`   | `ollama`           | Provider name for LangChain's `init_embeddings`              |
| `EMBEDDING_MODEL`      | `nomic-embed-text` | Embedding model                                              |
| `EMBEDDING_DIMENSIONS` | `768`              | Vector size; fixes the column size when the migration runs   |
| `UPLOAD_DIR`           | `./data/uploads`   | Where uploaded files are stored                              |
| `MAX_UPLOAD_MB`        | `10`               | Upload size limit                                            |
| `CHUNK_SIZE`           | `1000`             | Characters per chunk                                         |
| `CHUNK_OVERLAP`        | `150`              | Characters shared between neighbouring chunks                |
| `RETRIEVAL_K`          | `4`                | Chunks returned per search (no reranking)                    |
| `MAX_DOCUMENT_CHUNKS`  | `500`              | Documents with more chunks are marked `failed`, not embedded |

**Changing the embedding model:** each chunk remembers the model that embedded it, and search only uses chunks from the configured model (it tells the model when some were skipped). After changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS`, re-embed everything, resizing the vector column if needed:

```bash
cd backend
uv run python -m app.documents.reindex
```

It exits non-zero if any document fails; fix the cause and run it again.

**Will the model search?** Each turn the server tells the model which documents the user has (their ready filenames, at most 20), so it knows there is something to search. In live runs with the default 4B model this made "What is the codename of the Zephyr-9 project according to my documents?" call `search_documents` and answer with a citation 3 times out of 3. A question that doesn't mention documents ("What's the Zephyr-9 project's codename?") still never did (0 of 9 live runs across two wordings of the note, including "What's the budget of the Zephyr-9 project?"), so mention your documents when you ask. The measured runs are in [note 3, sections 8 and 9](docs/learning/03-documents-rag.md#8-telling-the-model-what-you-have).

## MCP

The agent can load tools from [MCP](https://modelcontextprotocol.io) servers listed in a JSON config file, next to its built-in tools. Two are configured by default: `utils` (this repo's `mcp-utils` server: `get_weather`, `convert_units`, `convert_time`; keyless, weather from Open-Meteo) and `fetch` (the pinned third-party `mcp-server-fetch`, run with `uvx`, tool `fetch`; it refuses private, loopback and link-local addresses). The app works with MCP down: a server that can't be reached at startup is skipped with a warning, and a call to a server that has died returns an error string to the model.

Run `mcp-utils` (listens on `127.0.0.1:8100`, path `/mcp`) in either of two ways:

```bash
cd mcp-utils && uv run python -m mcp_utils   # locally
docker compose up -d mcp-utils               # or in Docker
```

The config file is `backend/mcp_servers.json` (`MCP_CONFIG_PATH`, resolved relative to `backend/`). Each key is a server name. Supported transports are `streamable_http` (needs `url`) and `stdio` (needs `command`, optional `args`). A missing file means no MCP servers; invalid JSON or an unknown transport fails at startup.

```json
{
  "utils": { "transport": "streamable_http", "url": "http://localhost:8100/mcp" },
  "fetch": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-fetch==2026.10.24", "mcp-server-fetch"]
  }
}
```

| Variable                      | Default            | Meaning                                                            |
| ----------------------------- | ------------------ | ------------------------------------------------------------------ |
| `MCP_CONFIG_PATH`             | `mcp_servers.json` | Server config file                                                 |
| `MCP_TOOL_TIMEOUT_SECONDS`    | `30`               | Deadline for one MCP tool call                                     |
| `MCP_STARTUP_TIMEOUT_SECONDS` | `20`               | Deadline for loading one server's tools at startup                 |
| `MCP_UTILS_HOST`              | `127.0.0.1`        | `mcp-utils` bind address (the Docker image sets `0.0.0.0`)         |
| `MCP_UTILS_PORT`              | `8100`             | `mcp-utils` port                                                   |

A tool whose name matches a built-in tool or an already-loaded MCP tool is skipped with a warning; built-ins win. `GET /api/health` reports each server: `{"status": "ok", "mcp": {"utils": "ok", "fetch": "unavailable"}}`.

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
