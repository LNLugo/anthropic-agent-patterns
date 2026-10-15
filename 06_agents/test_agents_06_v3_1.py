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
