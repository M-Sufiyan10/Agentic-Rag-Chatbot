# Agentic Chatbot: Design

**Date:** 2026-11-10
**Status:** Draft for review

## 1. Intent and why

The owner is a developer building a **portfolio project** for recruiters to look at. It also serves as an **end-to-end learning exercise** in modern LLM application engineering. The product is a general-purpose, multi-user chatbot that can:

- answer general questions directly
- answer questions about documents the user uploaded (RAG)
- use built-in tools
- use tools exposed by MCP servers to reach external resources

The engineering around the LLM is as important as the chat itself: **observability** (tracing every step), **LLM evaluation** (measured quality with real numbers), auth, tests, CI and containerised deployment. A recruiter skimming the repo should come away thinking "this person can ship a production-shaped AI app", not "this is a notebook demo".

Today the app runs fully locally on a CPU-only laptop using Ollama. Later it will switch to a hosted LLM provider and be deployed to the cloud, and that switch should be a config change, not a rewrite.

## 2. Goals

1. **Multi-user web app.** Users register with email and password, log in, and see only their own conversations and documents.
2. **One chat interface for all query types.** A LangGraph agent decides per message whether to answer directly, search the user's documents, call a built-in tool or call an MCP tool.
3. **Document upload and Q&A.** Supported formats: PDF, DOCX, TXT and MD. Documents are chunked, embedded and stored per user. Answers that use documents cite the source document and chunk.
4. **MCP integration.**
   - The agent loads tools from configured MCP servers at startup.
   - At least one third-party MCP server is used.
   - At least one custom MCP server lives in this repo, so both the client and server sides of MCP are demonstrated.
5. **Streaming UX.** Tokens stream to the browser, and the UI shows which tools the agent is calling and when, for example "Searching your documents…" or "Calling fetch_url…".
6. **Persistent conversations.**
   - Chat history and agent state survive restarts (LangGraph checkpointer on Postgres).
   - Users can list, open, rename and delete conversations.
7. **Observability.**
   - Every agent run is traced in Langfuse, tagged with user id and conversation (session) id.
   - Each trace shows LLM calls, tool calls, retrieval, token counts and latency.
   - Thumbs up/down feedback in the UI is recorded as a Langfuse score on the trace.
8. **Evaluation.** A versioned eval dataset of about 15–25 cases in the repo, and one command that runs it. The run measures:
   - tool-selection accuracy
   - retrieval hit rate
   - answer faithfulness/correctness, scored by an LLM judge

   It writes a markdown report, which is committed to the repo, and also logs the run to Langfuse.

9. **Provider-agnostic LLM layer.** Chat model and embedding model are selected by environment variables. Ollama is the default, and switching to OpenAI or Anthropic (or another LangChain-supported provider) needs no code changes.
10. **Production-shaped repo.**
    - `docker compose up` runs the whole stack.
    - Config comes from env vars (`.env.example` provided).
    - Backend and frontend have tests.
    - GitHub Actions CI runs lint, type checks and tests.
    - The README has an architecture diagram, setup steps, screenshots or GIF, the eval results and Langfuse trace screenshots.

## 3. Non-goals (for this version)

- OAuth or social login, email verification, password-reset emails
- Billing, quotas per plan, admin dashboard
- Actually deploying to a cloud provider. The app must be **deployable** (containers plus env config) but deployment itself is out of scope.
- Sharing conversations or documents between users
- Voice, image input or image generation
- Fine-tuning models

## 4. Anti-goals (failure even if it "works")

- **One user can see or retrieve another user's documents or chats.** Isolation must be enforced on the server, in every query and every vector search, not just hidden in the UI.
- **The app breaks when Langfuse is down or not running.** Observability is optional at runtime; the chat must work without it.
- **Switching LLM provider requires editing code.**
- **It looks like a tutorial copy-paste.** Examples: a single giant file, secrets committed, no tests, or a README that is just a list of commands.
- **Slow and silent UI.** On a CPU-only laptop replies are slow, so the UI must always show progress (streaming tokens, tool-status indicators) rather than a frozen spinner.
- **An MCP server can touch the host filesystem or other users' data.** Any filesystem-like MCP server must be sandboxed per user, or not used.

