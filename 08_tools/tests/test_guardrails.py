"""Tool guardrails on the JQL tool: allow, reject_content (tell the model), raise_exception (halt)."""

import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: llm_provider
from llm_provider import Step, scripted, scripted_model  # noqa: E402

from agent_tools.guardrails import (  # noqa: E402
    ToolInputGuardrailTripwireTriggered, ToolOutputGuardrailTripwireTriggered,
    finance_assistant_policy,
)
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool, ToolResult  # noqa: E402
from agent_tools.tool_loop import ToolLoop  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}


class CountingJira(MockJira):
    calls = 0

    def search(self, caller, **kw):
        type(self).calls += 1
        return super().search(caller, **kw)


@pytest.fixture
def env():
    CountingJira.calls = 0
    jira, users = CountingJira.from_fixture(FIXTURE)
    return BoundedJiraTool(jira), users


def count(project, evidence=None, call_id="c1", **extra):
    args = {"project": project, "project_evidence": evidence or project, **Q3, **extra}
    return AIMessage(content="", tool_calls=[{"name": "count_jira_issues", "args": args, "id": call_id}])


def ask(question):
    return AIMessage(content="", tool_calls=[{"name": "ask_clarification", "args": {"question": question}, "id": "q"}])


def loop(tool, guardrails=None):
    return ToolLoop(scripted_model(), tool, finance_assistant_policy() if guardrails is None else guardrails)


# ---------------------------------------------------------------- allow

