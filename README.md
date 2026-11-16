# Agentic Chatbot

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A multi-user LLM chatbot built the way a production AI app is built: a **LangGraph agent** behind a **FastAPI** API, conversations persisted in **Postgres**, tokens **streamed** to a **React** UI over Server-Sent Events, and a test suite and CI around all of it. It runs fully locally on a laptop with Ollama, and moving to a hosted model is a configuration change, not a rewrite.

The goal is more than "a chat box". Over the roadmap below the agent grows tools, document Q&A (RAG), MCP servers, tracing, and a measured evaluation, because the engineering around the LLM is the point.

> **Status:** Milestones 1 (foundation), 2 (agent tools), 3 (documents and RAG), 4 (MCP) and 5 (observability) are complete. See the [roadmap](#roadmap).

## Screenshots

<!-- TODO (M7): add a screenshot and a short GIF of a streamed conversation here. -->

_Screenshots and a demo GIF will be added in Milestone 7._

## What works today (M1 to M5)

- **Auth:** register and log in with email and password (argon2 hashes). Short-lived JWT access tokens, with a refresh token held in an httpOnly cookie.
- **Conversations:** create, list, rename and delete. Every query is scoped to the signed-in user, and another user's conversation returns `404`.
- **Streamed chat:** a LangGraph agent (a model node and a tool node in a loop) replies token by token over SSE, with `tool_start`/`tool_end` events when it uses a tool. History lives in a Postgres-backed LangGraph checkpointer, so it survives restarts.
- **Swappable model:** the LLM is chosen by environment variables (Ollama by default).
- **React UI:** login/register, conversation sidebar, and a chat pane with live streaming and Markdown rendering.
- **Agent tools (M2):** the agent loops between the model and tools until it can answer. It has a safe `calculator` (an AST evaluator, never `eval`), `current_datetime`, and `web_search` (keyless metasearch via ddgs; see the caveat under Agent settings). The UI shows a live chip per tool call ("Searching the web...") and keeps them on the saved message. A max-rounds guard stops a looping model, long histories are trimmed for the model, a failed or abandoned reply is rolled back so the thread stays valid, and Markdown images in replies are shown as links instead of being fetched.
- **Documents and RAG (M3):** upload PDF, DOCX, TXT or MD files in a Documents panel. They are ingested in the background (`processing`, then `ready` or `failed` with a reason), chunked, embedded with `nomic-embed-text` and stored in pgvector. The agent's `search_documents` tool searches only the signed-in user's documents (the user id comes from the server, never from the model), and answers show expandable source citations (file, chunk and snippet) that also survive a reload. Another user's document returns `404`.
- **MCP tools (M4):** the agent also loads tools from MCP servers listed in `backend/mcp_servers.json`: a custom `mcp-utils` server (`get_weather`, `convert_units`, `convert_time`; its own service with a Dockerfile, compose entry and CI job) and the third-party `fetch` server for reading web pages (it refuses URLs whose host resolves to a private, loopback or link-local address, checked on every request and redirect). `fetch` is unreliable with the default 4B model: in the latest live check it was called for 1 of 4 public URLs, and otherwise the model answered that it cannot access websites. A server that is down at startup is skipped and shown as `unavailable` in `/api/health`; the app keeps working. See the [MCP section](#mcp).
- **Observability (M5):** an optional Langfuse profile traces every chat turn (prefetch, model calls with token usage, tools) tagged with the user and conversation, and thumbs up/down on replies is saved and sent as a score. Off by default. See [Observability](#observability).
- **Quality gates:** pytest (the LLM is always faked in unit tests), ruff, mypy strict, Vitest, oxlint and `tsc`, all run in GitHub Actions.

## Roadmap

The full design is in the [spec](docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md); the milestone breakdown and cross-milestone decisions (including the SSE event contract) are in the [roadmap](docs/superpowers/plans/2026-11-10-00-roadmap.md).

| #   | Milestone               | Delivers                                                                                              |
| --- | ----------------------- | ----------------------------------------------------------------------------------------------------- |
| M1  | Foundation & thin slice | Postgres, auth, conversations, streamed chat with a LangGraph agent, React UI, CI (**done**)          |
| M2  | Agent tools             | Tool loop; calculator, current time, web search; tool-status events and chips in the UI (**done**)    |
| M3  | Documents & RAG         | Upload PDF/DOCX/TXT/MD, ingestion into pgvector, per-user document search, citations (**done**)       |
| M4  | MCP                     | A custom FastMCP server plus a third-party one, loaded by the agent from config (**done**)            |
| M5  | Observability           | Langfuse tracing tagged by user and conversation, thumbs feedback as scores (**done**)                |
| M6  | Evaluation              | Eval dataset and runner, LLM judge, tool-selection / retrieval / faithfulness metrics, 4B vs 8B model |
| M7  | Production polish       | Dockerfiles, one-command `docker compose up`, rate limits, architecture diagram, screenshots          |

## Architecture

```
React (Vite) --POST /api/conversations/{id}/messages--> FastAPI --> LangGraph agent <--> chat model
     ^                                                      |             |   ^
     |                                                      |             |   '--> tools: calculator,
     |                                                      |             |        current_datetime, web_search,
     |                                                      |             |        search_documents (pgvector)
     |                                                      |             |   '--> MCP (from mcp_servers.json):
     |                                                      |             |        mcp-utils :8100 (HTTP)
     |                                                      |             |        fetch (stdio, uvx)
     '-- SSE: token, tool_start, tool_end, ..., done -------'             '--> Postgres checkpointer
                                                                              (chat history per thread)
```

- `backend/app/auth/`: registration, login, refresh, JWT helpers, the `get_current_user` dependency.
- `backend/app/conversations/`: conversation CRUD (SQLAlchemy 2 async, Alembic migrations).
- `backend/app/agent/`: the LangGraph graph (`graph.py`), the model factory (`llm.py`), the Postgres checkpointer (`checkpointer.py`), the prompts, and the tools (`tools/`).
- `backend/app/documents/`: upload API, background ingestion (extract, chunk, embed), pgvector retrieval, and the `reindex` command.
- `backend/app/agent/mcp.py` and `mcp-utils/`: the MCP client (config, per-server loading, guarded tools) and the custom MCP server.
- `backend/app/chat/`: the send-message endpoint and the graph-to-SSE translation (`streaming.py`).
- `frontend/src/`: the API client with transparent token refresh (`api/client.ts`), an SSE parser (`api/sse.ts`), and the chat UI (`chat/`).

New to the stack? [`docs/learning/01-foundation.md`](docs/learning/01-foundation.md) walks through how these pieces fit together, [`docs/learning/02-agent-tools.md`](docs/learning/02-agent-tools.md) explains the tool loop, [`docs/learning/03-documents-rag.md`](docs/learning/03-documents-rag.md) covers embeddings, ingestion, pgvector and per-user isolation, and [`docs/learning/04-mcp.md`](docs/learning/04-mcp.md) covers MCP servers, failure isolation and the live results.

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
| `AUTO_WEB_SEARCH`            | `true`    | Search the web before answering when the message needs fresh facts (see below)  |
| `KNOWLEDGE_CUTOFF_YEAR`      | `2024`    | Messages naming a later year count as needing fresh facts                       |

**Automatic web search:** small local models rarely decide to search on their own (the default 4B model answered "who won the 2026 FIFA World Cup?" from memory in 10 of 10 live runs, even with today's date in its prompt). So the server applies a fixed rule ([`freshness.py`](backend/app/agent/freshness.py)): a message naming a year after `KNOWLEDGE_CUTOFF_YEAR`, or using words such as _latest, current, news, today, price, won, release_, gets a web search before the model answers, shown as a normal tool chip. Weather, time and URL questions are left to their own tools. The rule can miss a question or search needlessly; the model can still call `web_search` itself. Details in [note 5](docs/learning/05-auto-retrieval.md).

**Web search caveat:** `web_search` relies on a third-party, keyless metasearch library (ddgs) scraping public engines, so it can be slow, rate-limited or fail; the agent then tells the user the search failed. The engines also block automated traffic: on 12 November 2026, from the development machine, every engine returned "No results found" (DuckDuckGo answered with an HTTP 202 challenge page, Brave with HTTP 429), and a few minutes later searches worked again. Some engines return nothing from some networks, so try another `WEB_SEARCH_BACKEND` (default `duckduckgo,brave,yahoo,startpage`; unknown engine names are rejected at startup) if results are empty.

## Documents settings

Supported uploads are `.pdf`, `.docx`, `.txt` and `.md`, up to `MAX_UPLOAD_MB` (default 10; larger files get `413`, unsupported or mismatched files `415`). Scanned PDFs with no text layer are marked `failed`. Uploaded files are stored under `UPLOAD_DIR` (gitignored) and never served back. These optional variables are in [`.env.example`](.env.example):

| Variable                                               | Default            | Meaning                                                                     |
| ------------------------------------------------------ | ------------------ | --------------------------------------------------------------------------- |
| `EMBEDDING_PROVIDER`                                   | `ollama`           | Provider name for LangChain's `init_embeddings`                             |
| `EMBEDDING_MODEL`                                      | `nomic-embed-text` | Embedding model                                                             |
| `EMBEDDING_DIMENSIONS`                                 | `768`              | Vector size; fixes the column size when the migration runs                  |
| `UPLOAD_DIR`                                           | `./data/uploads`   | Where uploaded files are stored                                             |
| `MAX_UPLOAD_MB`                                        | `10`               | Upload size limit                                                           |
| `CHUNK_SIZE`                                           | `1000`             | Characters per chunk                                                        |
| `CHUNK_OVERLAP`                                        | `150`              | Characters shared between neighbouring chunks                               |
| `RETRIEVAL_K`                                          | `4`                | Chunks returned per search (no reranking)                                   |
| `MAX_DOCUMENT_CHUNKS`                                  | `500`              | Documents with more chunks are marked `failed`, not embedded                |
| `AUTO_DOCUMENT_SEARCH`                                 | `true`             | Search the user's documents before every answer                             |
| `AUTO_DOCUMENT_MAX_DISTANCE`                           | `0.48`             | Keep only passages this close to the question (cosine distance)             |
| `EMBEDDING_QUERY_PREFIX` / `EMBEDDING_DOCUMENT_PREFIX` | model default      | Task prefixes; nomic-embed-text gets `search_query: ` / `search_document: ` |

**Changing the embedding model:** each chunk remembers the model that embedded it, and search only uses chunks from the configured model (it tells the model when some were skipped). After changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS`, re-embed everything, resizing the vector column if needed:

```bash
cd backend
uv run python -m app.documents.reindex
```

It exits non-zero if any document fails; fix the cause and run it again.

**Will the model search?** It doesn't have to decide any more. Before every answer, the server searches the signed-in user's ready documents with their message and hands the model the passages within `AUTO_DOCUMENT_MAX_DISTANCE` of it, with citations. This replaced relying on the model: with the default 4B model, a question that didn't mention documents never made it call `search_documents` (0 of 9 runs in M3, 0 of 4 in a later check), whereas with automatic retrieval "tell me about the one digital security assessment review" answered from the uploaded report with 4 citations. The threshold is a trade-off: on the one real report measured, questions about it scored 0.28-0.47 and unrelated ones 0.48-0.57, but some general questions close to the report's subject ("what is a penetration test?", "what is the latest version of python?") scored 0.40-0.45 and also get passages, which the model is free to ignore. Lower the threshold to attach passages less often. Details in [note 5](docs/learning/05-auto-retrieval.md).

**After upgrading from M4:** the task prefixes changed how documents are embedded, so existing chunks count as embedded with a different model until you run the reindex command above once.

## MCP

The agent can load tools from [MCP](https://modelcontextprotocol.io) servers listed in a JSON config file, next to its built-in tools. Two are configured by default: `utils` (this repo's `mcp-utils` server: `get_weather`, `convert_units`, `convert_time`; keyless, weather from Open-Meteo) and `fetch` (the pinned third-party `mcp-server-fetch`, run with `uvx`, tool `fetch`; it refuses URLs whose host resolves to a private, loopback or link-local address, checked on every request and redirect). The default 4B model often doesn't call `fetch`: in the latest live check it read 1 of 4 public URLs and claimed it couldn't access the other three (details in [note 4](docs/learning/04-mcp.md#7-live-results)). The app works with MCP down: a server that can't be reached at startup is skipped with a warning, and a call to a server that has died returns an error string to the model.

Run `mcp-utils` (listens on `127.0.0.1:8100`, path `/mcp`) in either of two ways:

```bash
cd mcp-utils && uv run python -m mcp_utils   # locally
docker compose up -d mcp-utils               # or in Docker
```

Inside its container the server binds `0.0.0.0`, which turns off the SDK's DNS-rebinding check, so compose publishes the port on `127.0.0.1:8100` only and the unauthenticated server is not reachable from the LAN.

The config file is `backend/mcp_servers.json` (`MCP_CONFIG_PATH`, resolved relative to `backend/`). Each key is a server name. Supported transports are `streamable_http` (needs `url`) and `stdio` (needs `command`, optional `args`). An optional `tool_descriptions` object replaces a tool's upstream description; it is used for `fetch`, whose own description tells the model it "did not have internet access" and was "advised to refuse". A missing file means no MCP servers; invalid JSON or an unknown transport fails at startup.

```json
{
  "utils": {
    "transport": "streamable_http",
    "url": "http://localhost:8100/mcp"
  },
  "fetch": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-fetch==2026.10.24", "mcp-server-fetch"],
    "tool_descriptions": {
      "fetch": "Read a web page: fetch a URL from the internet and return its main text as markdown."
    }
  }
}
```

| Variable                      | Default            | Meaning                                                    |
| ----------------------------- | ------------------ | ---------------------------------------------------------- |
| `MCP_CONFIG_PATH`             | `mcp_servers.json` | Server config file                                         |
| `MCP_TOOL_TIMEOUT_SECONDS`    | `30`               | Deadline for one MCP tool call                             |
| `MCP_STARTUP_TIMEOUT_SECONDS` | `20`               | Deadline for loading one server's tools at startup         |
| `MCP_UTILS_HOST`              | `127.0.0.1`        | `mcp-utils` bind address (the Docker image sets `0.0.0.0`) |
| `MCP_UTILS_PORT`              | `8100`             | `mcp-utils` port                                           |

A tool whose name matches a built-in tool or an already-loaded MCP tool is skipped with a warning; built-ins win. `GET /api/health` reports each server: `{"status": "ok", "mcp": {"utils": "ok", "fetch": "unavailable"}}`.

## Observability

Chat turns can be traced in [Langfuse](https://langfuse.com), a self-hosted trace viewer. It is optional: with no keys set, nothing is created and the app behaves exactly as before, and if Langfuse is configured but down, chat and feedback still work (failures are logged as warnings). `/api/health` reports `"langfuse": "off" | "ok" | "unavailable"`, the status checked once at startup (it does not change if Langfuse comes up later; traces still flow once it is up). The `prefetch` observation appears only when automatic retrieval is on.

```bash
# Start Langfuse (an optional Compose profile; plain `docker compose up -d postgres` is unaffected)
docker compose --profile observability up -d
```

1. Wait a minute or two for the first start, then open <http://localhost:3100> and sign in with `admin@chatbot.local` / `chatbot-admin-password`. The organization, the `chatbot` project and its API keys are created on first start.
2. Put the keys in `backend/.env` (they are in [`.env.example`](.env.example)) and restart the backend:

```
LANGFUSE_PUBLIC_KEY=pk-lf-chatbot-local
LANGFUSE_SECRET_KEY=sk-lf-chatbot-local
LANGFUSE_BASE_URL=http://localhost:3100
```

| Variable                   | Default                 | Meaning                                                     |
| -------------------------- | ----------------------- | ----------------------------------------------------------- |
| `LANGFUSE_PUBLIC_KEY`      | unset                   | Tracing is on only when both keys are set (empty = unset)   |
| `LANGFUSE_SECRET_KEY`      | unset                   | See above                                                   |
| `LANGFUSE_BASE_URL`        | `http://localhost:3100` | Langfuse server                                             |
| `LANGFUSE_TIMEOUT_SECONDS` | `5`                     | Most the startup reachability check may wait for the server |

**What a trace shows:** each chat turn is one trace named `chat-turn`, tagged `chat`, with the user id and the conversation id (shown as the session), so you can filter by person or follow one conversation. Inside it are the LangGraph steps: `prefetch` (the automatic document and web retrieval from [note 5](docs/learning/05-auto-retrieval.md)), `agent` (the model call, with its prompt, answer, token usage and latency) and `tools` (each tool call with input and output).

**Feedback:** saved assistant replies have thumbs up/down buttons. A vote is stored in Postgres (`message_feedback`) and sent to Langfuse as a `user_feedback` score on that turn's trace; changing the vote updates the same score and clearing it deletes the score. Why it is stored locally is in [note 6](docs/learning/06-observability.md).

**Langfuse Cloud instead:** skip the profile, create a project at <https://cloud.langfuse.com>, and set the same three variables (`LANGFUSE_BASE_URL=https://cloud.langfuse.com`, plus your own keys). With Langfuse Cloud, full prompts, including retrieved passages from users' uploaded documents, leave the machine.

**Cost:** the profile runs six containers (Langfuse web and worker, plus its own Postgres, ClickHouse, Redis and MinIO), which is noticeable RAM on a laptop that is also running a local model; stop it with `docker compose --profile observability down` when you don't need it. Only the web UI is published, on `127.0.0.1:3100`. All its passwords and keys are local-development defaults; change them (see the `LANGFUSE_*` variables in [`docker-compose.yml`](docker-compose.yml)) before exposing it.

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
