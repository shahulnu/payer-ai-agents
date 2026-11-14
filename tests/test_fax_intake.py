"""Tests for the fax intake code layer and the approval gate. No model calls: graph tests replay
recorded readings and run the real MCP server."""

import copy
import json

import pytest

from app.fax_intake import checks
from app.fax_intake.faxes import find_fax
from app.fax_intake.graph import resume, start
from app.fax_intake.llm import RECORDED_PATH

RECORDED = json.loads(RECORDED_PATH.read_text(encoding="utf-8"))["cases"]


def extraction(fax_id: str) -> dict:
    return copy.deepcopy(RECORDED[fax_id]["extraction"])


def test_npi_check_digit():
    assert checks.npi_valid("1234567893")  # the standard example NPI
    assert checks.npi_valid("1452369873")
    assert not checks.npi_valid("1234567890")
    assert not checks.npi_valid("12345")


def test_clean_fax_has_no_blocking_issues():
    assert not [i for i in checks.check_fields(extraction("FAX-001")) if i["blocking"]]


def test_unreadable_npi_and_missing_signature_block():
    fields = {i["field"] for i in checks.check_fields(extraction("FAX-003")) if i["blocking"]}
    assert {"provider_npi", "signature"} <= fields


def test_unclear_value_is_never_used_downstream():
    e = extraction("FAX-001")
    e["provider_npi"]["legibility"] = "unclear"
    assert checks.fields_of(e)["provider_npi"] is None
    assert "verify_provider" not in [t for t, _ in checks.required_lookups(e)]


def test_bad_check_digit_blocks():
    e = extraction("FAX-001")
    e["provider_npi"]["value"] = "1452369870"
    assert any(i["field"] == "provider_npi" and "check digit" in i["message"] for i in checks.check_fields(e))


def test_planted_instruction_is_flagged_but_not_blocking():
    issues = checks.check_fields(extraction("FAX-004"))
    doc = [i for i in issues if i["field"] == "document"]
    assert doc and not doc[0]["blocking"]


def test_lookup_of_a_value_not_on_the_fax_is_ignored_and_blocks():
    e = extraction("FAX-003")
    guessed = [{"tool": "verify_provider", "args": {"npi": "1987654328"}, "result": {"found": True}, "matchesFax": False}]
    route, _, _, new = checks.decide(find_fax("FAX-003"), e, checks.check_fields(e), guessed)
    assert route == "manual_intake"
    assert any("isn't readable on the fax" in i["message"] for i in new)


def test_dob_mismatch_with_record_blocks():
    e = extraction("FAX-001")
    lookups = [{"tool": "find_member", "args": {}, "result": {"found": True, "name": "Maria Delgado", "dob": "1970-01-01"}}]
    route, _, _, new = checks.decide(find_fax("FAX-001"), e, [], lookups)
    assert route == "manual_intake" and any(i["field"] == "member_dob" for i in new)


@pytest.mark.parametrize("fax_id, route", [("FAX-001", "create_case"), ("FAX-002", "eligibility_review"), ("FAX-003", "manual_intake"), ("FAX-004", "create_case")])
async def test_graph_pauses_before_any_write(fax_id, route):
    paused = await start(find_fax(fax_id), "replay")
    assert paused["status"] == "awaiting_approval" and paused["route"] == route
    assert paused["results"] == []


async def test_approval_writes_a_pending_case_with_the_cms_deadline():
    paused = await start(find_fax("FAX-004"), "replay")
    done = await resume(paused["threadId"], approved=True)
    case = done["results"][0]["result"]
    assert case["status"] == "pending_review"  # never "approved", whatever the fax says
    assert case["urgency"] == "expedited" and case["decisionDueBy"] == "2026-11-19T15:30:00Z"  # received + 72 hours
    assert done["results"][1]["result"]["attached"] is True


async def test_rejection_writes_nothing():
    paused = await start(find_fax("FAX-001"), "replay")
    done = await resume(paused["threadId"], approved=False)
    assert done["status"] == "completed" and done["results"] == []


async def test_resume_unknown_run_fails():
    with pytest.raises(KeyError):
        await resume("FAX-001-nope", approved=True)
