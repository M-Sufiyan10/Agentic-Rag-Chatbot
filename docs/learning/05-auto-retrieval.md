# Learning note 5: when the model won't use its tools

A debugging story with a design change at the end. After M4 the agent had working `web_search`, `search_documents` and `get_weather` tools, yet in the browser it refused all three kinds of question:

| You asked                                                | It answered                                                    |
| -------------------------------------------------------- | -------------------------------------------------------------- |
| "who won 2026 fifa world cup"                            | "The 2026 FIFA World Cup has not yet taken place..."           |
| "tell me about the one digital security assement review" | "I don't have access to the contents of your uploaded file..." |
| "what is the current temperature in lahore"              | "I can't provide real-time data..."                            |

## 1. Look at what the model actually did

The conversation is stored in the LangGraph checkpoint, so it can be read back message by message (an `AIMessage` lists its `tool_calls`). The saved turns showed **no tool call at all** on those questions. The `[1]` at the end of the documents answer was invented; no search ever ran. So the tools, the database and the MCP client were not the problem: the model never asked for them.

Two side findings from the same read:

- The `mcp-utils` server wasn't running, so `get_weather` didn't exist that day (`/api/health` says `"utils": "unavailable"`). Start it with `cd mcp-utils && uv run python -m mcp_utils`.
- Once a refusal is in the history, the model repeats it on the next turn. A fresh chat is a fairer test.

## 2. Measure, one variable at a time

Each probe asked the real `qwen3:4b-instruct` model the same question several times in a fresh chat and counted tool calls in its first reply:

| Setup                                                         | `web_search` calls on "who won 2026 fifa world cup" |
| ------------------------------------------------------------- | --------------------------------------------------- |
| Full prompt, all tools                                        | 0 of 4                                              |
| ...plus "Today is 12 November 2026; search for recent events" | 0 of 6                                              |
| One-line prompt, only `web_search` bound                      | 0 of 4                                              |
| Same, but the user writes "search the web: who won..."        | **4 of 4**                                          |
| A separate yes/no call: "does this need a web search?"        | 2 of 6 fresh questions classified YES               |

And the documents question got 0 of 4 `search_documents` calls with the documents note in the prompt.

So the tool _works_ when the model is told to use it. What fails is the model's own judgement of _whether_ it needs one: when it believes it already knows ("the 2026 World Cup is in the future"), no prompt wording changed its mind. That was the fourth prompt-level fix to fail (M3's documents note, M4's `fetch` description override, the date, the router), which is the signal to stop rewording prompts and change the design.

## 3. The design change: retrieve before the model answers

**Files:** `backend/app/agent/prefetch.py`, `backend/app/agent/freshness.py`, `backend/app/agent/graph.py`

The graph gained one node that runs once per turn, before the model:

```
START -> prefetch -> agent <-> tools
```

`prefetch` decides with code, not with the model:

