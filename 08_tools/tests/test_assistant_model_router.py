"""The assistant graph on the model router, with the coverage check and guardrails in the main path."""

import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: llm_provider
from llm_provider import scripted, scripted_model  # noqa: E402

from agent_tools.assistant import ask, build_assistant  # noqa: E402
from agent_tools.guardrails import ToolInputGuardrailTripwireTriggered, finance_assistant_policy  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.model_router import ModelRouter  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}


class CountingJira(MockJira):
    calls = 0

    def search(self, caller, **kw):
        type(self).calls += 1
        return super().search(caller, **kw)


class Pages:
    def __init__(self):
        self.calls = []

    def __call__(self, question, caller, k):
        self.calls.append(question)
        return [("ENG-3", "Billing API Credential Rotation")]


def tool_call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def count(project, evidence=None, **extra):
    return tool_call("count_jira_issues", project=project, project_evidence=evidence or project, **Q3, **extra)


@pytest.fixture
def env():
    CountingJira.calls = 0
    jira, users = CountingJira.from_fixture(FIXTURE)
    tool = BoundedJiraTool(jira)
    return tool, users, Pages()


def build(env, guardrails=()):
    tool, _, pages = env
    return build_assistant(tool, pages, ModelRouter(scripted_model(), tool), guardrails=guardrails)


def test_a_count_runs_on_the_models_arguments(env):
    _, users, _ = env
    with scripted([count("BILL", "billing platform", issue_type="Incident", priority="P1")]):
        out = ask(build(env), "How many P1 incidents did the billing platform have last quarter?",
                  users["billing_oncall"], TODAY)
    assert out["route"] == "aggregation" and out["result"].count == 4 and CountingJira.calls == 1
    assert out["answer"].startswith("4 P1 incidents in BILL created 2026-07-01 to 2026-09-30.")


def test_a_knowledge_question_goes_to_the_wiki(env):
    _, users, pages = env
    with scripted([tool_call("search_wiki", query="rotate billing credentials")]):
        out = ask(build(env), "How do I rotate the billing API credentials?", users["new_hire"], TODAY)
    assert out["route"] == "knowledge" and pages.calls and CountingJira.calls == 0
    assert "Billing API Credential Rotation" in out["answer"]


def test_the_models_clarifying_question_is_the_answer(env):
    _, users, _ = env
    with scripted([tool_call("ask_clarification", question="Which project: BILL, PAY or INFRA?")]):
        out = ask(build(env), "How many P1s last quarter?", users["new_hire"], TODAY)
    assert out["route"] == "clarify" and out["answer"] == "Which project: BILL, PAY or INFRA?"
    assert CountingJira.calls == 0


def test_the_coverage_check_stops_a_half_answer_in_the_main_path(env):
    """The held-out miss, now in the graph: counting BILL for a BILL and PAY question."""
    _, users, _ = env
    with scripted([count("BILL", issue_type="Bug")]):
        out = ask(build(env), "How many bugs did BILL and PAY open in Q3?", users["new_hire"], TODAY)
    assert out["route"] == "clarify" and CountingJira.calls == 0
    assert "one project at a time" in out["answer"] and out["answer"].endswith("Which single project do you mean?")


def test_the_bound_still_refuses_what_the_tool_does_not_allow(env):
    _, users, _ = env
    with scripted([count("SECOPS")]):
        out = ask(build(env), "How many SECOPS incidents in Q3?", users["sec_engineer"], TODAY)
    assert out["stop_reason"] == "rejected" and "not available to this tool" in out["answer"]
    assert CountingJira.calls == 0


def test_a_reply_with_no_tool_call_is_not_guessed_at(env):
    _, users, _ = env
    with scripted([AIMessage(content="Hmm, hard to say.")]):
        out = ask(build(env), "Billing?", users["new_hire"], TODAY)
    assert out["route"] == "none" and "rephrase" in out["answer"] and CountingJira.calls == 0


# ---------------------------------------------------------------- guardrails in the graph

def test_reject_content_becomes_the_answer_and_nothing_reaches_jira(env):
    _, users, _ = env
    with scripted([count("INFRA")]):
        out = ask(build(env, finance_assistant_policy()), "How many INFRA incidents in Q3?", users["new_hire"], TODAY)
    assert out["stop_reason"] == "guardrail" and CountingJira.calls == 0
    assert out["answer"].startswith("Not run: INFRA is outside this assistant's scope (BILL, PAY)")
    assert [(r.guardrail, r.behavior) for r in out["guardrail_records"]] == [
        ("restricted_project", "allow"), ("deployment_scope", "reject_content")]


def test_raise_exception_halts_the_graph_even_for_a_call_the_bound_refuses(env):
    """The router's own check refuses SECOPS, but guardrails see every attempted call
    first, so the restricted attempt halts and is recorded instead of being politely refused."""
    _, users, _ = env
    with pytest.raises(ToolInputGuardrailTripwireTriggered) as err, scripted([count("SECOPS")]):
        ask(build(env, finance_assistant_policy()), "SECOPS incidents in Q3?", users["sec_engineer"], TODAY)
    assert CountingJira.calls == 0 and err.value.output.output_info["caller"] == "sec_engineer"


def test_an_allowed_count_records_every_verdict(env):
    _, users, _ = env
    with scripted([count("PAY", "payments", issue_type="Incident")]):
        out = ask(build(env, finance_assistant_policy()), "How many payments incidents in Q3?", users["new_hire"], TODAY)
    assert CountingJira.calls == 1 and out["result"].jql.startswith("project = PAY")
    assert [r.behavior for r in out["guardrail_records"]] == ["allow"] * 4


def test_the_router_must_be_chosen_explicitly(env):
    tool, _, pages = env
    with pytest.raises(TypeError):
        build_assistant(tool, pages)
