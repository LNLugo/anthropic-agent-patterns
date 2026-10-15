"""Agents v3: the model loops over tools until it finishes; the run ends at the agent."""

from llm_provider import scripted
from agents_06_v3 import app

START = {"question": "Assess the platform.", "actions": [], "observations": [],
         "decision": "", "iteration": 0, "final_answer": ""}
ARCH = {"decision": "act", "tool": "architecture_analysis"}
RISK = {"decision": "act", "tool": "risk_analysis"}
FINISH = {"decision": "finish", "tool": ""}


def test_finishing_immediately_runs_no_tool(run_graph):
    with scripted([FINISH]):
        path, state = run_graph(app, START)
    assert path == ["agent"] and state["actions"] == []


def test_tools_run_in_the_order_the_model_chooses(run_graph):
    with scripted([RISK, ARCH, FINISH]):
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor", "agent", "tool_executor", "agent"]
    assert state["actions"] == ["risk_analysis", "architecture_analysis"]
    assert state["observations"][0].startswith("Risk analysis completed.")
    assert state["final_answer"] == ""  # v3 has no answer step; v3.1 adds it


def test_the_cap_ends_the_loop_after_three_tools(run_graph):
    with scripted([ARCH, RISK, ARCH, RISK]):
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor"] * 3 + ["agent"]
    assert state["iteration"] == 3 and len(state["actions"]) == 4  # the 4th choice is recorded, not run
