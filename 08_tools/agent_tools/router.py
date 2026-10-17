"""Routing and argument extraction for the two question shapes.

  knowledge    "how do I rotate the billing credentials"   -> retrieval (07_rag)
  aggregation  "how many P1s did billing have last quarter" -> count_jira_issues

Both steps are deliberately rule based here. That makes them deterministic and
testable offline, and it gives a measured baseline: run_router_eval.py reports
how often the rules route correctly on a labeled question set. In production a
model would fill the same QuerySpec through tool calling, and the same
validator in jql_tool.py would still sit between the model and Jira. The rules
are the stand-in for the model, not for the bound.

Routing requires two cues together: a counting cue ("how many", "list",
"which") and an issue cue ("incidents", "bugs", "P1"). Either alone is not
enough. "How many steps are in the deploy pipeline" has a counting cue but no
issue cue, and "what is the process for declaring a P1" has an issue cue but
no counting cue; both are knowledge questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .catalog import FINANCE, Catalog
from .jql_tool import QuerySpec

COUNTING = re.compile(
    r"\b(how many|number of|count|list|show me|which|were there any|any open|top \d+)\b", re.I
)
ISSUE = re.compile(r"\b(incidents?|bugs?|issues?|tickets?|changes?|outages?|p[1-4]s?)\b", re.I)

PROJECT_ALIASES = FINANCE.aliases  # the default catalog's words; pass a Catalog for another use case
TYPE_WORDS = {"incident": "Incident", "outage": "Incident", "bug": "Bug", "task": "Task", "change": "Change"}
STATUS_WORDS = {"open": "Open", "in progress": "In Progress", "resolved": "Resolved", "closed": "Closed"}

DEFAULT_WINDOW_DAYS = 90


class NeedsClarification(ValueError):
    """The question cannot be turned into a query without guessing."""


@dataclass
class Extraction:
    spec: QuerySpec
    assumptions: list[str] = field(default_factory=list)


def classify(question: str) -> str:
    return "aggregation" if COUNTING.search(question) and ISSUE.search(question) else "knowledge"


# ---------------------------------------------------------------- dates

def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _shift_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def time_window(question: str, today: date) -> tuple[date, date, str | None]:
    """(from, before_exclusive, assumption or None). Calendar periods, not rolling ones."""
    q = question.lower()
    tomorrow = today + timedelta(days=1)

    m = re.search(r"\bq([1-4])(?:\s+(\d{4}))?\b", q)
    if m:
        year = int(m.group(2)) if m.group(2) else today.year
        start = date(year, 3 * (int(m.group(1)) - 1) + 1, 1)
        return start, _shift_months(start, 3), None
    if re.search(r"\b(last|previous|prior) quarter\b", q):
        this_q = _quarter_start(today)
        return _shift_months(this_q, -3), this_q, None
    if re.search(r"\bthis quarter\b", q):
        return _quarter_start(today), tomorrow, None
    if re.search(r"\b(last|previous|prior) month\b", q):
        this_m = date(today.year, today.month, 1)
        return _shift_months(this_m, -1), this_m, None
    if re.search(r"\bthis month\b", q):
        return date(today.year, today.month, 1), tomorrow, None
    m = re.search(r"\b(?:last|past) (\d{1,3}) days\b", q)
    if m:
        return today - timedelta(days=int(m.group(1)) - 1), tomorrow, None
    if re.search(r"\bthis year\b", q):
        return date(today.year, 1, 1), tomorrow, None
    m = re.search(r"\bin (20\d\d)\b", q)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y + 1, 1, 1), None
    start = today - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    return start, tomorrow, f"No time period given, so I used the last {DEFAULT_WINDOW_DAYS} days."


# ---------------------------------------------------------------- extraction

def _first_alias(q: str, table: dict[str, str]) -> str | None:
    # Longest alias first, so "billing platform" wins over "billing".
    for alias in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}s?\b", q):
            return table[alias]
    return None


def extract(question: str, today: date, catalog: Catalog = FINANCE) -> Extraction:
    q = question.lower()
    project = _first_alias(q, catalog.aliases)
    if project is None:
        raise NeedsClarification(catalog.clarifying_question)

    priority = None
    m = re.search(r"\bp([1-4])s?\b", q)
    if m:
        priority = f"P{m.group(1)}"
    issue_type = _first_alias(q, TYPE_WORDS)
    status = _first_alias(q, STATUS_WORDS)

    group_by = None
    if re.search(r"\b(by|per|each) priority\b", q):
        group_by, priority = "priority", None
    elif re.search(r"\b(by|per|each) status\b", q):
        group_by, status = "status", None

    created_from, created_before, note = time_window(question, today)
    assumptions = [note] if note else []
    if priority and issue_type is None:
        issue_type = "Incident"
        assumptions.append(f"“{priority}” read as {priority} incidents.")
    return Extraction(
        QuerySpec(project, created_from, created_before, issue_type, priority, status, group_by),
        assumptions,
    )


# ---------------------------------------------------------------- the rules as a router

class RulesRouter:
    """The rules above, behind the same decide() interface as model_router.ModelRouter,
    so the assistant graph takes either. The rules run offline and are free; the model
    router routed 18 to 19 of 20 held-out questions where the rules routed 9."""

    def __init__(self, catalog: Catalog = FINANCE):
        self.catalog = catalog

    def decide(self, question: str, today: date, context: list[dict] | None = None):
        from .model_router import Decision  # deferred: model_router imports this module's names

        if classify(question) == "knowledge":
            return Decision("knowledge")
        try:
            ex = extract(question, today, self.catalog)
        except NeedsClarification as e:
            return Decision("clarify", message=str(e))
        return Decision("aggregation", ex.spec, assumptions=tuple(ex.assumptions))
