"""Offline tests for the model router. A scripted fake stands in for the chat
model, so these check the plumbing and the bound, not a model's accuracy;
run_model_router_eval.py measures that against a real model."""

import json
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agent_tools.evaluate import evaluate_model
from agent_tools.jira_mock import MockJira
from agent_tools.jql_tool import BoundedJiraTool
from agent_tools.model_router import ModelRouter, system_prompt

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 12)


class ScriptedModel:
    """Answers each question with a pre-set tool call, like a chat model bound to tools."""

    def __init__(self, script: dict[str, tuple[str, dict] | None]):
        self.script, self.bound_tools, self.tool_choice, self.prompts = script, None, None, []

    def bind_tools(self, tools, tool_choice=None):
        self.bound_tools, self.tool_choice = tools, tool_choice
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        question = messages[-1][1]
        call = self.script.get(question)
        tool_calls = [] if call is None else [{"name": call[0], "args": call[1], "id": "call_1"}]
        return AIMessage(content="", tool_calls=tool_calls,
                         usage_metadata={"input_tokens": 400, "output_tokens": 30, "total_tokens": 430})


@pytest.fixture
def tool():
    jira, _ = MockJira.from_fixture(FIXTURES / "mini_jira.json")
    return BoundedJiraTool(jira)


def count(**args):
    return ("count_jira_issues", args)


def test_binds_three_tools_and_forces_a_call(tool):
    model = ScriptedModel({})
    ModelRouter(model, tool, coverage=False)
    names = [t["function"]["name"] for t in model.bound_tools]
    assert names == ["count_jira_issues", "search_wiki", "ask_clarification"]
    assert model.tool_choice == "any"
    # The model sees only allowlisted projects; SECOPS is not offered.
    assert model.bound_tools[0]["function"]["parameters"]["properties"]["project"]["enum"] == ["BILL", "INFRA", "PAY"]


def test_tool_choice_is_the_route(tool):
    model = ScriptedModel({
        "q1": count(project="BILL", created_from="2026-07-01", created_before="2026-10-01"),
        "q2": ("search_wiki", {"query": "rotate credentials"}),
        "q3": ("ask_clarification", {"question": "Which project?"}),
        "q4": None,
    })
    r = ModelRouter(model, tool, coverage=False)
    assert [r.decide(q, TODAY).route for q in ("q1", "q2", "q3", "q4")] == ["aggregation", "knowledge", "clarify", "none"]


def test_arguments_become_a_validated_spec(tool):
    model = ScriptedModel({"q": count(project="PAY", created_from="2026-09-01", created_before="2026-10-01",
                                      issue_type="Incident", priority="P1")})
    d = ModelRouter(model, tool, coverage=False).decide("q", TODAY)
    assert (d.spec.project, d.spec.priority, d.spec.created_before) == ("PAY", "P1", date(2026, 10, 1))
    assert (d.input_tokens, d.output_tokens) == (400, 30)


@pytest.mark.parametrize("args, reason", [
    ({"project": "SECOPS", "created_from": "2026-07-01", "created_before": "2026-10-01"}, "not available"),
    ({"project": "BILL", "created_from": "2026-07-01", "created_before": "2026-10-01",
      "jql": "project = BILL OR project = SECOPS"}, "unknown arguments"),
    ({"project": "BILL", "created_from": "2024-01-01", "created_before": "2026-10-01"}, "capped"),
    ({"project": "BILL", "created_from": "2026-07-01", "created_before": "2026-10-01", "priority": "P0"}, "not one of"),
])
def test_model_arguments_outside_the_bound_are_rejected(tool, args, reason):
    """Whatever the model sends, the same validator as the rules path decides."""
    d = ModelRouter(ScriptedModel({"q": ("count_jira_issues", args)}), tool, coverage=False).decide("q", TODAY)
    assert d.route == "aggregation" and d.spec is None and reason in d.rejected


def test_prompt_states_the_tool_contract():
    p = system_prompt(TODAY)
    assert "Today is 2026-10-12" in p and "exclusive" in p and "created_from 2026-07-15" in p


