"""What each run cost, from its trace alone, and rollups that refuse to guess.

Every generation span is one model call, carrying its model and token counts
(10_observability). A run's cost is the sum of its calls. If any call used a
model with no price, the run is unpriced, and so is every total that includes it:
a partial sum would understate the spend and look like a real number.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .prices import PriceBook, Unpriced


@dataclass
class RunCost:
    trace_id: str
    workflow: str
    identity: str
    ended: str                       # why the run ended: answered, guardrail, max_turns, ...
    model_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = 0.0         # None when any call is unpriced
    unpriced_models: set = field(default_factory=set)


def _ended(trace: dict, spans: list[dict]) -> str:
    agent = next((s for s in spans if s["kind"] == "agent" and s["parent_id"] is None), None)
    if agent is not None:
        if agent.get("error"):
            return "halted" if "Tripwire" in agent["error"]["type"] else "error"
        d = agent["data"]
        return d.get("ended") or d.get("status") or "unknown"
    return "error" if trace.get("status") == "error" else "unknown"


def cost_runs(traces: dict, spans: list[dict], book: PriceBook) -> list[RunCost]:
    from tracing.files import identity  # the shared trace reader in 10_observability

    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["trace_id"]].append(s)
    runs = []
    for tid, t in traces.items():
        mine = by_trace[tid]
        r = RunCost(tid, t["workflow"], identity(t, mine), _ended(t, mine))
        for s in mine:
            if s["kind"] != "generation":
                continue
            d = s["data"]
            r.model_calls += 1
            r.input_tokens += d.get("input_tokens", 0)
            r.cached_input_tokens += d.get("cached_input_tokens", 0)
            r.output_tokens += d.get("output_tokens", 0)
            try:
                c = book.cost(d.get("model"), d.get("input_tokens", 0), d.get("output_tokens", 0),
                              d.get("cached_input_tokens", 0))
            except Unpriced:
                r.unpriced_models.add(d.get("model"))
                r.cost = None
                continue
            if r.cost is not None:
                r.cost += c
        runs.append(r)
    return runs


@dataclass
class Rollup:
    key: str
    runs: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = 0.0
    unpriced_runs: int = 0
    unpriced_models: set = field(default_factory=set)

    @property
    def cost_per_run(self) -> float | None:
        return None if self.cost is None or not self.runs else self.cost / self.runs


def rollup(runs: list[RunCost], by: str) -> list[Rollup]:
    """Group runs by an attribute of RunCost: identity, workflow or ended."""
    groups: dict[str, Rollup] = {}
    for r in runs:
        g = groups.setdefault(getattr(r, by), Rollup(getattr(r, by)))
        g.runs += 1
        g.model_calls += r.model_calls
        g.input_tokens += r.input_tokens
        g.output_tokens += r.output_tokens
        if r.cost is None:
            g.unpriced_runs += 1
            g.unpriced_models |= r.unpriced_models
            g.cost = None
        elif g.cost is not None:
            g.cost += r.cost
    return sorted(groups.values(), key=lambda g: g.key)


ANSWERED = {"answered", "completed", "posted", "pending_approval"}


def spend_without_an_answer(runs: list[RunCost]) -> tuple[float | None, float | None]:
    """(cost of runs that ended without an answer, share of all spend). None when anything is unpriced."""
    if any(r.cost is None for r in runs):
        return None, None
    total = sum(r.cost for r in runs)
    wasted = sum(r.cost for r in runs if r.ended not in ANSWERED)
    return wasted, (wasted / total if total else None)
