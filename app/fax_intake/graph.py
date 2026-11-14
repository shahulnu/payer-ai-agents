"""The LangGraph workflow:

read_fax -> look_up -> decide -> approve (interrupt: a person) -> act

Claude reads the fax and runs the read-only lookups through MCP tools. Code validates the reading,
chooses the route and builds the writes. Nothing is written until a person approves."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import settings
from app.fax_intake import checks, llm
from app.fax_intake.models import IntakeState
from app.payer_mcp.client import READ_TOOLS, payer_tools


def _step(node: str, t0: float, detail: str) -> list[dict]:
    return [{"node": node, "ms": round((time.perf_counter() - t0) * 1000), "detail": detail}]


async def read_node(state: IntakeState) -> dict:
    t0 = time.perf_counter()
    fax_id = state["fax"]["id"]
    if state["mode"] == "live":
        extraction, usage = await llm.read_fax_live(fax_id)
        detail = f"{settings.MODEL}, {usage['inputTokens']} in / {usage['outputTokens']} out tokens"
    else:
        extraction, usage = llm.replay_entry(fax_id)["extraction"], {"inputTokens": 0, "outputTokens": 0}
        detail = "Loaded recorded live reading"
    issues = checks.check_fields(extraction)
    unclear = sum(extraction[k]["legibility"] != "clear" for k in checks.REQUIRED)
    detail = f"{len(checks.REQUIRED) - unclear} of {len(checks.REQUIRED)} required fields clear; {len(issues)} issue(s). {detail}"
    return {"extraction": extraction, "issues": issues, "usage": [usage], "trace": _step("read_fax", t0, detail)}


def _same(a: dict, b: dict) -> bool:
    return {k: str(v).strip() for k, v in a.items()} == {k: str(v).strip() for k, v in b.items()}


async def lookup_node(state: IntakeState) -> dict:
    t0 = time.perf_counter()
    fax_id, extraction = state["fax"]["id"], state["extraction"]
    async with payer_tools() as tools:
        read_only = [t for n, t in tools.items() if n in READ_TOOLS]
        if state["mode"] == "live":
            calls, summary, usage = await llm.lookup_live(fax_id, checks.fields_of(extraction), state["issues"], read_only)
        else:
            (calls, summary), usage = await llm.lookup_replay(fax_id, read_only), {"inputTokens": 0, "outputTokens": 0}

        # Code checks the agent's calls against the values on the fax, and makes any lookup it skipped.
        required = checks.required_lookups(extraction)
        for c in calls:
            c["matchesFax"] = any(c["tool"] == tool and _same(c["args"], args) for tool, args in required)
        added = 0
        for tool, args in required:
            if not any(c["tool"] == tool and c["matchesFax"] for c in calls):
                t1 = time.perf_counter()
                result = llm.tool_json(await tools[tool].ainvoke(args))
                calls.append({"tool": tool, "args": args, "result": result, "by": "code", "matchesFax": True, "ms": round((time.perf_counter() - t1) * 1000)})
                added += 1
    agent_n = sum(c["by"] == "agent" for c in calls)
    detail = f"{agent_n} tool call(s) by the agent, {added} added by code"
    return {"lookups": calls, "agent_summary": summary, "usage": [usage], "trace": _step("look_up", t0, detail)}


def decide_node(state: IntakeState) -> dict:
    t0 = time.perf_counter()
    route, reason, actions, new = checks.decide(state["fax"], state["extraction"], state["issues"], state["lookups"])
    return {"route": route, "route_reason": reason, "actions": actions, "issues": state["issues"] + new,
            "trace": _step("decide", t0, f"{route}; {len(actions)} write(s) proposed")}


def approve_node(state: IntakeState) -> dict:
    """Pause the graph until a person approves or rejects the proposed writes."""
    decision = interrupt({"route": state["route"], "reason": state["route_reason"], "actions": state["actions"]})
    return {"decision": decision, "trace": [{"node": "approve", "ms": 0, "detail": f"{'Approved' if decision.get('approved') else 'Rejected'} by {decision.get('reviewer', 'reviewer')}"}]}


async def act_node(state: IntakeState) -> dict:
    t0 = time.perf_counter()
    if not state["decision"].get("approved"):
        return {"results": [], "trace": _step("act", t0, "Nothing written: the reviewer rejected the proposed actions")}
    results, previous = [], {}
    async with payer_tools() as tools:
        for action in state["actions"]:
            args = {k: (previous.get(v.split(".", 1)[1]) if isinstance(v, str) and v.startswith("$previous.") else v) for k, v in action["args"].items()}
            t1 = time.perf_counter()
            result = llm.tool_json(await tools[action["tool"]].ainvoke(args))
            results.append({"tool": action["tool"], "args": args, "result": result, "by": "code", "ms": round((time.perf_counter() - t1) * 1000)})
            previous = result
    return {"results": results, "trace": _step("act", t0, f"{len(results)} write(s) made after approval")}


def build_graph():
    g = StateGraph(IntakeState)
    g.add_node("read_fax", read_node)
    g.add_node("look_up", lookup_node)
    g.add_node("decide", decide_node)
    g.add_node("approve", approve_node)
    g.add_node("act", act_node)
    g.add_edge(START, "read_fax")
    g.add_edge("read_fax", "look_up")
    g.add_edge("look_up", "decide")
    g.add_edge("decide", "approve")
    g.add_edge("approve", "act")
    g.add_edge("act", END)
    # The checkpointer keeps a paused run so a later request can resume it.
    return g.compile(checkpointer=InMemorySaver())


GRAPH = build_graph()


def _shape(state: dict, thread_id: str, status: str) -> dict:
    usage = state.get("usage", [])
    tin = sum(u["inputTokens"] for u in usage)
    tout = sum(u["outputTokens"] for u in usage)
    fax = state["fax"]
    return {
        "faxId": fax["id"],
        "threadId": thread_id,
        "status": status,
        "mode": state["mode"],
        "engine": "python",
        "model": settings.MODEL,
        "fax": {k: fax[k] for k in ("id", "title", "teaser", "receivedAt", "fromFax", "sender")},
        "extraction": state["extraction"],
        "issues": state["issues"],
        "lookups": state["lookups"],
        "agentSummary": state["agent_summary"],
        "route": state["route"],
        "routeReason": state["route_reason"],
        "actions": state["actions"],
        "decision": state.get("decision"),
        "results": state.get("results", []),
        "trace": state["trace"],
        "usage": {
            "inputTokens": tin,
            "outputTokens": tout,
            "costUsd": round(tin * settings.PRICE_IN_PER_MTOK / 1e6 + tout * settings.PRICE_OUT_PER_MTOK / 1e6, 4),
        } if state["mode"] == "live" else None,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


async def start(fax: dict, mode: str) -> dict:
    """Run the graph until it pauses for approval."""
    thread_id = f"{fax['id']}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    state = await GRAPH.ainvoke({"fax": fax, "mode": mode, "lookups": [], "usage": [], "trace": []}, config)
    return _shape(state, thread_id, "awaiting_approval" if state.get("__interrupt__") else "completed")


async def resume(thread_id: str, approved: bool, reviewer: str = "intake reviewer") -> dict:
    """Resume a paused run with the reviewer's decision."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await GRAPH.aget_state(config)
    if not snapshot.next:
        raise KeyError(f"No run waiting for approval with id {thread_id}.")
    decision = {"approved": approved, "reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    state = await GRAPH.ainvoke(Command(resume=decision), config)
    return _shape(state, thread_id, "completed")