def test_perfect_model_scores_full_marks(tool):
    """A model that returns every labeled answer scores 25/25 and 12/12, so the
    scoring itself cannot be what holds a real model back."""
    raw = json.loads((FIXTURES / "router_questions.json").read_text(encoding="utf-8"))
    script = {}
    for item in raw["questions"]:
        spec = item.get("spec")
        if item["route"] == "knowledge":
            script[item["q"]] = ("search_wiki", {"query": item["q"]})
        elif spec is None:  # labeled aggregation with no spec: any counting call routes correctly
            script[item["q"]] = count(project="BILL", created_from="2026-07-15", created_before="2026-10-13")
        else:
            script[item["q"]] = count(**{k: v for k, v in spec.items() if v is not None})
    report = evaluate_model(FIXTURES / "router_questions.json", ModelRouter(ScriptedModel(script), tool, coverage=False))
    assert (report.routed_correctly, report.total) == (25, 25)
    assert (report.extracted_correctly, report.extraction_total) == (12, 12)
    assert report.input_tokens == 25 * 400 and not report.rejected_by_bound


def test_misses_and_rejections_are_reported(tool):
    q_count = "How many P1 incidents did the billing platform have last quarter?"
    q_wiki = "How do I rotate the billing API credentials?"
    script = {
        q_count: count(project="BILL", created_from="2026-07-01", created_before="2026-10-01", issue_type="Incident"),
        q_wiki: count(project="SECOPS", created_from="2026-07-01", created_before="2026-10-01"),
    }
    report = evaluate_model(FIXTURES / "router_questions.json", ModelRouter(ScriptedModel(script), tool, coverage=False))
    misses = dict(report.extraction_misses)
    assert "priority: expected 'P1', got None" in misses[q_count]
    assert (q_wiki, "knowledge", "aggregation") in report.misroutes
    assert report.rejected_by_bound[0][0] == q_wiki
    assert report.cost_usd(0.15, 0.60) == pytest.approx((25 * 400 * 0.15 + 25 * 30 * 0.60) / 1e6)


# ---------------------------------------------------------------- no_query label and held-out files

def write_set(tmp_path, questions):
    p = tmp_path / "set.json"
    p.write_text(json.dumps({"today": "2026-10-12", "questions": questions}), encoding="utf-8")
    return p


NO_PROJECT = "How many P1 incidents last month?"
OFF_LIST = "How many incidents did security operations have last quarter?"
TWO_PROJECTS = "How many incidents did billing and payments have last month?"


def test_rules_score_asking_and_refusing_as_no_query(tmp_path):
    from agent_tools.evaluate import evaluate
    path = write_set(tmp_path, [{"q": NO_PROJECT, "route": "no_query"}, {"q": OFF_LIST, "route": "no_query"}])
    report = evaluate(path)
    assert report.routed_correctly == 2, report.misroutes   # one asks, one is refused by validate()


def test_model_scores_clarify_or_rejection_as_no_query(tool, tmp_path):
    path = write_set(tmp_path, [
        {"q": NO_PROJECT, "route": "no_query"},
        {"q": OFF_LIST, "route": "no_query"},
        {"q": TWO_PROJECTS, "route": "no_query"},
    ])
    script = {
        NO_PROJECT: ("ask_clarification", {"question": "Which project?"}),
        OFF_LIST: count(project="SECOPS", created_from="2026-07-01", created_before="2026-10-01"),
        TWO_PROJECTS: count(project="BILL", created_from="2026-09-01", created_before="2026-10-01"),  # ran a query: wrong
    }
    report = evaluate_model(path, ModelRouter(ScriptedModel(script), tool, coverage=False))
    assert report.routed_correctly == 2
    assert report.misroutes == ((TWO_PROJECTS, "no_query", "aggregation"),)


def test_template_placeholders_are_refused(tool, tmp_path):
    path = write_set(tmp_path, [{"q": "REPLACE: a counting question", "route": "aggregation"}])
    with pytest.raises(ValueError, match="REPLACE"):
        evaluate_model(path, ModelRouter(ScriptedModel({}), tool, coverage=False))


def test_unknown_route_label_is_refused(tool, tmp_path):
    path = write_set(tmp_path, [{"q": "How many bugs in billing?", "route": "count"}])
    with pytest.raises(ValueError, match="unknown route"):
        evaluate_model(path, ModelRouter(ScriptedModel({}), tool, coverage=False))
