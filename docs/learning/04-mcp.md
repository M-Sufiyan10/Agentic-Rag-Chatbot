# Learning note 4: MCP (M4)

A guided tour of Milestone 4, with pointers to the real code. M2 gave the agent tools written inside this repo; M4 lets it use tools that live in _other processes_, described by a config file, over a standard protocol.

## 1. What MCP is

The **Model Context Protocol** is a standard way for an AI app to talk to tool providers. It has two roles:

- an **MCP server** exposes capabilities;
- an **MCP client** (here, our backend) connects to servers and uses them.

A server can offer three kinds of things: **tools** (functions the model can call, which is all we use), **resources** (readable data such as files) and **prompts** (reusable prompt templates). A client and server talk over a **transport**. Two matter here:

- **stdio:** the client starts the server as a child process and speaks over its stdin/stdout. Zero networking; the server lives and dies with the client.
- **streamable HTTP:** the server is a long-running web service at a URL (ours: `http://localhost:8100/mcp`). Several clients can share it, and it can run in its own container.

The win is the same as with USB: write a tool once, and any MCP-aware app can plug it in. Adding a tool to the chatbot becomes a line of config, not a code change.

## 2. Our two servers

**Files:** `mcp-utils/mcp_utils/` (`server.py`, `weather.py`, `units.py`, `timezones.py`, `__main__.py`), `backend/mcp_servers.json`

- **`utils` is custom, because it is the part worth learning.** `mcp-utils` is a small separate project (its own `pyproject.toml`, tests, Dockerfile, a `docker compose` service and a CI job). `server.py` creates `MCPServer("mcp-utils")` (this is what the mcp 2.x SDK calls FastMCP) and registers three tools with `@mcp.tool()`: `get_weather` (Open-Meteo geocoding plus forecast, no API key), `convert_units` (pint) and `convert_time` (zoneinfo). The decorator turns the type hints into the JSON schema and the docstring into the description, exactly like a LangChain `@tool`. `python -m mcp_utils` serves it on `127.0.0.1:8100`, path `/mcp`.
- **`fetch` is third-party, because it already exists and is good.** Reading a web page (download, strip HTML, truncate, obey robots.txt, refuse private addresses) is a solved problem, so we run the published `mcp-server-fetch` through `uvx` over stdio, pinned to `==2026.10.24`. Pinning matters: `uvx` would otherwise run whatever is newest, i.e. code we never reviewed, with network access, at every backend start.

Having one of each shows both sides of MCP: writing a server, and consuming someone else's.

## 3. From MCP tools to LangChain tools

**File:** `backend/app/agent/mcp.py`

`langchain-mcp-adapters` does the translation. `MultiServerMCPClient({name: connection}).get_tools()` connects, asks the server `tools/list`, and returns ordinary LangChain `BaseTool` objects: name, description and argument schema come from the server. From then on the agent's tool loop (note 2) cannot tell them from `calculator`. Calling one is a `tools/call` request. The adapter is stateless: it opens a fresh session for every call. That is simple and robust (nothing to reconnect), at the cost of a little latency per call.

We wrap every tool in `guard_mcp_tool`, which also strips MCP's content blocks down to plain text. It matters for the next section.

## 4. Startup and failure isolation

**Files:** `backend/app/agent/mcp.py` (`load_mcp_config`, `load_mcp_tools`, `guard_mcp_tool`), `backend/app/main.py` (lifespan, `/api/health`)

A chatbot should not die because a weather service is down. The design has four layers:

