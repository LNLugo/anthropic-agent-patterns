"""Evaluator-optimizer: generate, evaluate, refine until the score passes or the loop cap stops it."""

from llm_provider import scripted
from evaluator_optimizer_05 import app

START = {"question": "Why does an enterprise need an AI governance framework?"}


def score(n):
    return {"score": n, "feedback": f"scored {n}"}


def test_a_passing_first_draft_is_evaluated_once(run_graph):
    with scripted(["Draft one.", score(9)]):
        path, state = run_graph(app, START)
    assert path == ["generator", "evaluator"]
    assert state["draft"] == "Draft one." and state["score"] == 9


def test_feedback_drives_one_refinement(run_graph):
    with scripted(["Draft one.", score(5), "Draft two.", score(8)]) as model:
        path, state = run_graph(app, START)
    assert path == ["generator", "evaluator", "optimizer", "evaluator"]
    assert state["draft"] == "Draft two." and state["iteration"] == 2
    assert "scored 5" in model.calls[2].prompt  # the optimizer was given the evaluator's feedback


def test_the_loop_cap_ends_the_run_with_a_failing_score(run_graph):
    """Three drafts, none passing: the cap stops the loop and the last draft is
    returned with its failing score. Nothing in the state marks that the cap,
    not the evaluator, ended the run."""
    with scripted(["D1", score(4), "D2", score(5), "D3", score(6)]):
        path, state = run_graph(app, START)
    assert path == ["generator", "evaluator", "optimizer", "evaluator", "optimizer", "evaluator"]
    assert state["draft"] == "D3" and state["score"] == 6 and state["iteration"] == 3
