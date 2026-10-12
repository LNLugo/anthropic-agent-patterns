"""The bounded tool: exact counts, delegated permissions, and a surface that rejects
anything outside it before Jira is ever called."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from agent_tools.jira_mock import Caller, MockJira
from agent_tools.jql_tool import MAX_RANGE_DAYS, BoundedJiraTool, QuerySpec, ToolRejected, to_jql

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"

# Billing P1 incidents created in Q3 2026: BILL-101, 104, 107, 110.
# BILL-107 carries the billing-restricted security level.
Q3_BILLING_P1 = QuerySpec("BILL", date(2026, 7, 1), date(2026, 10, 1), "Incident", "P1")


@pytest.fixture
def jira_users():
    return MockJira.from_fixture(FIXTURE)


@pytest.fixture
def tool(jira_users):
    return BoundedJiraTool(jira_users[0])


@pytest.fixture
def users(jira_users):
    return jira_users[1]


# ---------------------------------------------------------------- correct counts

@pytest.mark.parametrize(
    "user_id, expected",
    [("billing_oncall", 4), ("new_hire", 3), ("sec_engineer", 3), ("contractor", 0)],
)
def test_count_depends_on_who_asks(tool, users, user_id, expected):
    assert tool.run(Q3_BILLING_P1, users[user_id]).count == expected


def test_restricted_issue_is_invisible_without_its_security_level(tool, users):
    assert "BILL-107" in tool.run(Q3_BILLING_P1, users["billing_oncall"]).keys
    assert "BILL-107" not in tool.run(Q3_BILLING_P1, users["new_hire"]).keys


def test_filters_exclude_other_quarters_types_and_priorities(tool, users):
    keys = set(tool.run(Q3_BILLING_P1, users["billing_oncall"]).keys)
    assert keys == {"BILL-101", "BILL-104", "BILL-107", "BILL-110"}
    # BILL-095 is Q2, BILL-115 is Q4, BILL-102 is a Bug, BILL-111 is P2.
    assert not keys & {"BILL-095", "BILL-115", "BILL-102", "BILL-111"}


def test_end_date_is_exclusive(tool, users):
    # An issue created on the first day after the window must not count.
    spec = QuerySpec("BILL", date(2026, 7, 1), date(2026, 10, 3), "Incident", "P1")
    assert "BILL-115" not in tool.run(spec, users["billing_oncall"]).keys
    spec = replace(spec, created_before=date(2026, 10, 4))
    assert "BILL-115" in tool.run(spec, users["billing_oncall"]).keys


def test_group_by_priority(tool, users):
    spec = QuerySpec("BILL", date(2026, 7, 1), date(2026, 10, 1), "Incident", group_by="priority")
    result = tool.run(spec, users["billing_oncall"])
    assert result.groups == {"P1": 4, "P2": 1, "P3": 1}
    assert result.count == 6


def test_key_list_is_capped_but_count_is_exact(jira_users, users):
    small = BoundedJiraTool(jira_users[0], max_keys=2)
    result = small.run(Q3_BILLING_P1, users["billing_oncall"])
    assert result.count == 4 and len(result.keys) == 2 and result.truncated


def test_jql_is_built_by_the_tool():
    assert to_jql(Q3_BILLING_P1) == (
        'project = BILL AND issuetype = Incident AND priority = P1 '
        'AND created >= "2026-07-01" AND created < "2026-10-01" ORDER BY created DESC'
    )


# ---------------------------------------------------------------- the bound

def test_project_outside_allowlist_is_rejected_even_for_permitted_user(tool, users):
    # sec_engineer can browse SECOPS in Jira, but the tool does not expose it.
    spec = replace(Q3_BILLING_P1, project="SECOPS")
    with pytest.raises(ToolRejected, match="not available"):
        tool.run(spec, users["sec_engineer"])


def test_injection_through_a_field_is_rejected(tool, users):
    spec = replace(Q3_BILLING_P1, project="BILL OR project = SECOPS")
    with pytest.raises(ToolRejected):
        tool.run(spec, users["sec_engineer"])


@pytest.mark.parametrize(
    "change",
    [
        {"priority": "P0"},
        {"priority": "P1 OR priority = P2"},
        {"issue_type": "Epic"},
        {"status": "Deleted"},
        {"group_by": "assignee"},
    ],
)
def test_values_outside_the_enums_are_rejected(tool, users, change):
    with pytest.raises(ToolRejected):
        tool.run(replace(Q3_BILLING_P1, **change), users["billing_oncall"])


def test_date_range_is_capped(tool, users):
    spec = replace(Q3_BILLING_P1, created_from=date(2024, 1, 1))
    assert (spec.created_before - spec.created_from).days > MAX_RANGE_DAYS
    with pytest.raises(ToolRejected, match="capped"):
        tool.run(spec, users["billing_oncall"])


def test_empty_or_reversed_range_is_rejected(tool, users):
    with pytest.raises(ToolRejected):
        tool.run(replace(Q3_BILLING_P1, created_before=date(2026, 7, 1)), users["billing_oncall"])


def test_raw_jql_argument_cannot_pass_through(tool):
    with pytest.raises(ToolRejected, match="unknown arguments: jql"):
        tool.spec_from_arguments({
            "jql": "project = SECOPS", "project": "BILL",
            "created_from": "2026-07-01", "created_before": "2026-10-01",
        })


def test_arguments_round_trip(tool):
    spec = tool.spec_from_arguments({
        "project": "BILL", "issue_type": "Incident", "priority": "P1",
        "created_from": "2026-07-01", "created_before": "2026-10-01",
    })
    assert spec == Q3_BILLING_P1


@pytest.mark.parametrize("args", [
    {"project": "BILL", "created_from": "2026-07-01"},
    {"project": "BILL", "created_from": "July 1", "created_before": "2026-10-01"},
])
def test_bad_arguments_are_rejected(tool, args):
    with pytest.raises(ToolRejected):
        tool.spec_from_arguments(args)


def test_schema_enums_match_the_validator(tool):
    props = tool.schema["input_schema"]["properties"]
    assert set(props["project"]["enum"]) == tool.allowed_projects
    assert tool.schema["input_schema"]["additionalProperties"] is False


def test_tool_exposes_no_write_operation(tool):
    public = {n for n in dir(tool) if not n.startswith("_")}
    assert public == {"allowed_projects", "client", "max_keys", "name", "run", "schema", "spec_from_arguments"}


# ---------------------------------------------------------------- delegation

def test_jira_refuses_calls_without_a_named_user(jira_users):
    jira = jira_users[0]
    with pytest.raises(PermissionError):
        jira.search(Caller(""), project="BILL", created_from=date(2026, 7, 1), created_before=date(2026, 10, 1))