## 5. Constraints

- **Dev hardware.** Intel i7-8665U (8 threads), 32 GB RAM, **no dedicated GPU**. Ollama runs on the CPU.
- **Default chat model.** `qwen3:4b-instruct-2507-q4_K_M`, which is already pulled. It must support tool calling. An 8B model (for example `qwen3:8b`) should be selectable by config for comparison, but it is not the default.
- **Default embedding model.** `nomic-embed-text` via Ollama.
- **No paid API keys available today.** Nothing in the default setup may require one, and every external tool used by default must be keyless. Web search via DuckDuckGo and weather via Open-Meteo are examples of keyless services.
- **Tooling on the machine.** Python 3.12, `uv`, Docker 29 with Compose v2, Ollama 0.40.
- **Ollama runs on the host, not in Compose.** It is already installed and needs host CPU access. Containers reach it via `host.docker.internal`.
- **Evals are slow on CPU.** Keep the default eval set small. Evals run on demand, not on every CI run. CI runs only unit tests with mocked LLMs.

## 6. Decisions

The owner made the decisions marked **(owner)**. The others are Claude's calls during design, and the owner can overturn them.

| Area                     | Decision                                                                                                                                                                                             | Who    |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| Purpose                  | Portfolio and learning; generic multi-purpose chatbot                                                                                                                                                | owner  |
| Agent framework          | LangChain + LangGraph                                                                                                                                                                                | owner  |
| Capabilities             | Doc upload + RAG, tools, MCP, evals, observability                                                                                                                                                   | owner  |
| LLM today                | Local Ollama model; hosted provider later via config                                                                                                                                                 | owner  |
| Auth                     | Self-registration and login, multi-user, cloud-deployable                                                                                                                                            | owner  |
| Frontend                 | React                                                                                                                                                                                                | owner  |
| Backend language         | Python (FastAPI), because LangGraph and the eval/observability tools are most mature in Python                                                                                                       | Claude |
| Frontend stack           | Vite + React + TypeScript + Tailwind + shadcn/ui; TanStack Query for server state. Vite rather than Next.js because the backend is a separate API, so SSR adds nothing.                              | Claude |
| Database                 | Postgres for users, conversations, documents metadata and the LangGraph checkpointer                                                                                                                 | Claude |
| Vector store             | **pgvector** in the same Postgres. One fewer service, real SQL filtering by `user_id`, and cloud-friendly (any managed Postgres works). This replaces Chroma, which came up earlier in conversation. | Claude |
| Auth mechanism           | Email + password, argon2/bcrypt hashing, JWT access token + refresh token in an httpOnly cookie                                                                                                      | Claude |
| Observability            | Langfuse, self-hosted via Docker Compose (separate compose profile so it can be left off)                                                                                                            | Claude |
| Streaming                | Server-Sent Events from FastAPI                                                                                                                                                                      | Claude |
| MCP client               | `langchain-mcp-adapters`, servers listed in a config file                                                                                                                                            | Claude |
| Third-party MCP server   | `fetch` (read a web page by URL)                                                                                                                                                                     | Claude |
| Custom MCP server        | A small Python FastMCP server in this repo exposing keyless utilities: weather (Open-Meteo), unit/currency-free conversions and time zones. Runs as its own container.                               | Claude |
| Filesystem MCP server    | **Not used.** It risks violating the isolation anti-goal; document Q&A covers the "my files" use case.                                                                                               | Claude |
| Built-in (non-MCP) tools | `search_documents` (RAG over the current user's docs), `web_search` (DuckDuckGo), `calculator`, `current_datetime`                                                                                   | Claude |
| Eval approach | Custom runner over a YAML/JSON dataset. The **judge model is configured separately** from the chat model (`EVAL_JUDGE_MODEL`, default `qwen3:8b`) so the model is not grading itself. Results go to markdown and to Langfuse datasets/scores. | Claude |
| Eval results policy | No minimum pass mark. The README reports scores honestly, as a **4B vs 8B comparison table**, plus one documented improvement round (prompt tuning and/or a router node) with before/after numbers. An honest 'here's what I measured and improved' story is stronger for recruiters than inflated numbers. | Claude |
| Embedding provider changes | Re-ingesting is acceptable. Store the embedding model name per chunk, use one fixed-dimension vector column sized from config, and provide a `reindex` command. Search refuses or warns when stored chunks don't match the configured model. | Claude |
| Langfuse hosting | Self-hosted **Langfuse v3** behind a Compose profile (`--profile observability`), started only when needed. Because the SDK is the same, Langfuse Cloud's free tier works by just setting env vars; the README documents both options. | Claude |
| Python tooling           | `uv`, `ruff`, `mypy` (or `pyright`), `pytest`                                                                                                                                                        | Claude |
| Frontend tooling         | `pnpm`, ESLint, Vitest + React Testing Library                                                                                                                                                       | Claude |

## 7. How it works (shape, not a plan)

### Request flow

1. The browser sends a message to `POST /api/conversations/{id}/messages`, authenticated by JWT.
2. FastAPI resolves the user and loads the LangGraph agent with `thread_id = conversation_id`. The Postgres checkpointer restores state.
3. The agent graph:
   - **agent** node: the LLM with tools bound. It decides to answer or call tools.
   - **tools** node: executes the calls. `search_documents` is bound to the current `user_id` at request time, never chosen by the LLM.
   - The graph loops until the agent answers, with a max-iterations guard.
   - When history gets long, a summarisation/trim step keeps the context within the small model's window.
4. Events (tokens, tool start/end, final answer, citations) stream back over SSE.
5. A Langfuse callback, if configured, traces the whole run with `user_id` and `session_id`.

### Document ingestion

1. Upload, with size and type validation.
2. Store the file. Use a local volume in dev; storage goes behind an interface so S3 can be added later.
3. Extract text, chunk it, embed it and write to pgvector with `user_id` and `document_id`.
4. Document status (`processing` / `ready` / `failed`) is visible in the UI. Ingestion runs in the background so the upload request returns quickly.

### Services in Compose

- `frontend`
- `backend`
- `postgres` (with pgvector)
- `mcp-utils` (the custom MCP server)
- an optional `langfuse` profile (Langfuse and its dependencies)

Ollama stays on the host.

## 8. Build approach

- **Claude writes the code; the owner reviews it and learns from it (owner).** There is no deadline, so the plan should be complete rather than trimmed.
- Work goes in small milestones that each leave a working app. The first is a thin end-to-end slice (register → login → chat → streamed answer), and each later milestone adds one capability: RAG, tools, MCP, observability, evals, polish.
- Each milestone ends with a short **"What you just learned" walkthrough** (`docs/learning/NN-<topic>.md`). It explains the concepts, the key files, and how to see it working, for example which Langfuse view to open. That way the owner can explain every layer.
- Code favours clarity over cleverness: small modules, docstrings on the non-obvious LLM/graph parts, and no hidden magic.

## 9. Left to the builder

These are the builder's call. Pick something sensible and note it in the README or code:

- exact chunk size/overlap, retriever `k`, and whether to add reranking
- graph details beyond the shape above, for example whether to add an explicit router node, as long as tool choice stays observable in traces
- DB schema details and migration tool (Alembic expected)
- the exact SSE event format
- UI layout and visual design (it should look clean and modern: sidebar of conversations, a documents panel, a chat pane with tool-status chips and citations)
- rate limiting approach (some per-user limit on messages and uploads should exist)
- how `fetch` MCP is run (`uvx mcp-server-fetch` inside the backend container vs its own container)
- the eval judge prompt and scoring scale
- the build order of milestones, though the plan should get a thin end-to-end slice working early (login → chat → streamed answer) before widening

## 10. Success looks like

- A new visitor clones the repo, copies `.env.example`, runs `ollama pull` plus `docker compose up`, registers, uploads a PDF and gets a cited answer, all by following the README.
- The README shows:
  - an eval results table, for example tool-selection accuracy, retrieval hit rate and judge-scored faithfulness for the default model
  - a Langfuse trace screenshot of a multi-tool run
- CI is green.
- The owner can explain every layer, since learning is half the point.
