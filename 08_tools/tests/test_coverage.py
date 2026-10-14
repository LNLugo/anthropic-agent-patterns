"""The coverage check: a valid query runs only if it covers what the question asked for."""

from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agent_tools.coverage import check, projects_named
from agent_tools.jira_mock import MockJira
from agent_tools.jql_tool import BoundedJiraTool
from agent_tools.model_router import EVIDENCE_FIELD, ModelRouter

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 12)
SEPT = {"created_from": "2026-09-01", "created_before": "2026-10-01"}


@pytest.mark.parametrize("text, named", [
    ("How many bugs did BILL and PAY open?", {"BILL", "PAY"}),
    ("billing platform incidents", {"BILL"}),
    ("payments vs infrastructure", {"PAY", "INFRA"}),
    ("security operations tickets", {"SECOPS"}),
    ("the invoice stack", set()),
    ("How many incidents last week?", set()),
])
def test_projects_named(text, named):
    assert projects_named(text) == named


@pytest.mark.parametrize("question, project, evidence, fires", [
    # the held-out miss: two projects, one counted
    ("How many bugs did BILL and PAY open in September?", "BILL", "BILL", "one project at a time"),
    # no project named: the model can only quote generic words, or words that are not there
    ("How many incidents were created last week?", "PAY", "incidents", "does not name a project"),
    ("How many incidents were created last week?", "PAY", "payments", "not in the question"),
    ("How many incidents were created last week?", "PAY", "", "no words"),
    ("How many incidents were created last week?", "PAY", None, "no words"),
    # a project off the allowlist, mapped onto an allowed one
    ("How many OPS tickets came in this quarter?", "INFRA", "OPS", None),  # see note in the test below
    ("How many security operations tickets this quarter?", "INFRA", "security operations", "one project at a time"),
    # evidence that names a different project than the one chosen
    ("How many payments incidents last month?", "BILL", "payments", "one project at a time"),
    # must pass: one project, plainly or informally named
    ("How many P1s did billing have last quarter?", "BILL", "billing", None),
    ("Need a read on the invoice stack, tickets opened in September?", "BILL", "invoice stack", None),
    ("Infra tickets this month. Need it for ELT.", "INFRA", "Infra", None),
    ("BILL bugs logged Q4-to-date?", "BILL", "BILL", None),
])
def test_check(question, project, evidence, fires):
    reason = check(question, project, evidence)
    if fires is None:
        assert reason is None
    else:
        assert reason and fires in reason


def test_unknown_names_are_the_models_call():
    """'OPS' is real text and not a known project, so the check cannot tell whether it
    means INFRA. That limit is stated in the README; the quoted evidence is logged."""
    assert check("How many OPS tickets came in this quarter?", "INFRA", "OPS") is None


# ---------------------------------------------------------------- inside the router

class ScriptedModel:
    def __init__(self, script):
        self.script, self.bound_tools = script, None

    def bind_tools(self, tools, tool_choice=None):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        call = self.script[messages[-1][1]]
        return AIMessage(content="", tool_calls=[{"name": call[0], "args": call[1], "id": "c1"}],
                         usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


@pytest.fixture
def tool():
    jira, _ = MockJira.from_fixture(FIXTURES / "mini_jira.json")
    return BoundedJiraTool(jira)


def test_schema_requires_evidence_only_when_the_check_is_on(tool):
    on, off = ScriptedModel({}), ScriptedModel({})
    ModelRouter(on, tool)
    ModelRouter(off, tool, coverage=False)
    params_on = on.bound_tools[0]["function"]["parameters"]
    params_off = off.bound_tools[0]["function"]["parameters"]
    assert EVIDENCE_FIELD in params_on["required"] and EVIDENCE_FIELD in params_on["properties"]
    assert EVIDENCE_FIELD not in params_off["properties"]
    assert "project_evidence" not in tool.schema["input_schema"]["properties"]  # the Jira tool is unchanged


def test_two_projects_become_a_question_not_a_count(tool):
    q = "How many bugs did BILL and PAY open in September?"
    args = {"project": "BILL", "issue_type": "Bug", EVIDENCE_FIELD: "BILL", **SEPT}
    d = ModelRouter(ScriptedModel({q: ("count_jira_issues", args)}), tool).decide(q, TODAY)
    assert d.route == "clarify" and d.spec is None and "one project at a time" in d.coverage
    assert d.arguments[EVIDENCE_FIELD] == "BILL"  # what the model claimed is kept for review


def test_a_covered_query_still_runs(tool):
    q = "How many P1 incidents did billing have in September?"
    args = {"project": "BILL", "issue_type": "Incident", "priority": "P1", EVIDENCE_FIELD: "billing", **SEPT}
    d = ModelRouter(ScriptedModel({q: ("count_jira_issues", args)}), tool).decide(q, TODAY)
    assert d.route == "aggregation" and d.spec.project == "BILL" and d.coverage is None


def test_the_bound_still_runs_first(tool):
    """A raw jql argument is refused by the validator; the coverage check never sees it."""
    q = "How many billing bugs in September?"
    args = {"project": "BILL", "jql": "project = SECOPS", EVIDENCE_FIELD: "billing", **SEPT}
    d = ModelRouter(ScriptedModel({q: ("count_jira_issues", args)}), tool).decide(q, TODAY)
    assert d.rejected and "unknown arguments" in d.rejected and d.coverage is None
