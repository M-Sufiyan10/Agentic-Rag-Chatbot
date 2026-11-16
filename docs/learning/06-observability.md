# Learning note 6: seeing what the agent did

Note 5 ended with a question we could not answer from the browser: _why_ was this answer slow, and _why_ did the model not call a tool? M5 adds the missing view: every chat turn is recorded in Langfuse, and a thumbs up/down on an answer is attached to the turn it judges.

## 1. Traces, observations and scores

Langfuse has three ideas, and they map onto what the app already does:

- A **trace** is one end-to-end request. Here: one chat turn, from the user's message to the finished answer.
- An **observation** is a step inside a trace, with its own input, output, start and end time. A _generation_ is the kind of observation that is an LLM call; it also records the model and token usage. A _span_ is any other step. Here the LangGraph nodes appear as observations: `prefetch`, `agent` (containing the generations) and `tools`.
- A **score** is a named value attached to a trace (or an observation). Here: `user_feedback`, a boolean, 1 for thumbs up and 0 for thumbs down.

Each trace also carries a **user id** and a **session id**. We set them to the user's id and the conversation's id, so Langfuse's Users and Sessions views group turns by person and by conversation. The trace is named `chat-turn` and tagged `chat`.

## 2. How feedback finds its trace

The score has to name the trace it belongs to, and the browser only knows messages. The link is the turn id. `stream_agent_events` already gave each user message a uuid; the turn's trace id is that uuid written as 32 hex characters. The transcript returns that id with each saved assistant message, the UI sends it back with the vote (`PUT /api/conversations/{id}/messages/{turn_id}/feedback`), and the server derives the trace id again. Nothing extra is stored to connect the two. A turn that failed and was rolled back never gets a transcript id, so it has no thumbs.

## 3. Why feedback is stored locally too

Langfuse only overwrites a score when its **id, name and timestamp** all match. If a user changes their mind from up to down and we sent the new vote with a fresh timestamp, Langfuse would keep both and the trace would show two contradictory scores. So the rule is:

- The score id is fixed per turn: `feedback-{turn_id}`.
- The timestamp is the time of the **first** vote and never changes.

Remembering the first vote's time needs somewhere to keep it, and a Langfuse that is down must not lose votes, so the vote lives in our own Postgres table `message_feedback` (migration 0003) and is the source of truth. Langfuse is a copy: sending it is best effort, and clearing a vote deletes the score. This is also what makes the app work with Langfuse off.

## 4. Optional by design

Tracing is on only when both keys are set. When they are not, no client and no callback is created. When they are set but the server is down, startup waits at most `LANGFUSE_TIMEOUT_SECONDS` (5), `/api/health` says `"langfuse": "unavailable"`, and every call that would reach Langfuse logs a warning instead of raising. The tests never touch the network: they set both keys to `None` and read spans through an in-memory exporter.

## 5. Reading a trace: the two problems from note 5

**"The answer took 7 minutes."** Open the trace and look at the timeline. Note 5 measured about 0.2 s of retrieval, 90 s for the model to read a 4,000-token prompt and 350 s to write 3,700 characters. In the trace those are the `prefetch` span (tiny) and the `agent` generation (huge), and the generation's token usage shows the prompt and output sizes behind its time. If `prefetch` is large, retrieval is the cost; if the prompt token count is large, the passages or history are; if the output count is large, the model just wrote a lot.

**"The model never called a tool."** Open the `agent` generation: its input shows exactly what the model was given, including any passages `prefetch` added, and its output shows whether it asked for a tool. Then look for a `tools` observation. No `tools` observation and no tool call in the output means the model decided not to; a `prefetch` observation with a `web_search` or `search_documents` result means the server did the retrieval for it. Filtering traces by the `chat` tag and by a user or session makes it easy to compare a good turn with a bad one, and the thumbs-down scores point at the turns worth opening first. M6 will use this to tune the retrieval threshold on data.

## 6. The Langfuse stack

Langfuse v4 needs more than one container: web and worker, plus Postgres, ClickHouse (the analytics store), Redis (the queue) and MinIO (S3-compatible blob storage). They are an optional Compose profile (`--profile observability`), so `docker compose up -d postgres` is unchanged. Only the web UI is published, on `127.0.0.1:3100` (3000 is often taken). Headless initialization creates an organization, a project and its API keys on first start, so the app can use fixed local-development keys without anyone clicking through setup. Those, and every other password in the file, are development defaults.

## 7. What to remember

- Trace per turn, trace id = turn id: the id is the only link between a thumb and a trace.
- A score is overwritten only if id, name and timestamp match; keep the first timestamp and store votes yourself.
- Observability must never be on the request's critical path: optional, bounded, and never raising.

## 8. Live check

Run on 16 November 2026 against the real stack: `docker compose --profile observability up -d` (Langfuse **4.80.0**; every data store healthy, the headless-init keys reached the `chatbot` project), a backend started with the three `LANGFUSE_*` variables, the default 4B model, and a throwaway user with one uploaded document.

| Check                                           | Result                                                                                                                                                                                                                                                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/health`                                   | `"langfuse": "ok"`                                                                                                                                                                                                                                                                                      |
| "What is the codename of the Zephyr-9 project?" | answered "BLUE HERON" from the document (87 s on CPU)                                                                                                                                                                                                                                                   |
| Its trace                                       | root `chat-turn` → `prefetch` → `agent` → `ChatOllama` **GENERATION**; every observation carries the user id and the conversation id as the session; the generation shows **1,589 input / 17 output tokens**, its latency, and the exact prompt (system prompt plus the retrieved "BLUE HERON" passage) |
| "Who won the 2026 FIFA World Cup?"              | trace shows `web_search` as a **TOOL** observation inside `prefetch` (2.4 s); the search engines were blocking that day, and the model said the search found nothing                                                                                                                                    |
| Thumbs up, then down, on the first answer       | exactly **one** `user_feedback` score, id `feedback-<turn id>`, value `false`, `updatedAt` later than `createdAt`: the change overwrote it                                                                                                                                                              |
| Langfuse web stopped, then chat + vote + clear  | answer streamed normally; feedback `200`, clear `204`, both instant; the backend logged the failed span export and score deletion as warnings                                                                                                                                                           |

Two things to know when you read traces:

- **Langfuse v4 changed the API.** The old `GET /api/public/traces/{id}` answers "not available ... in v4 events_only mode". Read observations with `GET /api/public/v2/observations?traceId=<id>&fields=core,basic,usage,model,io` and scores with `GET /api/public/v3/scores?traceId=<id>` (basic auth with the project keys), or just use the UI.
- **The model name is not recorded** on the generation (`providedModelName` is empty), so Langfuse shows no model or cost column for Ollama calls. Token counts and latency are there.
