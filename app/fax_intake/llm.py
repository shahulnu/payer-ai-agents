"""Claude calls through LangChain: reading the fax PDF, and the read-only lookup tool loop.
Replay mode loads a recorded live run instead and re-runs the recorded tool calls."""

from __future__ import annotations

import base64
import json
import time
from functools import lru_cache
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app import settings
from app.fax_intake.models import FaxExtraction
from app.fax_intake.prompts import LOOKUP_PROMPT, READ_PROMPT, lookup_message

FAX_DIR = settings.DATA_DIR / "fax-intake" / "faxes"
RECORDED_PATH = settings.DATA_DIR / "fax-intake" / "recorded.json"
MAX_TURNS = 8


class ModelDeclinedError(Exception):
    """The model declined (stop_reason "refusal"), even after the server-side fallback."""


class ReplayMissingError(Exception):
    """There is no recorded run for this fax."""


def _cached_system(text: str) -> SystemMessage:
    return SystemMessage(content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}])


@lru_cache(maxsize=1)
def _chat() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.MODEL,
        max_tokens=16000,
        effort="medium",
        # On a safety-classifier decline, the API retries on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        model_kwargs={"fallbacks": "default"},
    )


def usage_of(msg) -> dict:
    u = getattr(msg, "usage_metadata", None) or {}
    return {"inputTokens": u.get("input_tokens", 0), "outputTokens": u.get("output_tokens", 0)}


def tool_json(result) -> dict:
    """MCP tool results arrive as text blocks holding JSON."""
    content = result.content if isinstance(result, ToolMessage) else result
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {"raw": str(content)}


async def read_fax_live(fax_id: str) -> tuple[dict, dict]:
    pdf = base64.b64encode((FAX_DIR / f"{fax_id}.pdf").read_bytes()).decode()
    model = _chat().with_structured_output(FaxExtraction, method="json_schema", include_raw=True)
    result = await model.ainvoke([
        _cached_system(READ_PROMPT),
        HumanMessage(content=[
            {"type": "file", "base64": pdf, "mime_type": "application/pdf"},
            {"type": "text", "text": "Read this fax."},
        ]),
    ])
    if result["raw"].response_metadata.get("stop_reason") == "refusal":
        raise ModelDeclinedError("The model declined to read this fax. Route it to manual intake.")
    if result["parsed"] is None:
        raise RuntimeError(f"The fax reading didn't match the schema: {result.get('parsing_error')}")
    return result["parsed"].model_dump(), usage_of(result["raw"])


async def lookup_live(fax_id: str, fields: dict, issues: list[dict], tools: list) -> tuple[list[dict], str, dict]:
    """The agent loop. It sees only the read-only tools, so it can't write anything."""
    by_name = {t.name: t for t in tools}
    llm = _chat().bind_tools(tools)
    messages = [_cached_system(LOOKUP_PROMPT), HumanMessage(content=lookup_message(fax_id, fields, issues))]
    calls: list[dict] = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    summary = ""
    for _ in range(MAX_TURNS):
        ai = await llm.ainvoke(messages)
        u = usage_of(ai)
        usage = {k: usage[k] + u[k] for k in usage}
        if ai.response_metadata.get("stop_reason") == "refusal":
            raise ModelDeclinedError("The model declined this lookup. Route the fax to manual intake.")
        messages.append(ai)
        if not ai.tool_calls:
            summary = ai.text if hasattr(ai, "text") and isinstance(ai.text, str) else str(ai.content)
            break
        for tc in ai.tool_calls:
            t0 = time.perf_counter()
            tool = by_name.get(tc["name"])
            if tool is None:
                msg = ToolMessage(content=json.dumps({"error": f"Unknown tool {tc['name']}"}), tool_call_id=tc["id"])
            else:
                msg = await tool.ainvoke(tc)
            messages.append(msg)
            calls.append({"tool": tc["name"], "args": tc["args"], "result": tool_json(msg), "by": "agent", "ms": round((time.perf_counter() - t0) * 1000)})
    return calls, summary.strip(), usage


def recorded() -> dict:
    if not RECORDED_PATH.exists():
        return {"cases": {}}
    return json.loads(RECORDED_PATH.read_text(encoding="utf-8"))


def replay_entry(fax_id: str) -> dict:
    entry = recorded().get("cases", {}).get(fax_id)
    if entry is None:
        raise ReplayMissingError(f"No recorded run for {fax_id}. Run the eval with --live --record.")
    return entry


async def lookup_replay(fax_id: str, tools: list) -> tuple[list[dict], str]:
    """Re-run the agent's recorded tool calls against the MCP server, so results are real."""
    entry = replay_entry(fax_id)
    by_name = {t.name: t for t in tools}
    calls = []
    for c in entry["lookup"]["toolCalls"]:
        t0 = time.perf_counter()
        result = tool_json(await by_name[c["tool"]].ainvoke(c["args"]))
        calls.append({"tool": c["tool"], "args": c["args"], "result": result, "by": "agent", "ms": round((time.perf_counter() - t0) * 1000)})
    return calls, entry["lookup"]["summary"]


def fax_path(fax_id: str) -> Path:
    return FAX_DIR / f"{fax_id}.pdf"
