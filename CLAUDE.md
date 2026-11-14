# Payer AI agents (Python)

Health plan operations agents for Shahul Ummarali's portfolio, built with LangGraph, LangChain and MCP on Claude. The
portfolio site is `../ai-portfolio`; it shows these agents in its "Healthcare payer" section and serves recorded runs
exported from here. README.md has the design, commands and API. Sister repo for developer-tools agents:
`../langgraph-dev-agents`.

## Conventions
- Python via `uv`. Model `claude-opus-5-5` through `langchain_anthropic.ChatAnthropic`; structured output with
  `method="json_schema"` (the default forces a tool call, which Claude Opus 5.5 rejects). Keep the refusal fallback
  (`betas=["server-side-fallback-2026-07-01"]`, `model_kwargs={"fallbacks": "default"}`) and cached system prompts.
- MCP: `app/payer_mcp/server.py` (FastMCP, stdio), loaded through `langchain-mcp-adapters`. The agent gets read tools
  only; writes run in code after the LangGraph interrupt resumes. Keep `create_pa_case` without a status parameter.
- Code decides: field checks, lookup guards and routing are in `fax_intake/checks.py`. If the eval fails, tighten the
  prompt, not the check (the signature-value fix is the example).
- Only `--live` eval runs write `evals/fax-intake-summary.json`; `--live --record` rewrites `data/fax-intake/recorded.json`.
  After re-recording, run `scripts/export_site.py` (and `scripts/make_faxes.py --site ../ai-portfolio` if the faxes changed).
- Synthetic data only. Faxes are generated from `data/fax-intake/faxes.json`; labels in `evals/fax-intake-labels.json`.

## Standing rules from the owner
- Don't commit or push to git until asked.
- Never print or echo the API key. It's in `.env` here (git-ignored), and copies are in the other projects; a rotated key must be updated in all of them.
- Stop the uvicorn service when done.
