# M3 Documents & RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users upload PDF/DOCX/TXT/MD files. The files are chunked and embedded into pgvector in the background, and the agent answers questions about them through a per-user `search_documents` tool, with citations streamed to the UI and shown under the answer.

**Architecture:**

- **Uploads:** an upload stores the file behind a `FileStorage` interface (local disk in dev) and creates a `documents` row with status `processing`. The response comes back immediately, and a FastAPI background task then extracts the text, chunks it, embeds it and writes `document_chunks` (with `user_id`, the embedding model name and the vector) before setting status `ready` or `failed`.
- **Per-user search:** `search_documents` is one graph-wide tool. The **current user's id reaches it through the run config (`configurable.user_id`), set by the server, never by the model**. The model's tool schema only has `query`. This was verified while planning: a model-supplied `user_id` arg is ignored, and the injected config value is used.
- **Citations:** the tool returns its text plus a structured list (`response_format="content_and_artifact"`). The streaming layer turns those lists into one `citations` SSE event before `done`, and the transcript endpoint returns them with the answer.

**Tech Stack:** pgvector (`pgvector` Python package, `vector(N)` column, HNSW cosine index), `langchain.embeddings.init_embeddings` (provider-agnostic; default `ollama:nomic-embed-text`), `pypdf`, `python-docx`, `langchain-text-splitters`, `python-multipart`, React + TanStack Query + shadcn `Sheet`.

**Spec:** `docs/superpowers/specs/2026-11-10-agentic-chatbot-design.md`
**Roadmap (binding decisions, SSE contract):** `docs/superpowers/plans/2026-11-10-00-roadmap.md`
**Branch:** `feat/m3-documents-rag`, cut from `feat/m2-agent-tools`, which is unmerged. The owner asked for M3 before deciding on the merge.

## Global Constraints

