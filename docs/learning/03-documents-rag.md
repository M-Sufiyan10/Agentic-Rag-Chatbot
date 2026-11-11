# Learning note 3: documents and RAG (M3)

A guided tour of Milestone 3, with pointers to the real code. M2 let the model use tools; M3 lets it answer from _your_ files. This pattern is called **RAG** (retrieval-augmented generation): find the relevant passages first, then have the model answer using them.

## 1. Embeddings and vector search

**Files:** `backend/app/documents/embeddings.py`, `backend/app/documents/retrieval.py`

A model cannot read a 50-page PDF on every question; the context window is small and slow on a laptop. Instead we search for the few passages that matter and show the model only those.

An **embedding** is a list of numbers (768 of them for `nomic-embed-text`) that represents the _meaning_ of a piece of text. Texts with similar meaning get vectors that point in similar directions. "What is the project codename?" lands near "The project codename is BLUE HERON" even though few words overlap. **Vector search** embeds the question, then finds the stored chunks whose vectors are nearest to it. We measure "nearest" with **cosine distance** (the angle between two vectors, ignoring their length), which is what text-embedding models are trained for.

`build_embeddings` uses LangChain's `init_embeddings`, so the provider is configuration (`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`), just like the chat model in `llm.py`.

## 2. The ingestion pipeline

**Files:** `backend/app/documents/router.py`, `service.py`, `storage.py`, `extraction.py`, `ingestion.py`

`POST /api/documents` does the cheap, safe work inline: reads the upload in 1 MB pieces and stops past the limit (413), checks the extension and that the bytes look like that format (415), stores the file at `{user_id}/{document_id}{ext}` under `UPLOAD_DIR`, inserts a `documents` row with status `processing`, and returns. The slow part runs as a FastAPI **background task** (`ingest_document`): extract text, chunk it, embed the chunks in batches of 32, insert the `document_chunks` rows, flip the status.

The status lifecycle is:

```
processing --> ready     (chunks stored; searchable)
          '--> failed    (row carries a human-readable `error`)
```

`ingest_document` never raises: extraction problems (a scanned PDF with no text, a corrupt file) record their own message, and anything else (typically Ollama being down) records a generic "embedding model may be unavailable" message. The frontend polls the list while any document is `processing`.

A background task lives in the server process, so a restart loses it. On startup `fail_interrupted_documents` (called from the lifespan in `main.py`) marks anything still `processing` as `failed` with "Processing was interrupted by a server restart. Delete and upload it again." A real job queue would be better at scale; for one process this is honest and simple.

Files are never served back; the only things that read them are ingestion and `reindex`.

## 3. Chunking choices

**File:** `backend/app/documents/extraction.py` (`chunk_pages`)

You cannot embed a whole document as one vector (the meaning blurs), and one sentence loses its context. So text is cut into **chunks**. We use LangChain's `RecursiveCharacterTextSplitter`, which prefers to split on paragraph breaks, then lines, then spaces, so chunks tend to end at natural boundaries.

- `CHUNK_SIZE=1000` characters (about 250 tokens): big enough to hold a complete idea, small enough that four of them fit easily in the model's context.
- `CHUNK_OVERLAP=150`: neighbouring chunks share their edges, so a fact straddling a boundary is still whole in at least one chunk.
- `RETRIEVAL_K=4`: how many chunks come back per search. No reranking; it is a simple baseline you can measure in M6.

These are defaults, not truths. M6 (evaluation) is where you would tune them with data. PDFs are extracted page by page with `pypdf`, DOCX paragraphs with `python-docx`, TXT/MD are read as UTF-8. Scanned (image-only) PDFs have no text to extract and fail with a clear message; OCR is out of scope.

## 4. pgvector: column, index, cosine

**Files:** `backend/app/documents/models.py`, `backend/alembic/versions/0002_documents.py`

Postgres stores the vectors using the **pgvector** extension (the Docker image is `pgvector/pgvector:pg17`). `DocumentChunk.embedding` is a `Vector(768)` column; the size comes from `EMBEDDING_DIMENSIONS` when the migration runs. Searching is `ORDER BY embedding <=> :query LIMIT k` (`<=>` is cosine distance), written in `retrieval.py` as `DocumentChunk.embedding.cosine_distance(...)`.

Comparing a query against every row is exact but slow once there are many chunks, so there is an **HNSW** index (`vector_cosine_ops`). HNSW is an approximate nearest-neighbour graph: it trades a tiny chance of missing the true best match for much faster search. The index operator class has to match the distance you query with, which is why both say "cosine".

Each chunk also stores `embedding_model` (see section 7) and a copy of `user_id` (see section 5).

## 5. How isolation is enforced

**Files:** `backend/app/documents/retrieval.py`, `service.py`, `backend/app/agent/tools/documents.py`, `backend/app/chat/streaming.py`

This is the project's first anti-goal: one user must never see another user's documents. Two layers:

1. **Every query filters by `user_id` on the server.** The REST endpoints go through `get_owned_document`, which returns `404` (not 403, so existence is not leaked) for anyone else's id. `search_chunks` filters `DocumentChunk.user_id == user_id` _and_ `Document.user_id == user_id`; the chunk carries a denormalised `user_id` so the vector search needs no join to be safe.
2. **The tool never takes the user as an argument.** If `search_documents(query, user_id)` existed, the model would be choosing whose documents to read, and a prompt-injected document or a confused model could pick someone else. Instead the tool declares a `config: RunnableConfig` parameter. `stream_agent_events` builds the run config as `{"configurable": {"thread_id": ..., "user_id": <authenticated user>}}`, `ToolNode` passes the config into the tool, and the tool reads `config["configurable"]["user_id"]`. A `user_id` the model puts in its tool call is not in the schema and is ignored. Without a user id the tool returns an error string instead of searching.

