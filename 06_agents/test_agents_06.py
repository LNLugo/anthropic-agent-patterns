"""Agents v1: the model decides act or finish each turn; an iteration cap bounds the loop."""

from llm_provider import scripted
from agents_06 import app

START = {"question": "Should we build an AI platform for 10,000 employees?", "actions": [],
         "observation": "", "decision": "", "iteration": 0, "final_answer": ""}


def act(action):
    return {"decision": "act", "action": action}


FINISH = {"decision": "finish", "action": "Provide the final recommendation."}


def test_acts_until_the_model_finishes(run_graph):
    with scripted([act("Assess risks"), "Risks: access control.",
                   act("Assess cost"), "Cost: high.", FINISH, "Build in phases."]):
        path, state = run_graph(app, START)
    assert path == ["agent", "action", "agent", "action", "agent", "final_answer"]
    assert state["iteration"] == 2 and state["final_answer"] == "Build in phases."


def test_the_cap_overrides_the_model(run_graph):
    """After three actions the router finishes even though the model asked to act again.
    That fourth decision is recorded and silently ignored."""
    script = []
    for i in range(3):
        script += [act(f"step {i}"), f"observation {i}"]
    script += [act("step 3"), "Answer from three observations."]
    with scripted(script):
        path, state = run_graph(app, START)
    assert path == ["agent", "action"] * 3 + ["agent", "final_answer"]
    assert state["decision"] == "act" and state["iteration"] == 3