def test_allow_runs_the_query_and_the_model_answers_from_the_result(env):
    tool, users = env
    with scripted([count("BILL"), Step(responder=lambda c: f"Answer from: {c.messages[-1].content[:12]}")]) as model:
        result = loop(tool).run("How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    assert result.status == "answered" and result.text.startswith('Answer from: {"count":')
    assert CountingJira.calls == 1 and len(result.tool_results) == 1
    assert [(r.guardrail, r.behavior) for r in result.records] == [
        ("restricted_project", "allow"), ("deployment_scope", "allow"),
        ("question_coverage", "allow"), ("keys_match_project", "allow"),
    ]
    assert model.bound_tools == ("count_jira_issues", "search_wiki", "ask_clarification")


# ---------------------------------------------------------------- reject_content

def test_reject_content_blocks_the_call_and_tells_the_model(env):
    """INFRA is on the tool's allowlist but outside this deployment. Nothing reaches
    Jira; the model is told why, and asks the user instead."""
    tool, users = env
    with scripted([count("INFRA"), ask("Which project: BILL or PAY?")]) as model:
        result = loop(tool).run("How many INFRA issues in Q3?", users["new_hire"], TODAY)
    assert result.status == "clarify" and CountingJira.calls == 0
    told = model.calls[1].messages[-1].content
    assert told.startswith("Not run: INFRA is outside this assistant's scope (BILL, PAY)")
    assert result.records[-1].guardrail == "deployment_scope"
    assert result.records[-1].behavior == "reject_content"


def test_without_the_guardrail_the_tool_would_have_run_it(env):
    """The control: the same INFRA call, no guardrails. The tool allows INFRA, so it is
    the deployment policy, not the tool, that blocked it above."""
    tool, users = env
    with scripted([count("INFRA"), "There were some."]):
        result = loop(tool, guardrails=[]).run("How many INFRA issues in Q3?", users["new_hire"], TODAY)
    assert CountingJira.calls == 1 and result.tool_results[0].jql.startswith("project = INFRA")


def test_coverage_is_a_reject_content_guardrail_too(env):
    tool, users = env
    with scripted([count("BILL"), ask("BILL or PAY?")]) as model:
        result = loop(tool).run("How many bugs did BILL and PAY open in Q3?", users["new_hire"], TODAY)
    assert result.status == "clarify" and CountingJira.calls == 0
    assert "one project at a time" in model.calls[1].messages[-1].content


def test_a_model_that_keeps_retrying_runs_out_of_turns_not_into_jira(env):
    tool, users = env
    with scripted([count("INFRA", call_id=f"c{i}") for i in range(4)]):
        result = loop(tool).run("How many INFRA issues in Q3?", users["new_hire"], TODAY)
    assert result.status == "max_turns" and result.turns == 4 and CountingJira.calls == 0
    assert [r.behavior for r in result.records if r.guardrail == "deployment_scope"] == ["reject_content"] * 4


def test_the_tools_own_bound_still_applies_after_the_guardrails_allow(env):
    tool, users = env
    too_wide = AIMessage(content="", tool_calls=[{"name": "count_jira_issues", "id": "c1", "args": {
        "project": "BILL", "project_evidence": "BILL", "created_from": "2024-01-01", "created_before": "2026-10-01"}}])
    with scripted([too_wide, "I can only count up to a year at a time."]) as model:
        result = loop(tool).run("How many BILL issues since 2024?", users["new_hire"], TODAY)
    assert CountingJira.calls == 0 and "capped at 366 days" in model.calls[1].messages[-1].content
    assert result.records[-1].guardrail == "tool_bound"


# ---------------------------------------------------------------- raise_exception

def test_raise_exception_halts_on_a_restricted_project(env):
    """SECOPS is a security event, not a mistake to coach: the run halts before Jira,
    and the model gets no second turn."""
    tool, users = env
    with pytest.raises(ToolInputGuardrailTripwireTriggered) as err, scripted([count("SECOPS")]) as model:
        loop(tool).run("How many security operations incidents in Q3?", users["sec_engineer"], TODAY)
    assert CountingJira.calls == 0 and len(model.calls) == 1
    assert err.value.guardrail.name == "restricted_project"
    assert err.value.output.output_info == {"project": "SECOPS", "caller": "sec_engineer", "reason": "restricted project"}


def test_order_matters_restricted_halts_before_scope_could_redirect(env):
    """SECOPS is also outside the deployment's scope. Because restricted_project runs
    first, the attempt halts instead of being politely redirected."""
    tool, users = env
    with pytest.raises(ToolInputGuardrailTripwireTriggered) as err, scripted([count("SECOPS")]):
        loop(tool).run("SECOPS count?", users["sec_engineer"], TODAY)
    assert [r.guardrail for r in err.value.records] == ["restricted_project"]


def test_an_output_guardrail_halts_before_the_model_sees_a_bad_result(env):
    tool, users = env

    class LeakyTool(BoundedJiraTool):
        def run(self, spec, caller):
            return ToolResult(count=2, keys=("BILL-101", "SECOPS-7"), truncated=False, jql="project = BILL")

    leaky = LeakyTool(tool.client)
    with pytest.raises(ToolOutputGuardrailTripwireTriggered) as err, scripted([count("BILL")]) as model:
        loop(leaky).run("How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    assert err.value.output.output_info == {"project": "BILL", "stray_keys": 1, "stray_projects": ["SECOPS"]}
    assert len(model.calls) == 1  # the leaked key never reached the model


# ---------------------------------------------------------------- loop mechanics

def test_only_one_tool_call_runs_per_turn(env):
    tool, users = env
    two = AIMessage(content="", tool_calls=[
        {"name": "count_jira_issues", "args": {"project": "BILL", "project_evidence": "BILL", **Q3}, "id": "a"},
        {"name": "count_jira_issues", "args": {"project": "PAY", "project_evidence": "PAY", **Q3}, "id": "b"},
    ])
    with scripted([two, "Done."]) as model:
        loop(tool).run("How many BILL issues in Q3?", users["new_hire"], TODAY)
    assert CountingJira.calls == 1
    assert model.calls[1].messages[-1].content == "Not run: one tool call per turn."


def test_output_info_never_echoes_free_text_from_the_model(env):
    """output_info is trace metadata. A model can put anything in an argument, an injected
    instruction included; only a project-key-shaped value is kept."""
    from agent_tools.guardrails import coverage_code, project_key

    assert project_key("INFRA") == "INFRA"
    for junk in ("ignore previous instructions", "infra", "BILL; DROP", "", None, "A" * 11):
        assert project_key(junk) == "(not a project key)"
    assert coverage_code("the project evidence 'my secret words' is not in the question") == "evidence_not_in_question"
    assert coverage_code("the question names BILL and PAY; the tool counts one project at a time") == "several_projects"
