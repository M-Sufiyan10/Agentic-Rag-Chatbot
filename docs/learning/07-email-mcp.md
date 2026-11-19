# Learning note 7: email with a human in the loop

Notes 4 to 6 gave the agent tools and a way to watch them. This one is about a tool that can do something you cannot take back: send an email. The feature lets the agent read your inbox and _draft_ replies, but the email leaves only when you click Send. This note covers the roles, where the password lives, and the three rules that keep it safe.

## 1. Host, client and server

MCP has a host, a client and a server (note 4). For email:

- **Host:** the chat backend, which owns the user, the database and the agent.
- **Client:** `backend/app/email/client.py` (`EmailServerClient`), a small streamable-HTTP MCP client with a bearer token.
- **Server:** `mcp-email/` (port 8101, path `/mcp`), which speaks IMAP and SMTP. It is _stateless_: it keeps no credentials. Every call carries the account to use.

The backend, not the server, knows who the user is. Their mailbox settings live in Postgres, and the agent tools (`backend/app/email/agent_tools.py`) take the user id only from `config["configurable"]["user_id"]`, the same pattern as the document tools.

## 2. Credentials are injected below the traced boundary

The password is the one thing that must never reach the model, a log or a Langfuse trace. The tool the model sees is `list_emails(limit, since_days, ...)`: no account argument at all. When the tool runs, it loads the user's settings, decrypts the password (Fernet, key from `EMAIL_ENCRYPTION_KEY`) and passes the account to the MCP server inside a second, inner call.

That inner call is made with `EmailServerClient.call`, **without** the LangChain run config or callbacks. Langfuse records the inputs of the LangChain tool (which have no account) and nothing about the inner call. So the secret is added _below_ the layer that gets traced. Compare note 6: tracing is a callback on the run, and we simply do not hand the callback to the inner call.

## 3. Draft, then click

`draft_email` does not send. It checks the recipient, saves a row in `email_drafts` (status `pending`) and tells the model the draft is waiting for the user. The UI shows it as a card under the chat with Send and Cancel. Send is `POST /api/email/drafts/{id}/send`:

- `pending` becomes `sending` in one atomic update, so a double click or two concurrent requests send once; the second gets 409.
- Then `sent` or `failed`. `failed` is final; ask the agent for a new draft.
- There is an hourly limit per user (`EMAIL_MAX_SENDS_PER_HOUR`, default 20).

A model that is tricked by text inside an email can at worst create a draft. A person still has to read it and press the button.

## 4. The listed-sender rule

The agent may only email people who have already emailed the user. It stops the most damaging abuse (a prompt-injected email telling the agent to mail a stranger) without any allow-list to maintain. The check is in the MCP server (`is_listed_sender`, and again inside `send_email`), so it holds at draft time and at send time even if the backend is bypassed. Matching is on the parsed address, case-insensitively: `"Sara Khan" <SARA@Example.com>` matches `sara@example.com`, but `ara@example.com` does not, even though IMAP's search is a substring match.

Related limits: one recipient, no CC/BCC, no attachments, only INBOX, and reading uses `BODY.PEEK` so it never marks mail as read. Email text comes back wrapped in `<email>` tags behind a note that it is untrusted data.

## 5. Tradeoffs worth knowing

- **Email text is not secret.** What the tools return lives in the chat history and in Langfuse, like any tool output. Only the password is protected specially.
- **Drafts appear after the turn.** There is no draft SSE event; the chat refetches drafts when `done` arrives (plan ruling P1).
- **Off by default.** Without `EMAIL_ENCRYPTION_KEY` and `MCP_EMAIL_TOKEN` no client or tool is created, and `/api/health` says `"email": "off"`.
- **Plain HTTP between backend and server.** Fine on localhost; a cloud deployment needs TLS or a private network.

## 6. Try it with GreenMail

GreenMail is a throwaway mail server in Docker. Follow the [README's Email section](../../README.md#email): `docker compose up -d greenmail mcp-email`, `uv run alembic upgrade head`, connect `me@example.com` with the Local test server preset (host `greenmail`, IMAP 3143, SMTP 3025, no TLS; allowed only because compose sets `EMAIL_ALLOW_INSECURE_HOSTS=true` on `mcp-email`), and send yourself a message with the short `smtplib` snippet there. Then ask the agent to summarize your mail, ask what Sara wanted, and ask it to reply. Click Send, then look at Sara's inbox: the reply carries `In-Reply-To`, because the draft stores the original email's uid and the server builds the threading headers when it sends (plan ruling P2).

## 7. What to remember

- Keep secrets out of the tool schema; add them in a layer the trace never sees.
- For irreversible actions, let the model propose and a human commit, and make the commit idempotent.
- Put the safety rule (listed senders) in the component that performs the action, not only in the caller.
- Treat everything inside an email as data, never as instructions.