- **Documents:** if the user has ready documents, search them with the user's message and keep passages within `AUTO_DOCUMENT_MAX_DISTANCE` (cosine distance, default 0.48). Unrelated questions find nothing close enough and add nothing.
- **Web:** if `needs_fresh_information(message)` says so, run `web_search` with the message. The rule is deliberately simple and testable: a year after `KNOWLEDGE_CUTOFF_YEAR` decides by itself ("who won the 2018 world cup?" doesn't search); otherwise words like _latest, current, news, today, price, won, release_ do. Weather, time and URL questions are skipped because other tools answer them.

The neat part is _how_ the results enter the conversation. `prefetch` writes an ordinary tool exchange, an `AIMessage` with one `tool_calls` entry plus the matching `ToolMessage`, exactly as if the model had called the tool. Everything downstream already understood that shape, so nothing else had to change: the SSE `tool_start`/`tool_end`/`citations` events, the chips and citations in the UI and in reloaded transcripts, the rollback of failed turns, the max-rounds guard, and the model's view of earlier turns. The model keeps its tools and can still search again with a better query.

## 4. A better embedding signal: task prefixes

`nomic-embed-text` is trained to see `search_query: ` before questions and `search_document: ` before passages. We weren't adding them. Measured on the uploaded security report (lower is closer):

| Question                                               | without prefixes | with prefixes |
| ------------------------------------------------------ | ---------------- | ------------- |
| tell me about the one digital security assement review | 0.412            | 0.377         |
| what risks were found in the assessment?               | 0.495            | 0.387         |
| what does the report recommend?                        | 0.556            | 0.472         |
| who won 2026 fifa world cup                            | 0.614            | 0.513         |
| what is the weather in lahore                          | 0.673            | 0.571         |

`PrefixedEmbeddings` (in `app/documents/embeddings.py`) adds them, chosen automatically for nomic models. Because vectors made with and without prefixes aren't comparable, the prefixes are part of the tag each chunk stores (`Settings.embedding_tag`), so old chunks count as "a different model" until `python -m app.documents.reindex` re-embeds them.

Running that reindex for real exposed an M3 bug: run as `python -m`, the command never imported the `User` model, so saving chunks failed on the `documents.user_id` foreign key and every document was marked failed (with a misleading "embedding model unavailable" message). The tests hadn't caught it because pytest imports the whole app. The fix imports `app.models`, and a regression test checks the command in a fresh interpreter.

## 5. What the threshold can't do

No single distance separates "about the report" from "not about the report". Measured with prefixes:

- about the report: 0.28-0.47
- general questions: 0.48-0.57, **except** ones near the report's subject: "what is a penetration test?" 0.404, "how do I secure my wifi router?" 0.406, "what is the latest version of python?" 0.426, "write a python function to reverse a string" 0.445

Those get passages attached too. That's mostly harmless (the model is free to ignore them, and did: "the provided documents do not contain information about the latest version of Python"), but it costs prompt-reading time on a CPU. One real consequence was caught live: the first version skipped the web search whenever documents matched, so the Python question got only security passages. Now the web search runs whenever its rule fires, documents or not.

M6 (evaluation) is where the threshold and the rule get tuned properly, on a dataset instead of a handful of questions.

## 6. The web search itself

With retrieval fixed, the World Cup question still failed once, for a different reason: every search engine returned "No results found". Checking each engine directly showed DuckDuckGo answering with an HTTP 202 challenge page and Brave with HTTP 429 (too many requests), i.e. the keyless engines that `ddgs` scrapes were blocking automated traffic from this network. A few minutes later the same question got results, and the model answered from them with a citation. Two small prompt changes make a failed search honest instead of confidently wrong: the system prompt now states today's date and says never to claim something before today hasn't happened, and the search-failed message tells the model to say it couldn't check. A dependable search needs an API-backed provider (with a key); that is a separate decision.

## 7. Live results after the change

Real model, real tools, the same streaming code the browser uses:

| Question                                               | Tools used                       | Result                                                                           |
| ------------------------------------------------------ | -------------------------------- | -------------------------------------------------------------------------------- |
| who won 2026 fifa world cup                            | `web_search`                     | answered from search results with a citation (when the engines weren't blocking) |
| tell me about the one digital security assement review | `search_documents`               | a structured summary of the report, 4 citations                                  |
| what is the current temperature in lahore              | `get_weather`                    | current temperature, humidity and wind                                           |
| what is harness?                                       | none                             | answered directly                                                                |
| what is the latest version of python?                  | `search_documents`, `web_search` | the engines were blocking again; it said it couldn't check instead of guessing   |

**Speed:** the documents answer took about 7 minutes on this machine: 0.2 s of retrieval, about 90 s for the model to read a ~4,000-token prompt, and about 350 s to write a 3,700-character answer. The model itself runs at about 18 tokens/s reading and 6 tokens/s writing on this CPU (no GPU, and the machine was busy), so long grounded answers are slow; the retrieval adds almost nothing.
