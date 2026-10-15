"""Orchestrator-workers v1: the orchestrator decides the tasks; one node works them in turn."""

import json

import pytest

from llm_provider import scripted
from orchestrator_workers_04 import app

TEXT = "Design an AI platform for 10,000 employees."
TASKS = ["identity and access", "model hosting", "cost controls"]


def test_tasks_come_from_the_orchestrator_and_each_is_worked(run_graph):
    fenced = "```json\n" + json.dumps({"tasks": TASKS}) + "\n```"  # fences are stripped
    with scripted([fenced, "IAM notes", "hosting notes", "cost notes", "Final plan."]) as model:
        path, state = run_graph(app, {"text": TEXT})
    assert path == ["orchestrator", "workers", "synthesis"]
    assert state["tasks"] == TASKS and len(state["worker_results"]) == 3
    for task, call in zip(TASKS, model.calls[1:4]):
        assert task in call.prompt
    assert all(f"WORKSTREAM: {t}" in model.calls[4].prompt for t in TASKS)


def test_a_non_json_plan_stops_before_any_worker_runs(run_graph):
    with pytest.raises(json.JSONDecodeError), scripted(["I would look at security first."]) as model:
        run_graph(app, {"text": TEXT})
    assert len(model.calls) == 1
