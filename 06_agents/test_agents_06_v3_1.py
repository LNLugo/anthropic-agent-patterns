"""Agents v3.1: v3 plus a final answer step built from the observations."""

from llm_provider import scripted
from agents_06_v3_1 import app

START = {"question": "Assess the platform.", "actions": [], "observations": [],
         "decision": "", "iteration": 0, "final_answer": ""}
ARCH = {"decision": "act", "tool": "architecture_analysis"}
RISK = {"decision": "act", "tool": "risk_analysis"}
FINISH = {"decision": "finish", "tool": ""}


def test_tools_then_a_final_answer(run_graph):
    with scripted([ARCH, RISK, FINISH, "Proceed in phases."]) as model:
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor", "agent", "tool_executor", "agent", "final_answer"]
    assert state["final_answer"] == "Proceed in phases."
    answer_prompt = model.calls[-1].prompt
    assert "Architecture analysis completed." in answer_prompt and "Risk analysis completed." in answer_prompt


def test_the_cap_still_produces_an_answer(run_graph):
    with scripted([ARCH, RISK, ARCH, RISK, "Answer from three tools."]):
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor"] * 3 + ["agent", "final_answer"]
    assert state["final_answer"] == "Answer from three tools."