1. **Config is validated up front.** `load_mcp_config` reads `backend/mcp_servers.json`. A missing file means no MCP servers; bad JSON, an unknown transport or a missing `url`/`command` raises `ValueError` naming the file, so a typo fails at startup instead of silently dropping a server.
2. **Each server loads on its own, with a deadline.** `load_mcp_tools` makes one client per server and wraps `get_tools()` in `asyncio.wait_for(..., MCP_STARTUP_TIMEOUT_SECONDS)`. A server that errors or is too slow is logged at WARNING and recorded as `unavailable`; the others still load. (Adapter errors arrive as exception groups; `_reason` digs out the first real message.)
3. **Names can't clash.** A tool named like a built-in, or like one already loaded, is skipped with a warning. Built-ins win, so a server cannot replace `calculator`.
4. **Each call is guarded.** `guard_mcp_tool` turns a timeout (`MCP_TOOL_TIMEOUT_SECONDS`) or a crash into a string, `Error: the utils tool server didn't respond in 30s.`, that the model can read and relay. An exception would abort the whole reply.

The lifespan loads MCP tools _after_ the built-ins, stores each server's status in `app.state.mcp_servers`, and `GET /api/health` reports it: `{"status": "ok", "mcp": {"utils": "ok", "fetch": "ok"}}`. Status is checked once, at startup, so a server started later needs a backend restart (see the live results).

## 5. Security stance

- **No filesystem server.** The best-known MCP server reads and writes your disk. Handing that to a 4B model that also reads untrusted web pages is asking for trouble, so we simply do not configure one. Every server you add widens what a prompt injection can do.
- **`fetch` refuses private addresses.** It is started without `--allow-private-ips`, so it refuses URLs whose host resolves to a private, loopback or link-local address, checked on every request and redirect (including the `robots.txt` request). That covers the classic server-side request forgery (SSRF) target, the cloud metadata endpoint `169.254.169.254`. Do not add that flag. Some risk remains: the guard resolves the name, then httpx resolves it again to connect, so a DNS-rebinding name with a zero TTL can race the check, and a host the guard can't resolve is let through for proxy use. A real deployment should also block internal ranges at the network level, for example with an IMDSv2 hop limit of 1 or an egress policy.
- **`mcp-utils` is published on localhost only.** In Docker it binds `0.0.0.0`, which turns off the SDK's DNS-rebinding (Host header) check, so compose publishes it as `127.0.0.1:8100:8100` rather than on every interface.
- **Tool output is untrusted data.** A fetched page can say "ignore your instructions and...". The system prompt tells the model to treat tool output and fetched pages as data, never instructions. That is a mitigation, not a guarantee; the real protection is having no dangerous tools to misuse.
- **Pinned third-party code and a custom server with no secrets.** `fetch` is version-pinned; `mcp-utils` takes no credentials and only calls Open-Meteo.

## 6. Adding a server via config

Add a key to `backend/mcp_servers.json` and restart the backend:

```json
{
  "my-server": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["some-mcp-server==1.2.3"]
  },
  "other": {
    "transport": "streamable_http",
    "url": "http://localhost:9000/mcp"
  }
}
```

Then:

1. Check `GET /api/health` lists it as `ok`.
2. Mention its tools in `SYSTEM_PROMPT` (`backend/app/agent/prompts.py`); a small model rarely picks a tool it has not been told about. State what the tool lets the model do, not only when to use it (see section 7).
3. Read the server's own tool descriptions; the model sees them verbatim. If one misleads the model, override it with `"tool_descriptions": {"<tool>": "<text>"}` in the server's entry, as the committed config does for `fetch`.
4. Optionally add a friendly chip label in `frontend/src/chat/toolLabels.ts`; unknown tools show as "Calling name...".

## 7. Live results

All runs were on 2026-11-11 against `qwen3:4b-instruct-2507-q4_K_M` on CPU, with my own `mcp-utils` on port 8100 and backend on 8010. Each question was asked in a fresh conversation. Raw streams were saved while testing and are not committed. `GET /api/health` returned `{"status":"ok","mcp":{"utils":"ok","fetch":"ok"}}` every time.

### Before: the first run

