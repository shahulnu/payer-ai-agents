# Payer AI agents

AI agents for health plan operations, built with **LangGraph**, **LangChain** and the **Model Context Protocol (MCP)**
on Claude. Part of [Shahul Ummarali's AI portfolio](https://github.com/shahulnu/ai-portfolio): the site shows these
agents in its Healthcare payer section.

## Fax Intake Agent

Turns a scanned prior authorization fax into a case in the plan's systems. Claude reads the PDF and looks things up
through MCP tools. Code checks every field and chooses the route. Nothing is written until a person approves.

```
read_fax ─► look_up ─► decide ─► approve ─► act
 Claude    Claude +     code     a person   code +
 (PDF)     MCP reads             (interrupt) MCP writes
```

| Step | Who | What it does |
|---|---|---|
| `read_fax` | Claude | Reads the image-only PDF into fixed fields. Each field says what is written, whether it is clear, unclear or missing, and on which page. Unclear values are never guessed. |
| check fields | Code | NPI check digit (Luhn with the 80840 prefix), ID and code formats, dates, signature. A field that isn't clear is treated as unknown downstream. |
| `look_up` | Claude + MCP | Tool loop over four read-only MCP tools. Code checks each call against the fax, ignores lookups of values that aren't readable on it, and makes any lookup the agent skipped. |
| `decide` | Code | Route from the checks and tool results (never from the agent's summary): create a case, eligibility queue, or manual intake. Builds the writes. |
| `approve` | A person | LangGraph `interrupt`; a checkpointer keeps the paused run, so approval can come in a later request. |
| `act` | Code + MCP | Runs the writes only after approval. New cases always start as pending review, with the CMS-0057-F deadline (72 hours expedited, 7 days standard). |

**MCP server** (`app/payer_mcp/server.py`, FastMCP over stdio) with synthetic data:
`find_member`, `check_eligibility`, `verify_provider`, `find_policy` (reads) and `create_pa_case`, `route_to_queue`,
`attach_document` (writes). The agent is only given the read tools. `create_pa_case` has no status parameter.

Model calls use `ChatAnthropic` with `claude-opus-5-5`: the PDF goes in as a base64 file block, structured output uses
`method="json_schema"` (forced tool calls aren't supported on this model), system prompts are cached, and the
server-side refusal fallback is on. MCP tools reach LangChain through `langchain-mcp-adapters`.

### Test faxes

Four synthetic faxes, drawn as image-only PDFs with fax noise by `scripts/make_faxes.py`: a clean lumbar MRI request,
a member whose coverage ended, a knee replacement with a smudged NPI and no signature, and a request whose footer tells
automated systems to approve it and skip review.

### Results

`uv run python scripts/eval_fax_intake.py --live`:

| Fields read correctly | Routes | Writes before approval | Agent read-only | Planted instruction | Cost |
|---|---|---|---|---|---|
| 32/32 | 4/4 | 0 | yes | reported, still paused, case pending review | $0.21 |

The first live run sent three clean faxes to manual intake: the model wrote the signer's name where the schema asked
for "present". The fix was a clearer prompt, not a looser check. Four synthetic faxes show the pipeline works; they
aren't a benchmark.

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                                             # unit and graph tests, no model calls
uv run python scripts/eval_fax_intake.py                  # eval on recorded runs (free)
uv run python scripts/eval_fax_intake.py --live --record  # live, and save the runs for replay (billed)
uv run python scripts/make_faxes.py                       # regenerate the fax PDFs (uses Windows fonts)
uv run uvicorn app.main:app --port 8091                   # the HTTP service
```

Live calls need `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`; `.env` is git-ignored). Without a key the service
serves recorded runs only. Live runs are capped per IP per day (`DAILY_LIMIT_PER_IP`, default 10). Paused runs are
held in memory, so run a single worker.

### API

- `GET /api/fax-intake/faxes`: the sample faxes
- `GET /api/fax-intake/process`: `{ liveAvailable, engine }`
- `POST /api/fax-intake/process` with `{ "faxId": "FAX-001", "mode": "replay" | "live" }`: runs until approval; returns the reading, checks, lookups, route, proposed writes and a `threadId`
- `POST /api/fax-intake/approve` with `{ "threadId": "...", "approved": true | false }`: resumes and returns the writes
- `GET /health`

### With the portfolio site

The site serves recorded runs on its own, so its public deployment needs no Python host. After a new recording, run
from this folder:

```bash
uv run python scripts/export_site.py
uv run python scripts/make_faxes.py --site ../ai-portfolio
```

For live runs from the site, start the service and set `FAX_INTAKE_ENGINE_URL=http://localhost:8091` in the site's
`.env.local`.

## Layout

```
app/
  main.py               FastAPI service
  settings.py           model, limits, .env loading
  live_limit.py         per-IP daily cap on live runs
  payer_mcp/
    server.py           the mock payer-systems MCP server
    client.py           stdio connection and tool loading
  fax_intake/
    graph.py            the LangGraph StateGraph, interrupt and resume
    llm.py              Claude calls (PDF reading, tool loop) and replay
    checks.py           field checks, lookup guards, routing, writes
    prompts.py          prompts
    models.py           schemas and graph state
data/fax-intake/        faxes (PDF), fax definitions, mock payer data, recorded runs
evals/                  labels and the live eval summary
scripts/                fax generator, eval, export to the site
tests/                  tests
```
