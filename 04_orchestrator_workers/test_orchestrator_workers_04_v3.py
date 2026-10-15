"""Orchestrator-workers v3: one parallel worker per task through Send, results reduced into one list."""

import json

import pytest

from llm_provider import Step, scripted
from orchestrator_workers_04_v3 import app

TEXT = "Design an AI platform for 10,000 employees."


def script(tasks):
    return [json.dumps({"tasks": tasks})] + [
        Step(f"notes on {t}", match=f"workstream is:\n\n{t}") for t in tasks
    ] + ["Final plan."]


@pytest.mark.parametrize("tasks", [
    ["identity and access", "model hosting", "cost controls"],
    ["data residency", "evaluation", "rollout", "support", "budget"],
])
def test_one_worker_per_task(run_graph, tasks):
    with scripted(script(tasks)) as model:
        path, state = run_graph(app, {"text": TEXT})
    assert path == ["orchestrator"] + ["worker"] * len(tasks) + ["synthesis"]
    assert sorted(state["worker_results"]) == sorted(
        f"WORKSTREAM: {t}\nASSESSMENT:\nnotes on {t}" for t in tasks
    )
    assert all(f"WORKSTREAM: {t}" in model.calls[-1].prompt for t in tasks)


def test_each_worker_sees_only_its_own_task(run_graph):
    tasks = ["identity and access", "model hosting", "cost controls"]
    with scripted(script(tasks)) as model:
        run_graph(app, {"text": TEXT})
    for call in model.calls[1:-1]:
        assert sum(t in call.prompt for t in tasks) == 1
