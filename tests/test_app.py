"""The pattern explorer runs a pattern only when Run is pressed, and every pattern runs.

Before Oct 2026 an indentation slip attached four patterns to the wrong `if`:
selecting Parallelization, Orchestrator-Workers, Evaluator-Optimizer or Agents
called the live model on every page refresh, and Run did nothing for them.
"""

import json
import os
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


# ---------------------------------------------------------------- Agents scenarios

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("demo_scenarios", Path(APP).parent / "demo_scenarios.py")
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)
AGENT_SCENARIOS = _demo.AGENT_SCENARIOS

REPLAYS = [name for name, s in AGENT_SCENARIOS.items() if "replay" in s]


def open_agents_scenario(name):
    at = open_page("Agents")
    at.selectbox[1].set_value(name).run()
    return at


def ended(at):
    return dict((m.label, m.value) for m in at.metric)["Why the run ended"]


@pytest.mark.parametrize("name", AGENT_SCENARIOS)
def test_selecting_an_agents_scenario_calls_no_model(name):
    at = open_agents_scenario(name)
    assert not at.exception
    assert scripted_model().calls == ()


@pytest.mark.parametrize("name", REPLAYS)
def test_replays_abstain_offline_with_no_api_key(name, monkeypatch):
    """A replay must run on a machine with no key: the page switches the module to the
    scripted model itself, and puts the provider setting back afterwards."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = open_agents_scenario(name)
    at.button[0].click().run()
    assert not at.exception
    assert ended(at) == "invalid decision"
    assert "No answer" in " ".join(m.value for m in at.markdown)
    assert len(scripted_model().calls) == len(AGENT_SCENARIOS[name]["replay"])  # every reply used
    assert os.environ["LLM_PROVIDER"] == "openai"


def test_a_scenario_turn_limit_reaches_the_graph():
    at = open_agents_scenario("Turn limit: too broad for one turn")
    with scripted([{"decision": "act", "tool": "risk_analysis"}, "Partial."]) as model:
        at.button[0].click().run()
    assert len(model.calls) == 2  # one decision, then the capped answer; no second decision
    assert ended(at) == "max turns"
    assert any("designed to: max turns" in c.value for c in at.caption)


def test_a_live_run_that_ends_otherwise_says_so():
    at = open_agents_scenario("Hands off: a model risk question")
    with scripted([{"decision": "finish"}, "Answer."]):
        at.button[0].click().run()
    assert ended(at) == "completed"
    assert any("designed to end handed off and ended completed" in c.value for c in at.caption)


def test_the_agents_page_shows_the_run_trace_without_content():
    at = open_agents_scenario("Abstains: answers instead of deciding")
    at.button[0].click().run()
    assert not at.exception
    [expander] = [e for e in at.expander if e.label.startswith("Trace:")]
    assert expander.label == "Trace: 2 spans"  # the decision, and the agent run around it
    table = at.dataframe[0].value
    assert list(table["kind"]) == ["agent", "generation"]
    assert "Azure" not in table.to_string()  # the model's prose reply is content
