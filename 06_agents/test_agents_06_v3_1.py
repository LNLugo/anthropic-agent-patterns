"""Agents v3.1: every turn ends in one typed outcome, and every run says why it ended."""

import pytest

from llm_provider import scripted
from agents_06_v3_1 import (
    FinalOutput, Handoff, Interruption, MAX_TURNS, RunAgain, app, parse_outcome,
)

START = {"question": "Assess the platform.", "actions": [], "observations": [],
         "iteration": 0, "final_answer": ""}
ARCH = {"decision": "act", "tool": "architecture_analysis"}
RISK = {"decision": "act", "tool": "risk_analysis"}
FINISH = {"decision": "finish"}


# ---------------------------------------------------------------- parsing the model's reply

@pytest.mark.parametrize("reply, outcome", [
    ('{"decision": "finish"}', FinalOutput()),
    ('{"decision": "act", "tool": "risk_analysis"}', RunAgain("risk_analysis")),
    ('```json\n{"decision": "handoff", "to": "risk_specialist"}\n```', Handoff("risk_specialist")),
])
def test_valid_replies_map_to_one_outcome(reply, outcome):
    assert parse_outcome(reply) == outcome


@pytest.mark.parametrize("reply, detail", [
    ('{"decision": "act", "tool": "delete_everything"}', "unknown tool"),
    ('{"decision": "handoff", "to": "anyone"}', "unknown handoff target"),
    ('{"decision": "maybe"}', "unknown decision"),
    ("I think we should look at risk next.", "not JSON"),
    ("[1, 2]", "not a JSON object"),
])
def test_anything_else_is_an_interruption_not_a_guess(reply, detail):
    outcome = parse_outcome(reply)
    assert isinstance(outcome, Interruption) and outcome.reason == "invalid_decision"
    assert detail in outcome.detail


# ---------------------------------------------------------------- runs

def test_tools_then_a_final_answer(run_graph):
    with scripted([ARCH, RISK, FINISH, "Proceed in phases."]) as model:
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor", "agent", "tool_executor", "agent", "final_answer"]
    assert state["status"] == "completed" and state["outcome"] == FinalOutput()
    assert state["final_answer"] == "Proceed in phases."
    answer_prompt = model.calls[-1].prompt
    assert "Architecture analysis completed." in answer_prompt and "Risk analysis completed." in answer_prompt
    assert "turn limit" not in answer_prompt


def test_the_turn_limit_is_an_outcome_and_costs_no_model_call(run_graph):
    """At the limit the agent stops before asking the model. Before this change the
    model was asked a fourth time and its answer silently overruled."""
    with scripted([ARCH, RISK, ARCH, "Partial answer."]) as model:
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor"] * MAX_TURNS + ["agent", "final_answer"]
    assert state["status"] == "max_turns"
    assert state["outcome"] == Interruption("max_turns", f"{MAX_TURNS} of {MAX_TURNS} turns used")
    assert len(model.calls) == MAX_TURNS + 1  # three decisions and the answer; no wasted decision
    assert "turn limit" in model.calls[-1].prompt  # the answer is told it is capped


def test_the_limit_can_be_set_per_run(run_graph):
    with scripted([ARCH, "Answer after one tool."]):
        path, state = run_graph(app, {**START, "max_turns": 1})
    assert path == ["agent", "tool_executor", "agent", "final_answer"]
    assert state["status"] == "max_turns"


def test_a_handoff_ends_the_run_with_its_target(run_graph):
    with scripted([RISK, {"decision": "handoff", "to": "risk_specialist"}]):
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor", "agent", "handoff"]
    assert state["status"] == "handed_off" and state["handoff_to"] == "risk_specialist"
    assert state["final_answer"] == ""  # this agent did not answer; the specialist will


def test_an_invalid_decision_stops_without_an_answer(run_graph):
    with scripted([ARCH, {"decision": "act", "tool": "delete_everything"}]) as model:
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor", "agent", "stop"]
    assert state["status"] == "invalid_decision" and state["final_answer"] == ""
    assert state["actions"] == ["architecture_analysis"]  # the unknown tool was never recorded or run
    assert len(model.calls) == 2


def test_every_run_ends_with_a_status(run_graph):
    scripts = [
        [FINISH, "Answer."],
        [ARCH, RISK, ARCH, "Capped."],
        [{"decision": "handoff", "to": "risk_specialist"}],
        ["not json"],
    ]
    statuses = []
    for script in scripts:
        with scripted(script):
            statuses.append(run_graph(app, START)[1]["status"])
    assert statuses == ["completed", "max_turns", "handed_off", "invalid_decision"]


# ---------------------------------------------------------------- traces

from agents_06_v3_1 import run  # noqa: E402


def test_a_run_is_one_trace_that_reconciles_with_the_model_calls(traces):
    with scripted([ARCH, RISK, FINISH, "Proceed in phases."]) as model:
        state = run("Assess the platform.")
    assert state["status"] == "completed"
    gens = traces.of_kind("generation")
    assert len(gens) == len(model.calls) == 4
    assert [g["name"] for g in gens] == ["decide", "decide", "decide", "final_answer"]
    assert [g["data"]["outcome"] for g in gens[:3]] == ["RunAgain", "RunAgain", "FinalOutput"]
    assert [t["name"] for t in traces.of_kind("tool")] == ["architecture_analysis", "risk_analysis"]
    [agent] = traces.of_kind("agent")
    assert agent["data"] == {"max_turns": MAX_TURNS, "status": "completed", "turns": 2}


def test_a_handoff_is_a_handoff_span_and_no_answer_is_generated(traces):
    with scripted([{"decision": "handoff", "to": "risk_specialist"}]):
        run("Is our credit model SR 11-7 compliant?")
    [handoff] = traces.of_kind("handoff")
    assert handoff["data"] == {"from_agent": "agent", "to_agent": "risk_specialist"}
    assert [g["name"] for g in traces.of_kind("generation")] == ["decide"]


def test_the_turn_limit_shows_as_no_decision_spent(traces):
    with scripted([ARCH, "Partial."]):
        run("Assess everything.", max_turns=1)
    assert [g["name"] for g in traces.of_kind("generation")] == ["decide", "final_answer"]
    assert traces.of_kind("generation")[1]["data"]["capped"] is True
    assert traces.of_kind("agent")[0]["data"]["status"] == "max_turns"


def test_an_invalid_decision_keeps_the_bad_reply_out_of_the_trace(traces):
    with scripted(["Go with Azure, everyone knows it."]):
        state = run("Which cloud?")
    assert state["status"] == "invalid_decision"
    assert traces.of_kind("generation")[0]["data"]["outcome"] == "Interruption"
    assert "Azure" not in str(traces.records)