- **Isolation (anti-goal #1).** Every document query and every vector search filters by the authenticated user's id on the server. Cross-user access returns **404**. `search_documents` gets `user_id` only from `config["configurable"]["user_id"]`, which `stream_agent_events` sets.
- **Formats and limits.** Supported formats are `.pdf`, `.docx`, `.txt` and `.md`. Default upload limit is **10 MB** (`MAX_UPLOAD_MB=10`). Too large → **413**. Unsupported type or contents that don't match the extension → **415**.
- **Default embedding model.** `nomic-embed-text` via Ollama (`EMBEDDING_PROVIDER=ollama`, `EMBEDDING_MODEL=nomic-embed-text`, `EMBEDDING_DIMENSIONS=768`). Switching provider is config-only, through `init_embeddings`.
- **Embedding changes (spec decision).** Each chunk stores its `embedding_model`. There is one fixed-dimension vector column, sized from `EMBEDDING_DIMENSIONS` when the migration runs. Search only uses chunks whose `embedding_model` matches the configured model, and its text output **warns** when some of the user's chunks don't match. A `reindex` command re-embeds everything and resizes the column if the dimensions changed.
- **Document status** is `processing` | `ready` | `failed`, and `failed` carries a human-readable `error`. Ingestion runs in the background, so the upload response returns quickly. On startup, any document left `processing` is marked `failed` with "Processing was interrupted by a server restart. Delete and upload it again."
- **SSE contract (roadmap).** The new event is `citations` → `{"items": [{"document_id": str, "filename": str, "chunk_index": int, "snippet": str}]}`. It is sent at most once per turn, just before `done`, and only if `search_documents` returned items. Items are de-duplicated by `(document_id, chunk_index)` in first-seen order. Existing events are unchanged.
- **Retrieval defaults** (the spec leaves these to the builder): `CHUNK_SIZE=1000` characters, `CHUNK_OVERLAP=150`, `RETRIEVAL_K=4`, no reranking. They are documented in the README.
- **Snippets** are the chunk text, whitespace-collapsed and truncated to **300** characters.
- **Uploaded files are stored** under `UPLOAD_DIR` (default `./data/uploads`, gitignored), at key `{user_id}/{document_id}{ext}`. Files are never served back.
- Tests never call a real LLM, embedding model or the network. They use `DeterministicFakeEmbedding(size=768)` from `langchain_core.embeddings` and the existing `tests/fakes.py`.
- Backend: `ruff check`, `ruff format --check`, `mypy app` (strict) and `pytest` all pass. Frontend: `lint` (0 errors), `typecheck` and `test` all pass. Learning notes are written for the owner (spec §8).

## Review Focus

1. **User B asks about a topic that only user A's document covers.** B's `search_documents` returns nothing from A (Task 4 test, with two users and real pgvector queries).
2. **A scanned or image-only PDF, or an empty file.** The document ends up `failed` with "No text could be extracted (scanned PDFs are not supported)." It is not `ready` with zero chunks (Task 2 test).
3. **Ollama or the embedding model is down while a document is ingested.** The upload still returns 201, the document becomes `failed` with a readable error, and no partial chunks remain (Task 2 test).
4. **The user asks about a document that is still processing, or has no documents.** The tool says so plainly ("Your documents are still being processed…" / "You have no ready documents…"), and the model doesn't invent content (Task 4 test).
5. **A 50 MB upload, or a `.pdf` that is really an executable.** The server rejects it with 413/415 without reading the whole body into memory: it stops at the limit (Task 3 test).

---

## File Structure

**Backend (new package `app/documents/`)**

- `models.py`: `Document` and `DocumentChunk` ORM models
- `storage.py`: the `FileStorage` protocol and `LocalFileStorage`
- `extraction.py`: `extract_text(filename, data) -> list[str]` (one string per page, or one string for non-PDF) and `chunk_pages(pages, size, overlap) -> list[str]`
- `embeddings.py`: `build_embeddings(settings) -> Embeddings`
- `ingestion.py`: `ingest_document(...)` and `fail_interrupted_documents(...)`
- `retrieval.py`: `search_chunks(...)`
- `service.py`, `schemas.py`, `router.py`: the API
- `reindex.py`: the CLI (`python -m app.documents.reindex`)
- `app/agent/tools/documents.py`: the `search_documents` tool factory
- `alembic/versions/0002_documents.py`
- Modified: `app/config.py`, `app/main.py`, `app/agent/tools/__init__.py`, `app/agent/prompts.py`, `app/chat/streaming.py`, `app/chat/router.py`, `app/chat/schemas.py`, `pyproject.toml`, `.env.example`, `.gitignore`, `tests/conftest.py`

**Frontend**

- `src/documents/api.ts`: `DocumentInfo` type and hooks
- `src/documents/DocumentsPanel.tsx`
- `src/chat/Citations.tsx`
- Modified: `src/chat/useChatStream.ts`, `src/chat/ChatPane.tsx`, `src/chat/api.ts`, `src/chat/toolLabels.ts`, `src/chat/ConversationSidebar.tsx`

---

### Task 1: Document data model, storage, extraction and chunking

**Files:**

- Create: `backend/app/documents/__init__.py`, `models.py`, `storage.py`, `extraction.py`, `backend/alembic/versions/0002_documents.py`, `backend/tests/documents/test_extraction.py`, `backend/tests/documents/test_storage.py`, `backend/tests/documents/test_models.py`
- Modify: `backend/app/config.py`, `backend/pyproject.toml`, `backend/alembic/env.py` (import the new models so the `include_name` filter and metadata see them), `.env.example`, `.gitignore` (`backend/data/`)

**Interfaces (produces):**

- Settings:
  - `embedding_provider: str = "ollama"`
  - `embedding_model: str = "nomic-embed-text"`
  - `embedding_dimensions: int = 768`
  - `upload_dir: str = "./data/uploads"`
  - `max_upload_mb: int = 10`
  - `chunk_size: int = 1000`
  - `chunk_overlap: int = 150`
  - `retrieval_k: int = 4`
- `Document`, table `documents`:
  - `id` (uuid pk)
  - `user_id` (FK users, `ON DELETE CASCADE`, indexed)
  - `filename` (str 255)
  - `content_type` (str 100)
  - `size_bytes` (int)
  - `storage_key` (str)
  - `status` (str 20, default `"processing"`)
  - `error` (text, nullable)
  - `chunk_count` (int, default 0)
  - `created_at`, `updated_at` (timestamptz, server default now)
- `DocumentChunk`, table `document_chunks`:
  - `id` (uuid pk)
  - `document_id` (FK documents, `ON DELETE CASCADE`, indexed)
  - `user_id` (uuid, indexed; denormalised so the vector search filters without a join)
  - `chunk_index` (int)
  - `content` (text)
  - `embedding` (`Vector(settings.embedding_dimensions)`)
  - `embedding_model` (str 200)
  - unique constraint on `(document_id, chunk_index)`
- The migration creates an HNSW index `ix_document_chunks_embedding` with `vector_cosine_ops`. It reads the dimension from `get_settings().embedding_dimensions` at upgrade time, and its docstring explains that `reindex` resizes it later.
- `class FileStorage(Protocol)`:
  - `async def save(self, key: str, data: bytes) -> None`
  - `async def read(self, key: str) -> bytes`
  - `async def delete(self, key: str) -> None` (a missing key is fine)
- `LocalFileStorage(base_dir: str | Path)`:
  - Rejects keys that resolve outside `base_dir` with `ValueError`.
  - Uses `asyncio.to_thread` for disk I/O and creates parent directories.
- `SUPPORTED_EXTENSIONS = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/markdown"}`
- `class ExtractionError(ValueError)`
- `extract_text(filename: str, data: bytes) -> list[str]`:
  - PDF: one entry per page (pypdf).
  - DOCX: one entry with the paragraphs joined by `"\n"` (python-docx).
  - TXT/MD: one entry, UTF-8 with `errors="replace"`, BOM stripped.
  - Raises `ExtractionError("No text could be extracted (scanned PDFs are not supported).")` when the total stripped text is empty.
  - Raises `ExtractionError("The file could not be read as <TYPE>.")` on parser errors.
- `chunk_pages(pages: list[str], size: int, overlap: int) -> list[str]`: `RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)` over the pages, joined with `"\n\n"`. Empty or whitespace-only chunks are dropped.

- [ ] **Step 1: Write the failing tests**
  - Extraction:
    - Build a tiny PDF in the test from a hand-written minimal PDF byte string with one text page saying "Hello PDF world", checked in as `tests/documents/fixtures/hello.pdf`. Also build a PDF with one blank page.
    - Build a DOCX with python-docx into a `BytesIO`.
    - Plain text with a BOM.
    - Expected results: `extract_text("a.pdf", hello) == ["Hello PDF world"]` (strip and compare). The blank PDF raises `ExtractionError` with the "scanned PDFs" text. Random bytes named `x.pdf` raise `ExtractionError` "could not be read as PDF". An empty `.txt` raises the no-text error.
  - Chunking: 3500 characters of words at size 1000 and overlap 150 gives more than one chunk, each at most 1000 characters, and consecutive chunks share text.
  - Storage (`tmp_path`): save/read round trip, delete of a missing key is a no-op, and `"../escape"` raises `ValueError`.
  - Models (DB): insert a `Document` and two chunks with 768-dim vectors through `db_session`. Deleting the user cascades to both tables. A duplicate `(document_id, chunk_index)` raises `IntegrityError`.
- [ ] **Step 2: Run them to see them fail.** `cd backend && uv run pytest tests/documents -v` should fail on imports.
- [ ] **Step 3: Implement.** `uv add pgvector pypdf python-docx python-multipart langchain-text-splitters`, then the code above. Extend the `_truncate_users` conftest fixture only if the CASCADE doesn't already cover the new tables (it should).
- [ ] **Step 4: Run all backend checks.** `uv run alembic upgrade head` against the dev DB and the full suite should pass, with ruff and mypy clean.
- [ ] **Step 5: Commit**: `feat(documents): document tables, file storage, text extraction and chunking`

---

### Task 2: Embeddings and background ingestion

**Files:**

- Create: `backend/app/documents/embeddings.py`, `backend/app/documents/ingestion.py`, `backend/tests/documents/test_ingestion.py`
- Modify: `backend/app/main.py` (create storage and embeddings in the lifespan, store them on `app.state`, call `fail_interrupted_documents` at startup; new test seam `embeddings: Embeddings | None = None`), `backend/tests/conftest.py` (an `embeddings` fixture returning `DeterministicFakeEmbedding(size=768)` passed to `create_app`, and an `upload_dir` set to `tmp_path` via `test_settings`)

**Interfaces:**

- Consumes: Task 1 models, storage, extraction and settings.
- Produces:
  - `build_embeddings(settings) -> Embeddings`: `init_embeddings(f"{provider}:{model}", **({"base_url": settings.ollama_base_url} if provider == "ollama" else {}))`. An unsupported provider raises `ValueError` naming it.
  - `async def ingest_document(document_id: UUID, *, sessionmaker: async_sessionmaker[AsyncSession], storage: FileStorage, embeddings: Embeddings, settings: Settings) -> None`
    - Never raises.
    - Reads the file, extracts, chunks, then `await embeddings.aembed_documents(chunks)` in batches of 32.
    - Writes all chunks (`embedding_model=settings.embedding_model`) and sets `status="ready"`, `chunk_count=n`, `error=None` **in one transaction**.
    - On `ExtractionError` sets `failed` with its message.
    - On any other exception, it logs the traceback and sets `failed` with "Couldn't process this document: the embedding model may be unavailable. Delete it and try again." No chunks are written.
    - A document that was deleted mid-ingest (row gone) is a quiet no-op.
  - `async def fail_interrupted_documents(sessionmaker) -> int`: marks every `processing` document `failed` with the restart message from Global Constraints and returns the count.

- [ ] **Step 1: Write the failing tests** (`test_ingestion.py`; create the user and document rows directly through `db_session` and save the file with `LocalFileStorage(tmp_path)`):
  - A `.txt` of about 2500 characters becomes `ready`. `chunk_count == len(chunks in DB) > 1`. Every chunk has `embedding_model == "nomic-embed-text"` and a 768-length embedding.
  - A blank PDF becomes `failed` with the scanned-PDF message and 0 chunks.
  - Embeddings that raise `ConnectionError` give `failed` with the "embedding model may be unavailable" message and 0 chunks.
  - A document deleted before ingest runs: no exception, no rows.
  - `fail_interrupted_documents` flips 2 processing docs to failed with the restart message, leaves a ready doc alone, and returns 2.
  - `build_embeddings(Settings(embedding_provider="nope", ...))` raises `ValueError`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend checks.**
- [ ] **Step 5: Commit**: `feat(documents): background ingestion with provider-agnostic embeddings`

---

### Task 3: Documents API

**Files:**

- Create: `backend/app/documents/schemas.py`, `service.py`, `router.py`, `backend/tests/documents/test_documents_api.py`
- Modify: `backend/app/main.py` (include the router under `/api`)

**Interfaces (produces):** all routes require auth. Another user's document returns **404** "Document not found".

- `POST /api/documents` (multipart field `file`) → **201** `DocumentOut`, then schedules `ingest_document` via `BackgroundTasks`.
  - Validation order:
    1. The extension is in `SUPPORTED_EXTENSIONS`, else 415 "Unsupported file type. Upload a PDF, DOCX, TXT or MD file."
    2. Read the upload in 1 MB pieces and stop once the total exceeds `max_upload_mb`, giving 413 "File is larger than {N} MB."
    3. Empty file → 400 "The file is empty."
    4. Content sniff: `.pdf` must start with `%PDF-`, `.docx` must start with `PK\x03\x04`, and `.txt`/`.md` must not contain a NUL byte in the first 8 KB. Otherwise 415 "The file's contents don't match its extension."
  - The filename is stored as the basename only (no path), trimmed to 255 characters.
- `GET /api/documents` → `list[DocumentOut]`, newest first.
- `GET /api/documents/{id}` → `DocumentOut`.
- `DELETE /api/documents/{id}` → **204**. Deletes the row (chunks cascade) and then the stored file.
- `DocumentOut`: `{id, filename, content_type, size_bytes, status, error, chunk_count, created_at}`.

- [ ] **Step 1: Write the failing tests** (the `client` fixture; httpx `files=`):
  - Upload a .txt → 201 with `status == "processing"`. After the response (httpx ASGITransport runs background tasks before returning), `GET` shows `ready` with `chunk_count >= 1`.
  - `.exe` → 415.
  - `fake.pdf` containing `b"MZ..."` → 415.
  - An 11 MB .txt with `max_upload_mb=10` → 413. Assert through a spy on the read loop, or by reading at most 11 pieces, that the server didn't buffer past the limit.
  - Empty file → 400.
  - The list is newest first.
  - User B can't GET or DELETE A's document (404), and B's list is empty.
  - DELETE removes the rows and the file in storage.
  - A filename of `"../../evil.txt"` is stored as `"evil.txt"`.
- [ ] **Step 2: Run them to see them fail.** **Step 3: Implement.** **Step 4: Run all backend checks.**
- [ ] **Step 5: Commit**: `feat(documents): upload, list, get and delete endpoints`

---

### Task 4: Per-user retrieval and the `search_documents` tool

**Files:**

- Create: `backend/app/documents/retrieval.py`, `backend/app/agent/tools/documents.py`, `backend/tests/documents/test_retrieval.py`, `backend/tests/agent/test_documents_tool.py`
- Modify: `backend/app/agent/tools/__init__.py`, `backend/app/main.py`, `backend/app/agent/prompts.py`, `backend/app/chat/streaming.py` and `backend/app/chat/router.py` (pass `user_id`)

**Interfaces:**

- `@dataclass(frozen=True) class RetrievedChunk`: `document_id: UUID`, `filename: str`, `chunk_index: int`, `content: str`, `distance: float`
- `async def search_chunks(session, *, user_id: UUID, query_vector: list[float], embedding_model: str, k: int) -> list[RetrievedChunk]`
  - Cosine distance (`embedding.cosine_distance(vec)`), ordered ascending, limit `k`.
  - Filters: `DocumentChunk.user_id == user_id` AND `embedding_model == embedding_model` AND document `status == "ready"`.
- `async def count_mismatched_chunks(session, *, user_id, embedding_model) -> int`
- `make_search_documents_tool(*, sessionmaker, embeddings, settings) -> BaseTool`:
  - Name `"search_documents"`, `response_format="content_and_artifact"`, signature `(query: str, config: RunnableConfig)`. The schema exposed to the model is `{query}` only.
  - Reads `user_id = config["configurable"]["user_id"]`. If it's missing, return `("Error: document search is unavailable in this context.", [])`.
  - Content: numbered results `"[{n}] {filename} (chunk {chunk_index})\n{content}"`, joined by blank lines.
    - If the user has `processing` documents and no results: "Your documents are still being processed. Try again in a moment."
    - If no ready documents: "You have no ready documents to search. Upload one from the Documents panel."
    - If mismatched chunks exist, append "Note: {n} chunks were indexed with a different embedding model and were skipped; ask the administrator to run the reindex command."
  - Artifact: `[{"document_id": str, "filename": str, "chunk_index": int, "snippet": <collapsed, ≤300 chars>}]`.
  - Errors from embeddings or the DB return `("Error: document search failed. Answer without the documents and say so.", [])`.
- `build_tools(settings, *, sessionmaker, embeddings) -> list[BaseTool]`: the three M2 tools plus `search_documents`, last.
- `stream_agent_events(graph, thread_id, user_text, user_id: str)`: `config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}`. The router passes `str(user.id)`.
- Prompt: use `search_documents` when the user mentions their documents, files or uploads, or asks something their documents likely cover. Cite with the bracket numbers, e.g. `[1]`. If the tool finds nothing relevant, say so.

- [ ] **Step 1: Write the failing tests**
  - `test_retrieval.py` (real pgvector):
    - Users A and B. A's chunk text "zebra migration", B's "quantum tunnelling". Embed with `DeterministicFakeEmbedding` and query with the embedding of A's exact text.
    - As B, `search_chunks` never returns A's chunk; as A, it returns it first.
    - A chunk with `embedding_model="other"` is excluded and counted by `count_mismatched_chunks`.
    - A chunk of a `processing` document is excluded.
  - `test_documents_tool.py`:
    - Invoke the tool through a graph with `ScriptedChatModel` calling `search_documents` with args `{"query": ..., "user_id": "<A's id>"}` while `configurable.user_id` is B → the ToolMessage contains none of A's text (the model-supplied `user_id` is ignored).
    - The artifact shape matches, and the snippet is ≤ 300 characters.
    - The no-documents, still-processing and mismatch-note texts.
    - Embeddings raising → the "document search failed" text and an empty artifact.
  - `test_streaming.py`: an existing streaming test asserts `user_id` reaches `configurable`, using a tool that echoes `config["configurable"]["user_id"]`.
- [ ] **Step 2: Run them to see them fail.** **Step 3: Implement.** **Step 4: Run all backend checks.**
- [ ] **Step 5: Commit**: `feat(agent): per-user search_documents tool over pgvector`

---

### Task 5: Citations in the stream and the transcript

**Files:**

- Modify: `backend/app/chat/streaming.py`, `backend/app/chat/router.py`, `backend/app/chat/schemas.py`
- Test: `backend/tests/chat/test_streaming.py`, `backend/tests/chat/test_chat_api.py`

**Interfaces:**

- **Streaming:**
  - For every `ToolMessage` from the `"tools"` update whose `name == "search_documents"` and whose `artifact` is a list, collect the items.
  - Before `done`, if any were collected, yield one `citations` event `{"items": [...]}`, de-duplicated by `(document_id, chunk_index)` in first-seen order.
  - Nothing is sent on error or abandonment.
- **Transcript:** `MessageOut.citations: list[CitationOut] = Field(default_factory=list)`, where `CitationOut = {document_id, filename, chunk_index, snippet}`. It is attached to the same assistant message that gets `tools`, using the same de-duplication.

- [ ] **Step 1: Write the failing tests**
  - Streaming: a scripted run calls a fake `search_documents` tool that returns `content_and_artifact` with 2 items, one of them duplicated across two calls → exactly one `citations` event, right before `done`, with the de-duplicated items. A run without the tool sends no `citations` event.
  - API: after such a run, GET messages shows the assistant message with `citations` of length 2 and the user message with `[]`.
- [ ] **Step 2: Run them to see them fail.** **Step 3: Implement.** **Step 4: Run all backend checks.**
- [ ] **Step 5: Commit**: `feat(chat): stream and persist document citations`

---

### Task 6: `reindex` command

**Files:**

- Create: `backend/app/documents/reindex.py`, `backend/tests/documents/test_reindex.py`

**Interfaces:**

- `async def reindex(*, sessionmaker, storage, embeddings, settings) -> ReindexResult`, with `ReindexResult = (documents: int, resized_from: int | None)`:
  1. Read the current column dimension: `SELECT atttypmod FROM pg_attribute WHERE attrelid='document_chunks'::regclass AND attname='embedding'`. For pgvector the typmod is the dimension.
  2. If it differs from `settings.embedding_dimensions`, then in one transaction: `DELETE FROM document_chunks`, drop the HNSW index, `ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(N)`, and recreate the index.
  3. Set every document to `processing`, then run `ingest_document` for each, sequentially.
- `python -m app.documents.reindex` uses the real settings, prints a summary line (`"Reindexed {n} documents with {model}"` plus `" (resized vectors {old} → {new})"`), and exits non-zero on failure. The module docstring explains when to run it (after changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS`).

- [ ] **Step 1: Write the failing tests:**
  - Same dimensions: two ready docs with `embedding_model="old"` → after `reindex`, all chunks have `"nomic-embed-text"` and both docs are `ready`, with `resized_from is None`.
  - Dimension change: settings with `embedding_dimensions=384` plus `DeterministicFakeEmbedding(size=384)` → `resized_from == 768`, the column typmod is 384, and docs are ready. **In a `finally`, resize back to 768 with the same helper**, so other tests keep a 768 column.
- [ ] **Step 2: Run them to see them fail.** **Step 3: Implement.** **Step 4: Run all backend checks.**
- [ ] **Step 5: Commit**: `feat(documents): reindex command for embedding model changes`

---

### Task 7: Frontend documents panel and citations

**Files:**

- Create: `frontend/src/documents/api.ts`, `frontend/src/documents/DocumentsPanel.tsx`, `frontend/src/documents/DocumentsPanel.test.tsx`, `frontend/src/chat/Citations.tsx`, `frontend/src/chat/Citations.test.tsx`
- Modify: `frontend/src/chat/ConversationSidebar.tsx` (a "Documents" button above the account footer that opens the panel), `src/chat/api.ts` (`ChatMessage.citations?: Citation[]`), `src/chat/useChatStream.ts` (`citations: Citation[]`, set from the `citations` event with a type guard, reset per send), `src/chat/ChatPane.tsx` (render `<Citations>` under live and history assistant messages), `src/chat/toolLabels.ts` (`search_documents`: "Searching your documents…" / "Searched your documents"), `src/chat/ChatPane.test.tsx`

**Interfaces:**

- `type DocumentInfo = { id; filename; content_type; size_bytes; status: 'processing' | 'ready' | 'failed'; error: string | null; chunk_count; created_at }`
- Hooks: `useDocuments()`, `useUploadDocument()` (multipart via `apiFetch` with `FormData`, no JSON content-type) and `useDeleteDocument()`.
  - `useDocuments` uses `refetchInterval: (q) => q.state.data?.some(d => d.status === 'processing') ? 2000 : false`.
  - The query key is `['documents']`, cleared with the rest of the cache on logout (`clearSession` already clears everything).
- `type Citation = { document_id: string; filename: string; chunk_index: number; snippet: string }`

**Behaviour:**

- **The panel** is a shadcn `Sheet` (right side) titled "Documents".
  - Upload button: a hidden `<input type=file accept=".pdf,.docx,.txt,.md">`.
  - A client-side check before the request (extension and ≤ 10 MB) shows the same messages as the server.
  - Server errors (`detail`) are shown in an inline alert.
  - List rows show the filename, size (KB/MB), a status badge ("Processing…" with a spinner, "Ready · N chunks", "Failed") and, for failed rows, the `error` text.
  - Delete uses the existing `alert-dialog` confirm. Empty state: "No documents yet. Upload a PDF, DOCX, TXT or MD file to ask questions about it."
- **`<Citations items>`** goes under an assistant answer.
  - A "Sources" label, then one row per item: `FileText` icon, filename and "· chunk {chunk_index + 1}".
  - Each row is a `<details>` that shows the snippet when expanded.
  - Nothing is rendered when `items` is empty.

- [ ] **Step 1: Write the failing tests** (MSW):
  - The panel lists docs with the right badges and shows the error text for a failed doc.
  - Uploading a `.exe` shows the client-side message and makes no request.
  - Uploading a `.txt` POSTs multipart and the new row appears.
  - A processing doc is polled until ready. Use fake timers, or a short `refetchInterval` override via a prop for tests.
  - Delete with confirm removes the row.
  - ChatPane: SSE frames `tool_start(search_documents)`, `tool_end`, `token`, `citations{items:[…]}`, `done` → "Searching your documents…" appears, then "Sources" with the filename.
  - History with `citations` renders Sources.
  - A malformed `citations` payload is ignored.
- [ ] **Step 2: Run them to see them fail.** **Step 3: Implement**, matching the existing visual language. **Step 4:** `npm test -- --run && npm run typecheck && npm run lint` all pass, with 0 lint errors.
- [ ] **Step 5: Commit**: `feat(frontend): documents panel and answer citations`

---

### Task 8: Live check, learning note and docs

**Files:** Create `docs/learning/03-documents-rag.md`. Modify `README.md` and the roadmap.

- [ ] **Step 1: Live check** with real Ollama (`nomic-embed-text` is pulled) on a spare backend port (8010). Never touch the owner's servers on 8000/5173.
  - Run `uv run alembic upgrade head`.
  - Register a user. Upload a short .md file with distinctive made-up facts (e.g. "The Zephyr-9 project codename is BLUE HERON; its budget is 42,000 credits") and wait for `ready`.
  - Ask "What is the codename of the Zephyr-9 project according to my documents?" and confirm `tool_start(search_documents)`, a `citations` event naming the file, and a correct answer.
  - Upload a blank PDF and confirm it shows `failed`.
  - Register a second user, ask the same question, and confirm no citation from user 1 appears.
  - Record the results honestly in the note.
- [ ] **Step 2: Write `docs/learning/03-documents-rag.md`** in the style of the earlier notes. Cover:
  - what embeddings and vector search are
  - the ingestion pipeline (status lifecycle and background task)
  - chunking choices
  - pgvector (column, HNSW, cosine)
  - how isolation is enforced (`user_id` filter plus config injection rather than a tool argument), and why that matters
  - `content_and_artifact` and citations
  - the reindex story
  - key files, and how to watch it work
- [ ] **Step 3: Update the README** (features, document limits, new env vars, retrieval defaults, the `reindex` command, and `ollama pull nomic-embed-text` in the quickstart) **and the roadmap** (set the M3 plan cell).
- [ ] **Step 4: Run the full suites** (backend and frontend). Everything should pass.
- [ ] **Step 5: Commit**: `docs: M3 learning notes, README and roadmap`
