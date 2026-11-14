"""The deterministic layer. Code validates what Claude read, decides the route from lookup results
and builds the proposed writes. Claude never chooses an action."""

from __future__ import annotations

import re
from datetime import date

MEMBER_ID = re.compile(r"^M-\d{5}$")
CPT = re.compile(r"^(\d{5}|[A-Z]\d{4})$")
ICD10 = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
REQUIRED = ["member_name", "member_id", "member_dob", "provider_name", "provider_npi", "service_code", "diagnosis_codes", "date_of_service", "urgency"]


def npi_valid(npi: str) -> bool:
    """NPI check digit: the Luhn algorithm over the prefix 80840 plus the first nine digits."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi[:9])):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(npi[9])


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def fields_of(extraction: dict) -> dict:
    """The normalized values to use downstream. A field that isn't clear is treated as unknown."""
    keys = REQUIRED + ["service_description", "signature"]
    out = {k: ((extraction[k]["value"] or "").strip() or None if extraction[k]["legibility"] == "clear" else None) for k in keys}
    for k in ("urgency", "signature"):
        out[k] = out[k].lower() if out[k] else None
    if out["member_id"]:
        out["member_id"] = out["member_id"].upper()
    return out


def check_fields(extraction: dict) -> list[dict]:
    issues: list[dict] = []

    def add(field: str, message: str, blocking: bool = True):
        issues.append({"field": field, "message": message, "blocking": blocking})

    for k in REQUIRED:
        rv = extraction[k]
        if rv["legibility"] == "missing" or not rv["value"] and rv["legibility"] != "unclear":
            add(k, "Missing on the fax.")
        elif rv["legibility"] == "unclear":
            seen = f' (reads as "{rv["as_written"]}")' if rv["as_written"] else ""
            add(k, f"Not clearly readable{seen}.")

    f = fields_of(extraction)
    if f["member_id"] and not MEMBER_ID.match(f["member_id"]):
        add("member_id", f'"{f["member_id"]}" isn\'t a member ID (M-12345).')
    if f["provider_npi"] and not npi_valid(f["provider_npi"]):
        add("provider_npi", f'{f["provider_npi"]} fails the NPI check digit.')
    if f["service_code"] and not CPT.match(f["service_code"]):
        add("service_code", f'"{f["service_code"]}" isn\'t a CPT or HCPCS code.')
    if f["diagnosis_codes"]:
        bad = [c for c in (x.strip() for x in f["diagnosis_codes"].split(",")) if c and not ICD10.match(c)]
        if bad:
            add("diagnosis_codes", f"Not ICD-10 format: {', '.join(bad)}.")
    for k in ("member_dob", "date_of_service"):
        if f[k] and not _date(f[k]):
            add(k, f'"{f[k]}" isn\'t a valid date.')
    if f["urgency"] and f["urgency"] not in ("standard", "expedited"):
        add("urgency", f'"{f["urgency"]}" isn\'t standard or expedited.')

    sig = extraction["signature"]
    if (sig["value"] or "").strip().lower() != "present" or sig["legibility"] != "clear":
        add("signature", "The provider attestation isn't signed.")
    if extraction["instructions_to_automation"]:
        add("document", "The fax contains text addressed to automated systems. It was ignored; staff should review it.", blocking=False)
    return issues


def _name(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower().split(",")[0])


