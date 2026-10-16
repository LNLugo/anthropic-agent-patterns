"""Memory in 08's assistant: follow-ups resolve, standing instructions act, and nothing
from memory widens what the coverage check accepts."""

import json
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agent_tools.assistant import build_assistant
from agent_tools.jira_mock import MockJira
from agent_tools.jql_tool import BoundedJiraTool
from agent_tools.model_router import ModelRouter
from llm_provider import scripted, scripted_model
from memory import InMemorySession
from memory.assistant import ask_with_memory

FIXTURE = Path(__file__).resolve().parents[2] / "08_tools" / "tests" / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}


def count(project, evidence):
    return AIMessage(content="", tool_calls=[{"name": "count_jira_issues", "id": "c1",
                                              "args": {"project": project, "project_evidence": evidence, **Q3}}])


@pytest.fixture
def env():
    jira, users = MockJira.from_fixture(FIXTURE)
    tool = BoundedJiraTool(jira)
    app = build_assistant(tool, lambda q, c, k: [], ModelRouter(scripted_model(), tool))
    return app, users["billing_oncall"]


def test_a_follow_up_is_answered_with_the_earlier_turn_in_view(env):
    app, who = env
    s = InMemorySession("s1", who.id)
    with scripted([count("BILL", "BILL")]):
        ask_with_memory(app, s, "How many BILL incidents in Q3?", who, TODAY)
    with scripted([count("PAY", "PAY")]) as model:
        out = ask_with_memory(app, s, "What about PAY?", who, TODAY)
    seen = [m.content for m in model.calls[0].messages]
    assert "How many BILL incidents in Q3?" in seen and seen[-1] == "What about PAY?"
    assert out["result"].jql.startswith("project = PAY")
    assert [i["role"] for i in s.get_items()] == ["user", "assistant", "user", "assistant"]


def test_a_pinned_instruction_is_the_users_own_words_and_counts_as_evidence(env):
    app, who = env
    s = InMemorySession("s1", who.id)
    with scripted([AIMessage(content="Noted.")]):
        ask_with_memory(app, s, "From now on, only count Payments.", who, TODAY)
    assert s.get_items()[0]["pinned"] is True
    with scripted([count("PAY", "Payments")]):
        out = ask_with_memory(app, s, "How many P1 incidents in Q3?", who, TODAY)
    assert out.get("stop_reason") is None and out["result"].jql.startswith("project = PAY")


def test_without_the_pin_the_same_words_are_not_evidence(env):
    app, who = env
    s = InMemorySession("s1", who.id)
    s.add_items([{"role": "user", "content": "I mostly look at Payments."}, {"role": "assistant", "content": "OK."}])
    with scripted([count("PAY", "Payments")]):
        out = ask_with_memory(app, s, "How many P1 incidents in Q3?", who, TODAY)
    assert out["stop_reason"] == "clarify" and "result" not in out


def test_a_project_named_earlier_is_not_evidence_for_a_new_question(env):
    """The half-answer the coverage check exists to stop must not come back through memory:
    'BILL and PAY' earlier, then 'what about last month?', must not become a BILL-only count."""
    app, who = env
    s = InMemorySession("s1", who.id)
    s.add_items([{"role": "user", "content": "How many bugs did BILL and PAY open in Q3?"},
                 {"role": "assistant", "content": "Which single project do you mean?"}])
    with scripted([count("BILL", "BILL")]):
        out = ask_with_memory(app, s, "And what about last month?", who, TODAY)
    assert out["stop_reason"] == "clarify" and "result" not in out


def test_someone_elses_session_is_refused_before_anything_runs(env):
    app, who = env
    with scripted([]) as model:
        with pytest.raises(PermissionError):
            ask_with_memory(app, InMemorySession("s1", "new_hire"), "How many BILL incidents?", who, TODAY)
    assert model.calls == ()


def test_history_reaches_the_model_but_not_the_trace(env, traces):
    app, who = env
    s = InMemorySession("s1", who.id)
    s.add_items([{"role": "user", "content": "Note for later: QX09-history-4c1d"},
                 {"role": "assistant", "content": "Noted."}])
    with scripted([count("BILL", "BILL")]) as model:
        ask_with_memory(app, s, "How many BILL incidents in Q3? QX09-history-4c1d", who, TODAY)
    assert any("QX09-history-4c1d" in m.content for m in model.calls[0].messages[:-1])
    assert "QX09-history-4c1d" not in json.dumps(traces.records)
    assert {sp["name"] for sp in traces.of_kind("tool")} >= {"memory.get_items", "memory.add_items"}


def test_a_turn_with_memory_is_one_trace(env, traces):
    app, who = env
    s = InMemorySession("s1", who.id)
    with scripted([count("BILL", "BILL")]):
        ask_with_memory(app, s, "How many BILL incidents in Q3?", who, TODAY)
    ended = [t for t in traces.traces if t["ended_at"]]
    assert len(ended) == 1 and ended[0]["group_id"] == "s1" and ended[0]["metadata"] == {"caller": who.id}
    assert {sp["trace_id"] for sp in traces.spans} == {ended[0]["trace_id"]}


def test_the_trace_names_which_turns_the_model_saw_by_position_only(env, traces):
    """Enough to rebuild the model's context from the session in an audit; no text."""
    app, who = env
    s = InMemorySession("s1", who.id)
    s.add_items([{"role": "user", "content": "From now on, only count BILL.", "pinned": True},  # as ask_with_memory saves it
                 {"role": "assistant", "content": "Understood: BILL only."}])
    with scripted([count("BILL", "BILL")]):
        ask_with_memory(app, s, "How many incidents in Q3?", who, TODAY)
    [span] = [sp for sp in traces.of_kind("custom") if sp["name"] == "memory.context"]
    shown = [i for i in s.get_items() if i["seq"] in span["data"]["seqs"]]
    assert [i["content"] for i in shown] == ["From now on, only count BILL.", "Understood: BILL only."]
    assert span["data"]["pinned"] == 1 and span["data"]["tokens"] > 0
    assert "only count BILL" not in json.dumps(traces.records)
