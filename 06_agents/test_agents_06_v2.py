"""Agents v2: the model selects one tool; a deterministic tool runs; the run ends."""

import pytest

from llm_provider import scripted
from agents_06_v2 import app

START = {"question": "Assess the platform.", "actions": [], "observation": "",
         "iteration": 0, "final_answer": ""}


@pytest.mark.parametrize("tool, expect", [
    ("architecture_analysis", "Architecture analysis tool executed."),
    ("risk_analysis", "Risk analysis tool executed."),
])
def test_the_selected_tool_runs_once(run_graph, tool, expect):
    with scripted([{"tool": tool}]) as model:
        path, state = run_graph(app, START)
    assert path == ["agent", "tool_executor"]
    assert state["actions"] == [tool] and state["observation"].startswith(expect)
    assert len(model.calls) == 1  # the tool itself is code, not a model call


def test_an_unknown_tool_is_reported_not_run(run_graph):
    with scripted([{"tool": "delete_everything"}]):
        _, state = run_graph(app, START)
    assert state["observation"] == "Unknown tool selected: delete_everything"