The principle: **authorization data comes from the server's authenticated session, never from anything the model or client can write.**

## 6. `content_and_artifact` and citations

**Files:** `backend/app/agent/tools/documents.py`, `backend/app/chat/streaming.py` (`citation_items`, `dedupe_citations`), `backend/app/chat/router.py`, `frontend/src/chat/Citations.tsx`

A tool normally returns one thing, and that is what the model sees. We need two: text for the model (numbered passages, so it can cite `[1]`) and structured data for the UI (document id, filename, chunk index, snippet) that the model should not have to reproduce. LangChain's `@tool(response_format="content_and_artifact")` lets the function return `(content, artifact)`. The content becomes the `ToolMessage` text; the artifact rides along on `ToolMessage.artifact` and is never sent to the model.

`streaming.py` reads the artifact from each `search_documents` result, and at the end of the turn, just before `done`, emits one `citations` event (`{"items": [...]}`), de-duplicated by `(document_id, chunk_index)` in first-seen order. Snippets are whitespace-collapsed and cut to 300 characters. The transcript endpoint rebuilds the same list from the saved thread, so citations survive a reload. In the UI `Citations.tsx` shows them as an expandable "Sources" list under the answer. Because the citation data comes from the database, not from the model's words, a model cannot invent a citation.

Empty results are explicit text too ("You have no ready documents...", "Your documents are still being processed...", "No matching passages...") so the model can tell the user something useful instead of guessing.

## 7. The reindex story

**Files:** `backend/app/documents/reindex.py`, `retrieval.py` (`count_mismatched_chunks`)

Vectors from different embedding models are not comparable; even the same dimensions mean different things. So changing `EMBEDDING_MODEL` silently would make search return garbage. The design:

- every chunk records the `embedding_model` that made it;
- `search_chunks` only searches chunks whose model matches the configured one;
- if some of the user's chunks do not match, the tool output appends a note saying so, so the model can pass it on instead of just returning "nothing found";
- `uv run python -m app.documents.reindex` fixes it: it deletes all chunks, marks every document `processing`, resizes the vector column and rebuilds the HNSW index if `EMBEDDING_DIMENSIONS` changed, then re-ingests every document from its stored file. It prints a summary, lists failures and exits non-zero if any document failed. If it is interrupted, run it again; the original files are the source of truth.

That is why uploaded files are kept at all, even though they are never served.

## 8. Live results (recorded on 2026-11-11)

Checked against a real backend (port 8010), real Postgres, Ollama `qwen3:4b-instruct-2507-q4_K_M` and `nomic-embed-text`, with curl:

- Upload `zephyr.md` (invented facts: "The Zephyr-9 project codename is BLUE HERON. Its budget is 42,000 credits..."): status went `processing` then `ready` with 1 chunk within seconds.
- Upload a blank one-page PDF: `failed`, with "No text could be extracted (scanned PDFs are not supported)."
- "What is the codename of the Zephyr-9 project according to my documents?": **the 4B model did not call `search_documents`** and answered "I don't have access to your documents". Same for "Search my documents for the Zephyr-9 project and tell me its codename."
- "Use your search_documents tool to look up the Zephyr-9 project codename in my uploaded files.": the model called the tool (`tool_start` with query "Zephyr-9 project codename"), `tool_end` returned the passage, one `citations` event named `zephyr.md` (chunk 0), and the answer was "The Zephyr-9 project codename is BLUE HERON [1]."
- Second user, same question: the model again did not call the tool in the natural phrasing. With the explicit phrasing it called the tool and got "You have no ready documents to search. Upload one from the Documents panel." No citation event, no leak. `GET /api/documents/<user 1's id>` as user 2 returned `404` and their list was `[]`.

The lesson is the same as in note 2: whether a small model picks a tool is a property of the model and the prompt, not of the plumbing. The retrieval, citation and isolation parts worked every time they ran. Making tool choice reliable (a stronger model, or a retrieval step that always runs) is something to measure in M6.

## Try it yourself

1. **Watch it in the UI.** Upload a short `.md` file in the Documents panel, wait for `ready`, then ask about it and name your documents. Expand the "Sources" rows under the answer.
2. **Watch it on the wire.** Following the curl recipe in note 2, upload with `curl -F file=@notes.md $B/api/documents -H "authorization: Bearer $TOKEN"`, poll `GET $B/api/documents/<id>` until `ready`, then stream a question and look for `tool_start`, `tool_end` and `citations`.
3. **Look inside Postgres.** `SELECT chunk_index, embedding_model, left(content, 60) FROM document_chunks;` and `\d document_chunks` to see the HNSW index.
4. **Change the chunking.** Set `CHUNK_SIZE=300`, upload a longer file, and compare what comes back.
5. **Try isolation.** Register a second user and ask the same question. Request the first user's document id and read the 404.
6. **Break and heal.** Change `EMBEDDING_MODEL` and ask a question (read the mismatch note), then run `uv run python -m app.documents.reindex`.

## Key files

- `backend/app/documents/`: `models.py`, `storage.py`, `extraction.py` (extract and chunk), `embeddings.py`, `ingestion.py`, `retrieval.py`, `router.py`/`service.py`/`schemas.py`, `reindex.py`
- `backend/alembic/versions/0002_documents.py`: tables, vector column, HNSW index
- `backend/app/agent/tools/documents.py`: `search_documents`
- `backend/app/chat/streaming.py` and `router.py`: `citations` event and transcript citations
- `frontend/src/documents/` (`api.ts`, `DocumentsPanel.tsx`) and `frontend/src/chat/Citations.tsx`
