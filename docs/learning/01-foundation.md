# Learning note 1: the M1 foundation

A guided tour of the ideas in Milestone 1, with pointers to the real code. Read the file, then come back for the next section.

## 1. Auth: access token, refresh token, httpOnly cookie

**Files:** `backend/app/auth/security.py`, `backend/app/auth/router.py`, `backend/app/auth/deps.py`, `frontend/src/api/client.ts`

A **JWT** is a signed JSON blob (`create_token` in `security.py`). The server does not store it; it just verifies the signature and expiry (`decode_token`). We issue two kinds, distinguished by a `type` claim so one cannot be used as the other:

- **Access token**: short-lived (15 minutes by default). The browser sends it as `Authorization: Bearer ...` on every API call; `get_current_user` in `deps.py` validates it. Because it lives only in a JavaScript variable (`accessToken` in `client.ts`), it is gone on page reload and never touches `localStorage`.
- **Refresh token**: long-lived (7 days). It is set as a cookie in `_token_response` (`router.py`) with `httponly=True`, `samesite="lax"` and `path="/api/auth"`.

Why the cookie? An **httpOnly** cookie cannot be read by JavaScript, so an XSS bug cannot steal the long-lived credential. Limiting its `path` means the browser sends it only to the auth endpoints, and `SameSite=Lax` blocks it on cross-site POSTs (CSRF). The short-lived access token is what JavaScript holds, so a leak is only useful for a few minutes.

The loop: on page load the frontend calls `POST /api/auth/refresh` (`refreshSession` in `client.ts`). The browser attaches the cookie, the server replies with a fresh access token (and rotates the cookie). When any API call later gets a `401`, `apiFetch` refreshes once and retries. Concurrent callers share one refresh request through `refreshInFlight`.

Passwords are hashed with argon2 (`pwdlib`), never stored in plain text.

## 2. LangGraph: state, nodes, edges, checkpointer

**Files:** `backend/app/agent/graph.py` (read its docstring first), `backend/app/agent/checkpointer.py`

LangGraph models an agent as a small state machine:

- **State** is the data flowing through the graph. We use `MessagesState`: a dict with a `messages` list. Its _reducer_ appends what a node returns instead of overwriting, so history accumulates.
- **Nodes** are functions `state -> partial update`. Ours is `agent` inside `build_graph`: it calls the model and returns `{"messages": [response]}`.
- **Edges** are control flow. `START -> agent -> END` runs the node once. In M2 a conditional edge will route `agent -> tools -> agent` until the model stops asking for tools; that loop is what makes it an _agent_.
- **Checkpointer** saves the state after each step under a `thread_id`. We use `AsyncPostgresSaver` (`open_checkpointer`), and the thread id is the conversation's UUID. Call the graph again with the same thread id and the old messages are loaded first, so the caller sends only the new message.

Consequence: there is **no messages table**. The checkpointer _is_ the chat history, and `get_messages` in `chat/router.py` reads the transcript back with `graph.aget_state(...)`. LangGraph owns its own tables; Alembic only manages ours.

The graph is built once in the FastAPI `lifespan` (`main.py`) and shared by all requests. That is safe because per-conversation state lives in the checkpointer, not in the graph object.

## 3. A message's journey: browser to tokens

**Files:** `frontend/src/chat/useChatStream.ts`, `frontend/src/api/sse.ts`, `backend/app/chat/router.py`, `backend/app/chat/streaming.py`

1. The user hits send. `useChatStream.send` shows the message immediately and `POST`s to `/api/conversations/{id}/messages`.
2. `send_message` in `chat/router.py` checks that the conversation belongs to the user (otherwise `404`) and that no reply is already streaming for it (`409`). It returns an `EventSourceResponse`.
3. `stream_agent_events` in `streaming.py` calls `graph.astream(..., stream_mode="messages")`. LangGraph yields `(chunk, metadata)` pairs while the model is still generating. Each text chunk becomes `event: token` / `data: {"text": "..."}`; at the end one `done` event carries the full reply. Failures become a single `error` event.
4. SSE is just a long-lived HTTP response with a line-based format. The browser's `EventSource` only supports GET, so `sse.ts` parses the `fetch` response body by hand, taking care that network chunks split events arbitrarily.
5. `useChatStream` appends each `token` to `streamingText`. On `done` it refetches the saved transcript from the server and clears the temporary state.

The event names and payloads are a contract (see the roadmap): later milestones add events (`tool_start`, `citations`) but never change existing ones, and the frontend ignores events it does not know.

## 4. Why `init_chat_model` makes providers swappable

**File:** `backend/app/agent/llm.py`

Every LangChain chat model (Ollama, OpenAI, Anthropic, ...) implements the same `BaseChatModel` interface: `ainvoke`, `astream`, tool binding. `init_chat_model(model=..., model_provider=...)` is a factory that picks the right class from a string. `build_chat_model` feeds it `LLM_PROVIDER` and `LLM_MODEL` from `Settings`, so the rest of the code (`graph.py`, `streaming.py`) only ever sees a `BaseChatModel` and never imports a vendor SDK. The one catch: the vendor's integration package (for example `langchain-openai`) must be installed.

This is also what makes the tests cheap: `create_app` accepts a `chat_model` argument, and tests pass a `GenericFakeChatModel` so no real LLM is ever called.

## Try it yourself

1. **Change the persona.** Edit `SYSTEM_PROMPT` in `agent/prompts.py` (say, "answer like a pirate"). Existing conversations change behaviour too, because the prompt is prepended in the node and never stored in state. Why is that a good design?
2. **Add a second node.** In `build_graph`, add a `summarize` node that runs after `agent` and appends a one-line summary message, then wire `agent -> summarize -> END`. Watch what `get_messages` returns, and what the stream sends. Hint: `stream_mode="messages"` will stream tokens from _both_ nodes; `metadata["langgraph_node"]` tells you which one produced a chunk.
3. **Inspect a checkpoint.** After chatting, open `psql` on the dev database and look at the `checkpoints` and `checkpoint_blobs` tables. Find your conversation's `thread_id`.
4. **Watch the wire.** Open the browser dev tools Network tab, send a message, and look at the `messages` request's EventStream view.
5. **Break the token.** Set `ACCESS_TOKEN_TTL_MINUTES=1`, keep chatting for two minutes, and watch `/api/auth/refresh` fire in the Network tab.
6. **Swap the model.** Pull a second Ollama model and change only `LLM_MODEL`. Restart and compare answers.
