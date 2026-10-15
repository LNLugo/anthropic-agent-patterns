"""Routing: one classification call, then exactly one specialised branch."""

import pytest

from llm_provider import scripted
from routing_02 import app

TEXT = "Microsoft announced a new enterprise AI platform today."


@pytest.mark.parametrize("label, branch", [
    ("News", "news"),
    ("Research.", "research"),
    ("Category: Blog", "blog"),
    ("Opinion piece", "other"),
])
def test_classification_selects_one_branch(run_graph, label, branch):
    with scripted([label, f"{branch} analysis"]) as model:
        path, state = run_graph(app, {"text": TEXT})
    assert path == ["classification_node", branch]
    assert state["selected_path"] == branch and state["response"] == f"{branch} analysis"
    assert len(model.calls) == 2  # one classification, one branch: no other branch ran


def test_substring_routing_picks_the_first_keyword_it_checks(run_graph):
    """A known weakness of free text routing, kept visible: 'not news, it is research'
    goes to the news branch because 'news' is checked first. routing_02_v2 fixes this
    with a constrained structured output."""
    with scripted(["Not news, it is research", "news analysis"]):
        path, _ = run_graph(app, {"text": TEXT})
    assert path == ["classification_node", "news"]
