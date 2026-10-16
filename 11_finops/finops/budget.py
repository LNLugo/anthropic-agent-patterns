"""Per-identity budgets, checked before every model call, fed by the trace.

    meter = SpendMeter(book)                       # a trace exporter: prices generation spans as they end
    set_tracer(Tracer([JsonlExporter(), meter]))
    budget = BudgetPolicy(meter, limits={"billing_oncall": 0.50}, default_limit=0.10, reserve=0.0002)
    app = build_assistant(..., budget=budget)      # 08 asks budget.check() before the router's model call

Spend is measured, not estimated: the meter prices each model call from its
generation span, the same record module 10 writes and module 11 reports on.
Before a call, the policy adds a reserve (what one more call is expected to cost)
to what the identity has already spent today:

    spent + reserve <= limit, below warn_at x limit   allow
    spent + reserve <= limit, at or above warn_at     allow, marked as a warning in the trace
    spent + reserve >  limit                          reject_content: the call is not made
    the model to be called, or any call already made today, has no price
                                                      reject_content: spend cannot be measured,
                                                      so it cannot be kept within a limit

Running over budget is not a security event, so nothing halts: the user is told
why, and no model is called. The behaviours are the guardrail contract's (08).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date
from typing import Callable

from .prices import PriceBook, Unpriced


class SpendMeter:
    """A trace exporter that keeps today's spend per identity, from generation spans."""

    def __init__(self, book: PriceBook, today: Callable[[], date] = date.today):
        self.book, self.today = book, today
        self._who: dict[str, str] = {}
        self._spent: dict[tuple[str, date], float] = defaultdict(float)
        self._unpriced: dict[tuple[str, date], set] = defaultdict(set)
        self._lock = threading.Lock()

    def export(self, record: dict) -> None:
        with self._lock:
            if record["type"] == "trace":
                m = record.get("metadata") or {}
                who = m.get("caller") or m.get("approver") or m.get("subject")
                if who:
                    self._who.setdefault(record["trace_id"], who)
                return
            d = record["data"]
            if record["kind"] == "guardrail" and record["name"] == "token_scope" and d.get("subject"):
                self._who[record["trace_id"]] = d["subject"]   # an MCP call names its user in the token check
                return
            if record["kind"] != "generation":
                return
            key = (self._who.get(record["trace_id"], "(unknown)"), self.today())
            try:
                self._spent[key] += self.book.cost(d.get("model"), d.get("input_tokens", 0),
                                                   d.get("output_tokens", 0), d.get("cached_input_tokens", 0))
            except Unpriced:
                self._unpriced[key].add(d.get("model"))

    def shutdown(self) -> None:
        pass

    def spent(self, identity: str, day: date | None = None) -> float:
        return self._spent.get((identity, day or self.today()), 0.0)

    def unpriced(self, identity: str, day: date | None = None) -> set:
        return set(self._unpriced.get((identity, day or self.today()), ()))


class BudgetPolicy:
    def __init__(self, meter: SpendMeter, limits: dict[str, float] | None = None,
                 default_limit: float | None = None, reserve: float = 0.0, warn_at: float = 0.8):
        """limits: USD per identity per day. default_limit: for identities not listed;
        None means they have no budget (and are not checked). reserve: what one more call
        is expected to cost, e.g. the p95 call cost from run_cost_report.py."""
        self.meter, self.limits, self.default_limit = meter, dict(limits or {}), default_limit
        self.reserve, self.warn_at = reserve, warn_at

    def limit(self, identity: str) -> float | None:
        return self.limits.get(identity, self.default_limit)

    def check(self, identity: str, model: str | None) -> tuple[str, str, dict]:
        """(behavior, message, output_info) for the call about to be made. output_info holds
        only numbers and model names from the price file, since it is exported as trace metadata."""
        limit = self.limit(identity)
        if limit is None:
            return "allow", "", {"budget": None}
        if model not in self.meter.book:
            return ("reject_content",
                    "Not run: this model has no price, so its spend cannot be kept within your budget.",
                    {"reason": "model_unpriced", "limit": limit})
        if self.meter.unpriced(identity):
            return ("reject_content",
                    "Not run: earlier calls today used a model with no price, so today's spend is unknown.",
                    {"reason": "spend_unknown", "limit": limit})
        spent = self.meter.spent(identity)
        info = {"spent": round(spent, 8), "reserve": self.reserve, "limit": limit}
        if spent + self.reserve > limit:
            return ("reject_content",
                    f"Not run: today's model budget is used (${spent:.4f} of ${limit:.4f}). It resets tomorrow.",
                    info | {"reason": "over_budget"})
        return "allow", "", info | {"warning": spent + self.reserve >= self.warn_at * limit}