def decide(fax: dict, extraction: dict, issues: list[dict], lookups: list[dict]) -> tuple[str, str, list[dict], list[dict]]:
    """Choose the route from the checks and the lookup results (not from the agent's summary),
    and build the writes to propose. Returns route, reason, actions and any new issues."""
    f = fields_of(extraction)
    new: list[dict] = []
    by_tool = {l["tool"]: l["result"] for l in lookups if l.get("matchesFax", True)}

    member = by_tool.get("find_member")
    if f["member_id"] and member is not None:
        if not member.get("found"):
            new.append({"field": "member_id", "message": f"No member {f['member_id']} in the plan's records.", "blocking": True})
        else:
            if f["member_dob"] and member["dob"] != f["member_dob"]:
                new.append({"field": "member_dob", "message": f"Date of birth on the fax ({f['member_dob']}) doesn't match the record ({member['dob']}).", "blocking": True})
            if f["member_name"] and _name(member["name"]).replace(" ", "") != _name(f["member_name"]).replace(" ", ""):
                new.append({"field": "member_name", "message": f"Name on the fax doesn't match the record ({member['name']}).", "blocking": True})
    provider = by_tool.get("verify_provider")
    if f["provider_npi"] and provider is not None:
        if not provider.get("found"):
            new.append({"field": "provider_npi", "message": f"NPI {f['provider_npi']} isn't in the provider directory.", "blocking": True})
        else:
            if _name(provider["name"]) != _name(f["provider_name"]):
                new.append({"field": "provider_name", "message": f"Provider name doesn't match the NPI record ({provider['name']}).", "blocking": True})
            if provider.get("network") == "out":
                new.append({"field": "provider_npi", "message": "Provider is out of network; the reviewer should check out-of-network rules.", "blocking": False})
    policy = by_tool.get("find_policy")
    if f["service_code"] and policy is not None and not policy.get("found"):
        new.append({"field": "service_code", "message": f"No prior authorization policy covers {f['service_code']}.", "blocking": True})
    needed = {tool for tool, _ in required_lookups(extraction)}
    for l in lookups:
        if not l.get("matchesFax", True):
            if l["tool"] in needed:
                # The agent used different arguments; code already re-ran the lookup with the fax's values.
                new.append({"field": l["tool"], "message": f"The agent called {l['tool']} with {l['args']}, which differs from the fax; code used the fax's values instead.", "blocking": False})
            else:
                new.append({"field": l["tool"], "message": f"The agent looked up a value that isn't readable on the fax ({l['args']}); the result was ignored.", "blocking": True})

    all_issues = issues + new
    blocking = [i for i in all_issues if i["blocking"]]
    fax_id = fax["id"]
    if blocking:
        reason = "; ".join(f"{i['field']}: {i['message']}" for i in blocking)
        return "manual_intake", reason, [
            {"tool": "route_to_queue", "args": {"queue": "manual-intake", "source_fax_id": fax_id, "reason": reason}},
            {"tool": "attach_document", "args": {"record_id": "$previous.itemId", "source_fax_id": fax_id}},
        ], new
    elig = by_tool.get("check_eligibility") or {}
    if not elig.get("eligible"):
        reason = f"Not eligible on {f['date_of_service']}: {elig.get('reason', 'eligibility not confirmed')}"
        return "eligibility_review", reason, [
            {"tool": "route_to_queue", "args": {"queue": "eligibility-review", "source_fax_id": fax_id, "reason": reason}},
            {"tool": "attach_document", "args": {"record_id": "$previous.itemId", "source_fax_id": fax_id}},
        ], new
    reason = "Every field is readable and checks out, and the member is eligible on the date of service."
    return "create_case", reason, [
        {
            "tool": "create_pa_case",
            "args": {
                "member_id": f["member_id"],
                "provider_npi": f["provider_npi"],
                "service_code": f["service_code"],
                "diagnosis_codes": [c.strip() for c in f["diagnosis_codes"].split(",")],
                "date_of_service": f["date_of_service"],
                "urgency": f["urgency"],
                "policy_id": policy["id"],
                "received_at": fax["receivedAt"],
                "source_fax_id": fax_id,
            },
        },
        {"tool": "attach_document", "args": {"record_id": "$previous.caseId", "source_fax_id": fax_id}},
    ], new


def required_lookups(extraction: dict) -> list[tuple[str, dict]]:
    """The lookups the route depends on, with the exact arguments taken from the fax."""
    f = fields_of(extraction)
    out: list[tuple[str, dict]] = []
    if f["member_id"] and MEMBER_ID.match(f["member_id"]):
        out.append(("find_member", {"member_id": f["member_id"]}))
        if f["date_of_service"] and _date(f["date_of_service"]):
            out.append(("check_eligibility", {"member_id": f["member_id"], "date_of_service": f["date_of_service"]}))
    if f["provider_npi"] and npi_valid(f["provider_npi"]):
        out.append(("verify_provider", {"npi": f["provider_npi"]}))
    if f["service_code"] and CPT.match(f["service_code"]):
        out.append(("find_policy", {"service_code": f["service_code"]}))
    return out
