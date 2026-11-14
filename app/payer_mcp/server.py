"""A mock "payer systems" MCP server: member, eligibility, provider and policy lookups, plus the
writes an intake team makes. Synthetic data; state lives in memory for one server process.

Run on its own (stdio):  uv run python -m app.payer_mcp.server"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

DATA = json.loads((Path(__file__).resolve().parents[2] / "data" / "fax-intake" / "payer-systems.json").read_text(encoding="utf-8"))
MEMBERS = {m["id"]: m for m in DATA["members"]}
PROVIDERS = {p["npi"]: p for p in DATA["providers"]}

mcp = FastMCP("payer-systems")

# Written records for this server process.
_cases: dict[str, dict] = {}
_queue: dict[str, dict] = {}
_attachments: list[dict] = []


@mcp.tool()
def find_member(member_id: str) -> dict:
    """Look up a member by member ID (format M-12345). Returns name, date of birth, plan and coverage dates."""
    m = MEMBERS.get(member_id.strip().upper())
    return {"found": True, **m} if m else {"found": False, "member_id": member_id}


@mcp.tool()
def check_eligibility(member_id: str, date_of_service: str) -> dict:
    """Check whether a member has active coverage on a date of service (YYYY-MM-DD)."""
    m = MEMBERS.get(member_id.strip().upper())
    if not m:
        return {"eligible": False, "reason": "Member not found."}
    try:
        dos = date.fromisoformat(date_of_service)
    except ValueError:
        return {"eligible": False, "reason": f"Date of service {date_of_service!r} isn't a YYYY-MM-DD date."}
    start = date.fromisoformat(m["coverage"]["start"])
    end = date.fromisoformat(m["coverage"]["end"]) if m["coverage"]["end"] else None
    if dos < start:
        return {"eligible": False, "reason": f"Coverage starts {start}.", "coverage": m["coverage"]}
    if end and dos > end:
        return {"eligible": False, "reason": f"Coverage ended {end}.", "coverage": m["coverage"]}
    return {"eligible": True, "reason": "Active coverage on the date of service.", "plan": m["plan"], "coverage": m["coverage"]}


@mcp.tool()
def verify_provider(npi: str) -> dict:
    """Look up a provider by 10-digit NPI. Returns name, practice, specialty and network status."""
    p = PROVIDERS.get(npi.strip())
    return {"found": True, **p} if p else {"found": False, "npi": npi}


@mcp.tool()
def find_policy(service_code: str) -> dict:
    """Find the prior authorization policy that covers a CPT or HCPCS code."""
    for p in DATA["policies"]:
        if service_code.strip() in p["codes"]:
            return {"found": True, **p}
    return {"found": False, "service_code": service_code}


@mcp.tool()
def create_pa_case(
    member_id: str,
    provider_npi: str,
    service_code: str,
    diagnosis_codes: list[str],
    date_of_service: str,
    urgency: Literal["standard", "expedited"],
    policy_id: str,
    received_at: str,
    source_fax_id: str,
) -> dict:
    """Create a prior authorization case for clinical review. New cases always start as pending review;
    only clinical staff can approve or deny. The decision deadline follows CMS-0057-F: 72 hours for
    expedited requests, 7 calendar days for standard ones, from receipt."""
    received = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    due = received + (timedelta(hours=72) if urgency == "expedited" else timedelta(days=7))
    case_id = f"PA-2026-{source_fax_id.split('-')[-1]}{len(_cases) + 1:02d}"
    case = {
        "caseId": case_id,
        "status": "pending_review",
        "memberId": member_id,
        "providerNpi": provider_npi,
        "serviceCode": service_code,
        "diagnosisCodes": diagnosis_codes,
        "dateOfService": date_of_service,
        "urgency": urgency,
        "policyId": policy_id,
        "decisionDueBy": due.isoformat().replace("+00:00", "Z"),
    }
    _cases[case_id] = case
    return case


@mcp.tool()
def route_to_queue(queue: Literal["manual-intake", "eligibility-review"], source_fax_id: str, reason: str) -> dict:
    """Send a fax to a staff work queue with the reason it needs a person."""
    item_id = f"Q-{queue.split('-')[0].upper()}-{source_fax_id.split('-')[-1]}{len(_queue) + 1:02d}"
    _queue[item_id] = {"itemId": item_id, "queue": queue, "sourceFaxId": source_fax_id, "reason": reason, "status": "open"}
    return _queue[item_id]


@mcp.tool()
def attach_document(record_id: str, source_fax_id: str) -> dict:
    """Attach the original fax PDF to a case or queue item."""
    if record_id not in _cases and record_id not in _queue:
        return {"attached": False, "reason": f"No case or queue item {record_id}."}
    att = {"attached": True, "recordId": record_id, "document": f"{source_fax_id}.pdf", "attachmentId": f"DOC-{len(_attachments) + 1:03d}"}
    _attachments.append(att)
    return att


if __name__ == "__main__":
    mcp.run()