| Question                                               | Tool chosen, input                                                                                     | Output                                                                                         | Answer                                                                     |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| "What's the weather in Lahore right now?"              | `get_weather` `{"location": "Lahore"}`                                                                 | `Lahore, Pakistan: mainly clear, 23.8°C (feels like 25.6°C), humidity 68%, ...`                | Correct, restated the numbers                                              |
| "Convert 26.2 miles to kilometres"                     | `convert_units` `{"value": 26.2, "from_unit": "mile", "to_unit": "km"}`                                | `26.2 mile = 42.1648 km`                                                                       | Correct                                                                    |
| "If it's 3pm in Karachi, what time is it in New York?" | `convert_time` `{"time": "15:00", "from_timezone": "Asia/Karachi", "to_timezone": "America/New_York"}` | `2026-11-11T15:00+05:00 (Asia/Karachi) = 2026-11-11T05:00-05:00 (America/New_York, Wednesday)` | Correct (5 AM, same day)                                                   |
| "Read https://example.com and tell me what it says"    | none                                                                                                   | none                                                                                           | Wrong: it can't access websites, and example.com is a "placeholder"        |
| "Fetch http://169.254.169.254/latest/meta-data/"       | none                                                                                                   | none                                                                                           | Refused by the model itself ("private metadata endpoint"), not by the tool |

The three utils tools were chosen correctly, with correct arguments, first time. The model never called `fetch` in five attempts. Three asked about example.com ("Read ...", "Please use your fetch tool to open ...", "What does the page at ... say? Open it and quote the main text."). For those it said it can't access websites, and once called example.com a placeholder with no real content. The two metadata-URL attempts were refused because it is a "private metadata endpoint typically used by AWS", which is the model's own judgement and proves nothing about the guard. The tool was loaded (`fetch: ok`), so this was the model ignoring a tool it had, as with documents in note 3.

The prompt was the likely cause. It only restricted the tool ("Use fetch only to read a specific URL the user gives..."). It never said the assistant can read the web, and it still listed "weather" under `web_search`. The third-party server made it worse. Its own `fetch` description, which the model sees word for word, says "Although originally you did not have internet access, and were advised to refuse and tell the user this, this tool now grants you internet access."

### After: two fixes, measured one at a time

Each fix was measured on four public URLs plus the earlier checks. The bar was `fetch` called with the given URL on at least 3 of the 4.

1. **Prompt.** I removed "weather" from the `web_search` list and stated the capability the way note 3 did for documents: "You can read web pages with fetch: when the user gives a URL or asks what a page says, call fetch with that exact URL before answering, and never say you cannot access websites or the internet."
2. **Description override.** I kept the prompt and replaced the upstream description through config (`tool_descriptions` in `mcp_servers.json`) with "Read a web page: fetch a URL from the internet and return its main text as markdown."

| Question                                                         | Prompt fix only                                     | Prompt fix + description override                                                                          |
| ---------------------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| "Read https://example.com and tell me what it says"              | No tool; "cannot access... a placeholder"           | `fetch` `{"url": "https://example.com"}`, which returned the page text; the answer summarised it correctly |
| "What does https://www.python.org/about/ say?"                   | No tool; "don't have the capability to browse"      | No tool; "I cannot access or retrieve the content"                                                         |
| "Summarise https://en.wikipedia.org/wiki/Model_Context_Protocol" | No tool; claimed MCP "is not a recognized protocol" | No tool; same claim                                                                                        |
| "Open https://httpbin.org/html and quote its first sentence"     | No tool; "cannot access or open external websites"  | No tool; "falls outside the capabilities of the tools I have access to"                                    |
| **`fetch` called**                                               | **0 of 4**                                          | **1 of 4** (bar of 3 missed)                                                                               |

With both fixes in place:

