"""Routing v2: the route is a constrained structured output, not free text."""

import pytest
from pydantic import ValidationError

from llm_provider import scripted
from routing_02_v2 import RouteDecision, app

TEXT = "A new study shows retrieval augmented generation improves accuracy."


@pytest.mark.parametrize("route", ["news", "research", "blog", "other"])
def test_structured_route_selects_one_branch(run_graph, route):
    with scripted([{"route": route}, f"{route} analysis"]) as model:
        path, state = run_graph(app, {"text": TEXT})
    assert path == ["classification_node", route]
    assert state["selected_path"] == route
    assert model.calls[0].schema == "RouteDecision"


def test_a_route_outside_the_schema_never_reaches_a_branch(run_graph):
    with pytest.raises(ValidationError), scripted([{"route": "finance"}]) as model:
        run_graph(app, {"text": TEXT})
    assert len(model.calls) == 1  # failed at classification; no branch was called


def test_the_ambiguous_label_that_misroutes_v1_cannot_be_expressed():
    with pytest.raises(ValidationError):
        RouteDecision(route="Not news, it is research")
