from datetime import date
from pathlib import Path

import pytest

from agent_tools.assistant import ask, build_assistant
from agent_tools.jira_mock import MockJira
from agent_tools.jql_tool import BoundedJiraTool

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
HEADLINE = "How many P1 incidents did the billing platform have last quarter?"


class StubKnowledge:
    """Stands in for 07_rag's permission-filtered retriever and records its calls."""

    def __init__(self, pages=(("ENG-3", "Billing API Credential Rotation"),)):
        self.pages, self.calls = list(pages), []

    def __call__(self, question, caller, k):
        self.calls.append((question, caller.id))
        return self.pages[:k]


@pytest.fixture
def setup():
    jira, users = MockJira.from_fixture(FIXTURE)
    knowledge = StubKnowledge()
    return build_assistant(BoundedJiraTool(jira), knowledge), users, knowledge


def test_headline_question_counts_what_the_user_can_see(setup):
    app, users, knowledge = setup
    oncall = ask(app, HEADLINE, users["billing_oncall"], TODAY)
    new_hire = ask(app, HEADLINE, users["new_hire"], TODAY)
    assert oncall["route"] == "aggregation" and oncall["result"].count == 4
    assert new_hire["result"].count == 3
    assert new_hire["answer"].startswith("3 P1 incidents in BILL created 2026-07-01 to 2026-09-30.")
    assert "BILL-107" not in new_hire["answer"]
    assert "only issues you can see" in new_hire["answer"]
    assert 'JQL: project = BILL AND issuetype = Incident AND priority = P1' in new_hire["answer"]
    assert knowledge.calls == []  # the retriever was never involved


def test_knowledge_question_goes_to_retrieval(setup):
    app, users, knowledge = setup
    out = ask(app, "How do I rotate the billing API credentials?", users["billing_oncall"], TODAY)
    assert out["route"] == "knowledge"
    assert knowledge.calls == [("How do I rotate the billing API credentials?", "billing_oncall")]
    assert "result" not in out
    assert "Billing API Credential Rotation" in out["answer"]


def test_knowledge_with_no_pages_abstains(setup):
    _, users, _ = setup
    jira, _ = MockJira.from_fixture(FIXTURE)
    app = build_assistant(BoundedJiraTool(jira), StubKnowledge(pages=()))
    out = ask(app, "How do I rotate the billing API credentials?", users["new_hire"], TODAY)
    assert out["answer"].startswith("Not in the wiki")


def test_missing_project_asks_a_question_and_runs_nothing(setup):
    app, users, _ = setup
    out = ask(app, "How many P1 incidents last quarter?", users["new_hire"], TODAY)
    assert out["stop_reason"] == "clarify" and "result" not in out
    assert out["answer"].startswith("Which project")


def test_project_outside_the_bound_is_refused(setup):
    app, users, _ = setup
    out = ask(app, "How many incidents did security operations have last quarter?", users["sec_engineer"], TODAY)
    assert out["stop_reason"] == "rejected" and "result" not in out
    assert "not available" in out["answer"]


def test_assumptions_are_stated_in_the_answer(setup):
    app, users, _ = setup
    out = ask(app, "Show me the open P2s in payments", users["new_hire"], TODAY)
    assert "last 90 days" in out["answer"] and "P2 incidents" in out["answer"]


def test_user_with_no_access_gets_zero_not_an_error(setup):
    app, users, _ = setup
    out = ask(app, HEADLINE, users["contractor"], TODAY)
    assert out["result"].count == 0 and out["answer"].startswith("0 P1 incidents")