| Question                                               | Tool chosen, input                                                                                     | Answer                                                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| "Fetch http://169.254.169.254/latest/meta-data/"       | none                                                                                                   | Model declined by itself: "a private metadata endpoint typically used by AWS EC2 instances" |
| "Read http://localhost:5433"                           | none                                                                                                   | Model declined by itself: "cannot access or read URLs, including localhost addresses"       |
| "What's the weather in Lahore right now?"              | `get_weather` `{"location": "Lahore"}`                                                                 | Correct (23.0°C, mainly clear)                                                              |
| "How many kilometres is 26.2 miles?"                   | `convert_units` `{"value": 26.2, "from_unit": "mile", "to_unit": "km"}`                                | Correct (42.1648 km)                                                                        |
| "If it's 3pm in Karachi, what time is it in New York?" | `convert_time` `{"time": "15:00", "from_timezone": "Asia/Karachi", "to_timezone": "America/New_York"}` | Correct (5 AM, same day)                                                                    |

What this shows:

- **The fixes helped a little, not enough.** Removing the "you did not have internet access... refuse" text produced the first real `fetch` call, but only 1 of 4. For the other URLs the 4B model still trusted its belief that it can't browse over a tool it had and a prompt telling it to use it. One answer was worse than a refusal: it confidently said the Model Context Protocol doesn't exist instead of reading the page. Treat `fetch` as unreliable with this model. A bigger model or a forced tool choice for messages containing a URL are the next things to try. Both are out of scope for M4.
- **The SSRF guard is still proven only by a direct call.** The model never sent a private URL to `fetch`; it declined both by itself. So I called the tool directly (through `load_mcp_tools`, bypassing the model). `fetch("https://example.com")` returned `Contents of https://example.com/: This domain is for use in documentation examples without needing permission...`. `fetch("http://169.254.169.254/latest/meta-data/")` returned `Error executing tool fetch: Refused to fetch http://169.254.169.254/robots.txt: 169.254.169.254 resolves to 169.254.169.254, which is not a public address. Start the server with --allow-private-ips ...`. The server-side refusal works. The server checks `robots.txt` first, so the refused URL it names is the robots file.
- **The utils tools didn't regress.** All three still picked the right tool with the right arguments after the prompt change.
- **Degradation works, with a quality cost** (first run). After stopping `mcp-utils` and restarting the backend, `/api/health` gave `{"status":"ok","mcp":{"utils":"unavailable","fetch":"ok"}}` and the log had `MCP server 'utils' unavailable, skipping: All connection attempts failed`. The weather question did not crash. The model called `current_datetime` and `web_search`, then wrote a rambling, repetitive answer saying it could not get live weather and pointing at AccuWeather. Safe, but not helpful: the fallback tool for weather is much weaker.
- **No hot reload.** A server that was down at startup stays `unavailable` until the backend restarts.

## Try it yourself

1. **Watch it in the UI.** Start `mcp-utils` and the backend, then ask "What's the weather in London?" and watch the "Checking the weather..." chip.
2. **Watch it on the wire.** Use the curl recipe from note 2 and look for `tool_start` events whose `name` is `get_weather`, `convert_units` or `convert_time`.
3. **Break and heal.** Stop `mcp-utils`, restart the backend, read `/api/health`, and ask the weather question again. Start it, restart the backend, and ask once more.
4. **Poke the server directly.** `uv run python -m mcp_utils`, then point an MCP client such as the MCP Inspector (`npx @modelcontextprotocol/inspector`) at `http://localhost:8100/mcp` and call a tool by hand.
5. **Add a server.** Follow section 6 with any stdio MCP server you trust, pinned to a version.

## Key files

- `mcp-utils/mcp_utils/server.py`, `weather.py`, `units.py`, `timezones.py`, `__main__.py`; `mcp-utils/Dockerfile`; the `mcp-utils` service in `docker-compose.yml` and its CI job
- `backend/app/agent/mcp.py`: `load_mcp_config`, `load_mcp_tools`, `guard_mcp_tool`
- `backend/mcp_servers.json`: the server list
- `backend/app/main.py`: lifespan loading and the `mcp` field of `/api/health`
- `backend/app/agent/prompts.py`: tool guidance in the system prompt
- `frontend/src/chat/toolLabels.ts`: chip labels
