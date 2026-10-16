"""Every run leaves a trace that matches what happened: one generation span per model
call, one tool span per Jira call, one guardrail span per verdict, and the approval
as a handoff to a person."""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: llm_provider
from llm_provider import Step, scripted, scripted_model  # noqa: E402

from agent_tools.assistant import ask, build_assistant, checkpoint_serde, resume  # noqa: E402
from agent_tools.comments import CommentStore, CommentTool  # noqa: E402
from agent_tools.guardrails import ToolInputGuardrailTripwireTriggered, finance_assistant_policy  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.model_router import ModelRouter  # noqa: E402
from agent_tools.tool_loop import ToolLoop  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}


class CountingJira(MockJira):
    calls = 0

    def search(self, caller, **kw):
        type(self).calls += 1
        return super().search(caller, **kw)


def call(name, call_id="c1", **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def count(project, evidence=None, call_id="c1"):
    return call("count_jira_issues", call_id, project=project, project_evidence=evidence or project, **Q3)


@pytest.fixture
def env():
    CountingJira.calls = 0
    jira, users = CountingJira.from_fixture(FIXTURE)
    return BoundedJiraTool(jira), users


def assistant(tool, guardrails=()):
    return build_assistant(tool, lambda q, c, k: [("ENG-3", "Billing API Credential Rotation")],
                           ModelRouter(scripted_model(), tool), guardrails=guardrails)


def verdicts(spans):
    return [(s["name"], s["data"]["stage"], s["data"]["behavior"]) for s in spans]


def test_a_count_reconciles_span_for_span(env, traces):
    tool, users = env
    with scripted([count("BILL")]) as model:
        out = ask(assistant(tool, finance_assistant_policy()), "How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    [trace_end] = [t for t in traces.traces if t["ended_at"]]
    assert trace_end["workflow"] == "assistant" and trace_end["status"] == "ok"
    assert trace_end["metadata"] == {"caller": "billing_oncall"}
    assert len(traces.of_kind("generation")) == len(model.calls) == 1
    assert len(traces.of_kind("tool")) == CountingJira.calls == 1
    assert traces.of_kind("tool")[0]["data"] == {"caller": "billing_oncall", "outcome": "ok", "truncated": False}
    recorded = [(r.guardrail, r.stage, r.behavior) for r in out["guardrail_records"]]
    assert verdicts(traces.of_kind("guardrail")) == recorded and len(recorded) == 4
    [agent] = traces.of_kind("agent")
    assert agent["data"]["ended"] == "answered" and agent["data"]["route"] == "aggregation"
    assert all(s["parent_id"] == agent["span_id"] for s in traces.spans if s is not agent)


def test_a_halt_is_in_the_trace_with_no_tool_span(env, traces):
    tool, users = env
    with scripted([count("SECOPS")]):
        with pytest.raises(ToolInputGuardrailTripwireTriggered):
            ask(assistant(tool, finance_assistant_policy()), "How many SECOPS issues in Q3?", users["sec_engineer"], TODAY)
    assert verdicts(traces.of_kind("guardrail")) == [("restricted_project", "input", "raise_exception")]
    assert traces.of_kind("guardrail")[0]["data"]["output_info"]["caller"] == "sec_engineer"
    assert traces.of_kind("tool") == [] and CountingJira.calls == 0
    assert traces.of_kind("agent")[0]["error"] == {"type": "ToolInputGuardrailTripwireTriggered"}
    assert traces.traces[-1]["status"] == "error"


def test_a_coached_retry_and_a_halt_look_different(env, traces):
    """reject_content tells the model why and gives it another turn; raise_exception stops.
    The trace records which, not just that a guardrail fired."""
    tool, users = env
    with scripted([count("INFRA"), count("BILL", call_id="c2"), "4 BILL issues."]) as model:
        result = ToolLoop(scripted_model(), tool, finance_assistant_policy()).run(
            "How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    assert result.status == "answered"
    gens = traces.of_kind("generation")
    assert len(gens) == len(model.calls) == 3 and [g["data"]["turn"] for g in gens] == [1, 2, 3]
    first = traces.of_kind("guardrail")[1]
    assert (first["name"], first["data"]["behavior"]) == ("deployment_scope", "reject_content")
    assert len(traces.of_kind("tool")) == CountingJira.calls == 1  # INFRA never reached Jira
    assert verdicts(traces.of_kind("guardrail")) == [(r.guardrail, r.stage, r.behavior) for r in result.records]
    assert traces.of_kind("agent")[0]["data"] == {"caller": "billing_oncall", "max_turns": 4, "status": "answered", "turns": 3}


def test_the_bound_refusing_a_call_is_a_guardrail_verdict_too(env, traces):
    tool, users = env
    long = call("count_jira_issues", project="BILL", project_evidence="BILL",
                created_from="2024-01-01", created_before="2026-10-01")
    with scripted([long, "Too long a range."]):
        result = ToolLoop(scripted_model(), tool).run("BILL since 2024?", users["billing_oncall"], TODAY)
    bound = [s for s in traces.of_kind("guardrail") if s["name"] == "tool_bound"]
    assert len(bound) == 1 and bound[0]["data"]["behavior"] == "reject_content"
    assert traces.of_kind("tool")[0]["data"]["outcome"] == "rejected" and CountingJira.calls == 0
    assert [r.guardrail for r in result.records] == ["tool_bound"]


def test_wiki_search_is_a_tool_span_with_page_ids_only(env, traces):
    tool, users = env
    with scripted([call("search_wiki", query="credential rotation")]):
        ask(assistant(tool), "How do we rotate the billing API credentials?", users["billing_oncall"], TODAY)
    [search] = traces.of_kind("tool")
    assert search["name"] == "search_wiki" and search["data"]["page_ids"] == ["ENG-3"]
    assert "Credential Rotation" not in str(traces.records)  # page titles are content


# ---------------------------------------------------------------- approval

@pytest.fixture
def writes(tmp_path):
    jira, users = MockJira.from_fixture(FIXTURE)
    store, db = CommentStore(), tmp_path / "checkpoints.db"

    def build():
        conn = sqlite3.connect(db, check_same_thread=False)
        tool, comments = BoundedJiraTool(jira), CommentTool(jira, store)
        return build_assistant(tool, lambda q, c, k: [], ModelRouter(scripted_model(), tool, comment_tool=comments),
                               comment_tool=comments, checkpointer=SqliteSaver(conn, serde=checkpoint_serde()))
    return build, users, store


def comment():
    return call("comment_on_issue", issue_key="BILL-101", body="Q3 P1 count: 4")


def test_an_approval_is_a_handoff_to_a_person_and_two_traces_in_one_group(writes, traces):
    build, users, store = writes
    with scripted([comment()]):
        ask(build(), "Add 'Q3 P1 count: 4' to BILL-101", users["billing_oncall"], TODAY, thread_id="req-1")
    [handoff] = traces.of_kind("handoff")
    assert handoff["data"] == {"from_agent": "assistant", "to_agent": "human_approver", "action": "comment_on_issue",
                               "issue_key": "BILL-101", "requested_by": "billing_oncall"}
    assert traces.of_kind("agent")[0]["data"]["ended"] == "pending_approval"
    assert traces.of_kind("tool") == []  # nothing posted yet

    resume(build(), "req-1", approved=True, approver="incident_lead")  # a restarted service
    ended = [t for t in traces.traces if t["ended_at"]]
    assert [(t["workflow"], t["group_id"]) for t in ended] == [("assistant", "req-1"), ("assistant.resume", "req-1")]
    [approval] = [s for s in traces.of_kind("custom") if s["name"] == "approval"]
    assert approval["data"]["decision"] == "approved" and approval["data"]["approver"] == "incident_lead"
    [post] = traces.of_kind("tool")
    assert post["name"] == "comment_on_issue" and post["data"]["outcome"] == "posted" and len(store.comments) == 1
    assert len(traces.of_kind("handoff")) == 1  # resuming re-runs the approve node; the handoff is not repeated


def test_a_self_approval_is_recorded_as_declined(writes, traces):
    build, users, store = writes
    with scripted([comment()]):
        ask(build(), "Add it to BILL-101", users["billing_oncall"], TODAY, thread_id="req-2")
    resume(build(), "req-2", approved=True, approver="billing_oncall")
    [approval] = [s for s in traces.of_kind("custom") if s["name"] == "approval"]
    assert approval["data"]["decision"] == "declined" and approval["data"]["self_approval"] is True
    assert traces.of_kind("tool") == [] and store.comments == []


# ---------------------------------------------------------------- a budget before the model call

class FixedBudget:
    """Stands in for 11_finops BudgetPolicy: allows the first `calls` checks, then refuses."""

    def __init__(self, calls):
        self.left, self.asked = calls, []

    def check(self, identity, model):
        self.asked.append((identity, model))
        if self.left > 0:
            self.left -= 1
            return "allow", "", {"left": self.left}
        return "reject_content", "Not run: today's model budget is used.", {"reason": "over_budget"}


def test_over_budget_the_model_is_never_called(env, traces):
    tool, users = env
    budget = FixedBudget(0)
    app = build_assistant(tool, lambda q, c, k: [], ModelRouter(scripted_model(), tool), budget=budget)
    with scripted([]) as model:  # any model call would fail: nothing is scripted
        out = ask(app, "How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    assert model.calls == () and CountingJira.calls == 0
    assert out["stop_reason"] == "budget" and out["answer"] == "Not run: today's model budget is used."
    assert budget.asked == [("billing_oncall", "ScriptedChatModel")]
    [verdict] = traces.of_kind("guardrail")
    assert (verdict["name"], verdict["data"]["stage"], verdict["data"]["behavior"]) == ("model_budget", "model", "reject_content")
    assert [(r.guardrail, r.behavior) for r in out["guardrail_records"]] == [("model_budget", "reject_content")]


def test_within_budget_the_verdict_is_kept_with_the_others(env, traces):
    tool, users = env
    app = build_assistant(tool, lambda q, c, k: [], ModelRouter(scripted_model(), tool),
                          guardrails=finance_assistant_policy(), budget=FixedBudget(1))
    with scripted([count("BILL")]):
        out = ask(app, "How many BILL issues in Q3?", users["billing_oncall"], TODAY)
    names = [r.guardrail for r in out["guardrail_records"]]
    assert names[0] == "model_budget" and len(names) == 5   # the budget, then the four tool guardrails
    assert verdicts(traces.of_kind("guardrail")) == [(r.guardrail, r.stage, r.behavior) for r in out["guardrail_records"]]


def test_the_rules_router_calls_no_model_so_no_budget_is_asked(env):
    from agent_tools.router import RulesRouter

    tool, users = env
    budget = FixedBudget(0)
    out = ask(build_assistant(tool, lambda q, c, k: [], RulesRouter(), budget=budget),
              "How many P1 incidents in BILL last quarter?", users["billing_oncall"], TODAY)
    assert budget.asked == [] and out.get("stop_reason") != "budget"


def test_a_budget_stops_a_looping_model_mid_run(env, traces):
    """The model keeps asking for an out-of-scope project; the budget allows two turns."""
    tool, users = env
    loop = ToolLoop(scripted_model(), tool, finance_assistant_policy(), budget=FixedBudget(2))
    with scripted([count("INFRA"), count("INFRA", call_id="c2")]) as model:
        result = loop.run("How many INFRA issues in Q3?", users["billing_oncall"], TODAY)
    assert result.status == "budget" and result.turns == 3
    assert len(model.calls) == 2 and len(traces.of_kind("generation")) == 2
