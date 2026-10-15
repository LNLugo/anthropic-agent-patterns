"""The pattern explorer runs a pattern only when Run is pressed, and every pattern runs.

Before Oct 2026 an indentation slip attached four patterns to the wrong `if`:
selecting Parallelization, Orchestrator-Workers, Evaluator-Optimizer or Agents
called the live model on every page refresh, and Run did nothing for them.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from llm_provider import Step, scripted, scripted_model  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app" / "Agent_Patterns.py")
TASKS = ["identity", "hosting", "cost"]
RUNS = {
    "Prompt Chaining": ["Blog", "A, B", "Summary."],
    "Routing": ["News", "news analysis"],
    "Routing V2": [{"route": "news"}, "news analysis"],
    "Parallelization": [Step("t", match="from a technical"), Step("b", match="from a business"),
                        Step("r", match="from a risk"), Step("s", match="Synthesize the following three")],
    "Orchestrator-Workers": [json.dumps({"tasks": TASKS})]
        + [Step(f"notes {t}", match=f"workstream is:\n\n{t}") for t in TASKS] + ["plan"],
    "Evaluator-Optimizer": ["Draft.", {"score": 9, "feedback": "ok"}],
    "Agents": [{"decision": "act", "tool": "risk_analysis"}, {"decision": "finish"}, "Answer."],
}


def open_page(pattern):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.selectbox[0].set_value(pattern).run()
    return at


@pytest.mark.parametrize("pattern", RUNS)
def test_selecting_a_pattern_calls_no_model(pattern):
    at = open_page(pattern)
    assert not at.exception
    assert scripted_model().calls == ()


@pytest.mark.parametrize("pattern", RUNS)
def test_run_executes_the_selected_pattern(pattern):
    at = open_page(pattern)
    with scripted(RUNS[pattern]) as model:  # unused or missing steps fail the test
        at.button[0].click().run()
    assert not at.exception and model.calls


@pytest.mark.parametrize("steps, status", [
    ([{"decision": "act", "tool": "risk_analysis"}] * 3 + ["Partial."], "max turns"),
    ([{"decision": "handoff", "to": "risk_specialist"}], "handed off"),
    (["not json"], "invalid decision"),
])
def test_agents_page_shows_why_the_run_ended(steps, status):
    at = open_page("Agents")
    with scripted(steps):
        at.button[0].click().run()
    assert not at.exception
    assert ("Why the run ended", status) in [(m.label, m.value) for m in at.metric]
