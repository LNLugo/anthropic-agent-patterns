"""Budgets checked before every model call: where they stop a run, and that nothing is called after.

    python run_budget_check.py        # writes results/budget_check.json

Runs 08's assistant and tool loop offline. The model is the scripted stand-in,
priced as gpt-6-luna and reporting the held-out mean of 728 input and 39 output
tokens per router call, so each call costs what an average real call does
($0.0000923). Spend is measured by a SpendMeter on the tracer, from generation
spans, exactly as it would be in production. Each scenario has a control run
with no budget.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ["LLM_PROVIDER"] = "scripted"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT, ROOT / "10_observability", ROOT / "08_tools"):
    sys.path.insert(0, str(p))

from langchain_core.messages import AIMessage  # noqa: E402

from finops import BudgetPolicy, PriceBook, SpendMeter  # noqa: E402
from llm_provider import scripted, scripted_model  # noqa: E402
from tracing import InMemoryExporter, Tracer, set_tracer  # noqa: E402

TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}
TOKENS = {"input_tokens": 728, "output_tokens": 39, "total_tokens": 767}   # held-out mean, gpt-6-luna
MODEL = "gpt-6-luna"


def call(project, call_id="c1"):
    return AIMessage(content="", usage_metadata=TOKENS, tool_calls=[{
        "name": "count_jira_issues", "args": {"project": project, "project_evidence": project, **Q3}, "id": call_id}])


class Clock:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


def setup(limits=None, reserve=None, default=None):
    book = PriceBook.load()
    clock = Clock(TODAY)
    meter = SpendMeter(book, today=clock)
    one = book.cost(MODEL, TOKENS["input_tokens"], TOKENS["output_tokens"])
    budget = None if limits is None else BudgetPolicy(meter, limits, default, reserve=one if reserve is None else reserve)
    trace = InMemoryExporter()
    set_tracer(Tracer([trace, meter]))
    return book, meter, budget, clock, one, trace


def jira():
    from agent_tools.jira_mock import MockJira
    from agent_tools.jql_tool import BoundedJiraTool

    j, users = MockJira.from_fixture(ROOT / "08_tools" / "tests" / "fixtures" / "mini_jira.json")
    return BoundedJiraTool(j), users


def assistant(tool, budget, model=MODEL):
    from agent_tools.assistant import build_assistant
    from agent_tools.model_router import ModelRouter

    router = ModelRouter(scripted_model(), tool)
    router.model = model        # the stand-in, priced as the model it stands in for
    return build_assistant(tool, lambda q, c, k: [], router, budget=budget)


def daily_limit(with_budget: bool) -> dict:
    """billing_oncall asks 12 counts against a $0.001 daily budget; new_hire asks 2."""
    from agent_tools.assistant import ask

    _, meter, budget, clock, one, trace = setup({"billing_oncall": 0.001, "new_hire": 0.001} if with_budget else None)
    tool, users = jira()
    app = assistant(tool, budget)
    outcomes, calls = [], 0
    for who, n in (("billing_oncall", 12), ("new_hire", 2)):
        for _ in range(n):
            with scripted([call("BILL")]) as model:
                out = ask(app, "How many BILL issues in Q3?", users[who], TODAY)
                calls += len(model.calls)
                if out.get("stop_reason") == "budget":
                    model.discard_steps()  # refused before the model: its reply is never used
            outcomes.append((who, out.get("stop_reason") or "answered"))
    warned = [s["data"]["output_info"].get("warning") for s in trace.of_kind("guardrail") if s["name"] == "model_budget"]
    clock.day = TODAY + timedelta(days=1)
    next_day = None
    if with_budget:
        with scripted([call("BILL")]):
            next_day = ask(app, "How many BILL issues in Q3?", users["billing_oncall"], TODAY).get("stop_reason") or "answered"
    oncall = [o for w, o in outcomes if w == "billing_oncall"]
    return {"budget": with_budget, "cost_per_call": one, "model_calls": calls,
            "billing_oncall_answered": oncall.count("answered"), "billing_oncall_refused": oncall.count("budget"),
            "first_refused_at": oncall.index("budget") + 1 if "budget" in oncall else None,
            "warned_runs": [i + 1 for i, w in enumerate(warned[:12]) if w],
            "new_hire_answered": [o for w, o in outcomes if w == "new_hire"].count("answered"),
            "billing_oncall_spent": meter.spent("billing_oncall", TODAY), "next_day": next_day}


def runaway_loop(with_budget: bool) -> dict:
    """A model that keeps asking for an out-of-scope project, in the tool loop (4 turns)."""
    from agent_tools.guardrails import finance_assistant_policy
    from agent_tools.tool_loop import ToolLoop

    _, meter, budget, _, one, _ = setup({"billing_oncall": 2.5 * 0.0000923} if with_budget else None)
    tool, users = jira()
    loop = ToolLoop(scripted_model(), tool, finance_assistant_policy(), budget=budget)
    loop.model = MODEL
    with scripted([call("INFRA", f"c{i}") for i in range(4)]) as model:
        result = loop.run("How many INFRA issues in Q3?", users["billing_oncall"], TODAY)
        calls = len(model.calls)
        model.discard_steps()
    return {"budget": with_budget, "status": result.status, "turns": result.turns, "model_calls": calls,
            "spent": meter.spent("billing_oncall", TODAY)}


def unpriced_model() -> dict:
    """The router is switched to a model the price file does not have."""
    from agent_tools.assistant import ask

    _, _, budget, _, _, _ = setup({"billing_oncall": 1.0})
    tool, users = jira()
    with scripted([]) as model:
        out = ask(assistant(tool, budget, model="claude-sonnet-5-5"), "How many BILL issues in Q3?",
                  users["billing_oncall"], TODAY)
    return {"stop_reason": out.get("stop_reason"), "answer": out["answer"], "model_calls": len(model.calls)}


def main() -> int:
    result = {"daily_limit": [daily_limit(True), daily_limit(False)],
              "runaway_loop": [runaway_loop(True), runaway_loop(False)],
              "unpriced_model": unpriced_model()}
    set_tracer(None)
    d, c = result["daily_limit"]
    print(f"Daily budget, $0.001 for billing_oncall; each call ${d['cost_per_call']:.7f}, reserve one call")
    print(f"  with budget:    {d['billing_oncall_answered']} answered, refused from question {d['first_refused_at']},"
          f" {d['model_calls']} model calls in all; warned on runs {d['warned_runs']};"
          f" spent ${d['billing_oncall_spent']:.6f}; new_hire answered {d['new_hire_answered']} of 2;"
          f" next day: {d['next_day']}")
    print(f"  without (control): {c['billing_oncall_answered']} answered, {c['model_calls']} model calls")
    b, nb = result["runaway_loop"]
    print(f"\nRunaway tool loop, budget of 2.5 calls")
    print(f"  with budget:    stopped '{b['status']}' at turn {b['turns']}, {b['model_calls']} model calls")
    print(f"  without (control): '{nb['status']}' after {nb['model_calls']} model calls")
    u = result["unpriced_model"]
    print(f"\nRouter switched to an unpriced model: {u['stop_reason']}, {u['model_calls']} model calls: {u['answer']}")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "budget_check.json").write_text(json.dumps(result, indent=2) + "\n")
    print("\nWrote results/budget_check.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
