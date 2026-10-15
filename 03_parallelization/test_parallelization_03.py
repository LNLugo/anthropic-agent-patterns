"""Parallelization: three independent analyses fan out, then one synthesis fans in."""

from llm_provider import Step, scripted
from parallelization_03 import app

TEXT = "A bank plans a RAG assistant over policy documents for 10,000 staff."
BRANCHES = {"technical_analysis", "business_analysis", "risk_analysis"}


def script():
    return [
        Step("TECH: hybrid retrieval", match="from a technical"),
        Step("BIZ: faster answers", match="from a business"),
        Step("RISK: access control", match="from a risk"),
        Step("Proceed with a pilot.", match="Synthesize the following three"),
    ]


def test_three_branches_run_then_one_synthesis(run_graph):
    with scripted(script()):
        path, state = run_graph(app, {"text": TEXT})
    assert set(path[:3]) == BRANCHES and len(path) == 4
    assert path[3] == "synthesis"
    assert state["synthesis"] == "Proceed with a pilot."
    assert all(state[f"{b.split('_')[0]}_duration"] >= 0 for b in BRANCHES)


def test_synthesis_sees_every_branch(run_graph):
    with scripted(script()) as model:
        run_graph(app, {"text": TEXT})
    synthesis_prompt = next(c.prompt for c in model.calls if "Synthesize" in c.prompt)
    for finding in ("TECH: hybrid retrieval", "BIZ: faster answers", "RISK: access control"):
        assert finding in synthesis_prompt
