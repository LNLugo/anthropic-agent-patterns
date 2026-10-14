"""Router and extraction accuracy on a labeled question set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .jql_tool import ToolRejected, validate
from .router import NeedsClarification, classify, extract

SPEC_FIELDS = ("project", "issue_type", "priority", "status", "group_by", "created_from", "created_before")


def load_questions(path: str | Path) -> tuple[date, list[dict]]:
    """(today, questions). Refuses a file that still holds template placeholders.

    Each question's "route" is one of:
      aggregation   a Jira count should run; add "spec" to also score extraction
      knowledge     a wiki search should answer it
      no_query      nothing should run: the system should ask (no project, two
                    projects) or the bound should refuse (a project off the allowlist)
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = raw["questions"]
    placeholders = [q["q"] for q in questions if q["q"].startswith("REPLACE")]
    if placeholders:
        raise ValueError(f"{len(placeholders)} template questions still start with REPLACE in {Path(path).name}")
    for q in questions:
        if q["route"] not in ("aggregation", "knowledge", "no_query"):
            raise ValueError(f"unknown route {q['route']!r} for: {q['q']}")
    return date.fromisoformat(raw["today"]), questions


def rules_route(question: str, today: date) -> str:
    """The rules' route, with "no_query" when the counting path would ask or refuse."""
    route = classify(question)
    if route != "aggregation":
        return route
    try:
        validate(extract(question, today).spec)
    except (NeedsClarification, ToolRejected):
        return "no_query"
    return route


def model_route(decision) -> str:
    """The model's route, with "no_query" when it asked or the bound refused its arguments."""
    if decision.route == "clarify" or (decision.route == "aggregation" and decision.rejected):
        return "no_query"
    return decision.route


def spec_diffs(expected: dict, spec) -> list[str]:
    """Field by field differences between a labeled spec and an extracted QuerySpec."""
    diffs = []
    for f in SPEC_FIELDS:
        want = expected.get(f)
        have = getattr(spec, f)
        have = have.isoformat() if isinstance(have, date) else have
        if want != have:
            diffs.append(f"{f}: expected {want!r}, got {have!r}")
    return diffs


@dataclass(frozen=True)
class RouterReport:
    total: int
    routed_correctly: int
    misroutes: tuple[tuple[str, str, str], ...]          # (question, expected, got)
    extraction_total: int
    extracted_correctly: int
    extraction_misses: tuple[tuple[str, str], ...]      # (question, what differed)

    @property
    def router_accuracy(self) -> float:
        return self.routed_correctly / self.total

    @property
    def extraction_accuracy(self) -> float:
        return self.extracted_correctly / self.extraction_total if self.extraction_total else 0.0


def evaluate(path: str | Path) -> RouterReport:
    today, questions = load_questions(path)
    misroutes, misses = [], []
    ok_route = ok_extract = n_extract = 0
    for item in questions:
        got = classify(item["q"]) if item["route"] != "no_query" else rules_route(item["q"], today)
        if got == item["route"]:
            ok_route += 1
        else:
            misroutes.append((item["q"], item["route"], got))
        expected = item.get("spec")
        if expected is None:
            continue
        n_extract += 1
        try:
            spec = extract(item["q"], today).spec
        except NeedsClarification as e:
            misses.append((item["q"], f"asked to clarify: {e}"))
            continue
        diffs = spec_diffs(expected, spec)
        if diffs:
            misses.append((item["q"], "; ".join(diffs)))
        else:
            ok_extract += 1
    return RouterReport(len(questions), ok_route, tuple(misroutes), n_extract, ok_extract, tuple(misses))


# ---------------------------------------------------------------- model router

@dataclass(frozen=True)
class ModelReport:
    total: int
    routed_correctly: int
    misroutes: tuple[tuple[str, str, str], ...]          # (question, expected, got)
    extraction_total: int
    extracted_correctly: int
    extraction_misses: tuple[tuple[str, str], ...]
    rejected_by_bound: tuple[tuple[str, str], ...]      # (question, reason): model args the validator refused
    input_tokens: int
    output_tokens: int
    latency_s: float
    rows: tuple[dict, ...]                              # one record per question, for the results file

    @property
    def router_accuracy(self) -> float:
        return self.routed_correctly / self.total

    @property
    def extraction_accuracy(self) -> float:
        return self.extracted_correctly / self.extraction_total if self.extraction_total else 0.0

    def cost_usd(self, input_price: float, output_price: float) -> float:
        """Total cost at USD per million input and output tokens."""
        return (self.input_tokens * input_price + self.output_tokens * output_price) / 1_000_000


def evaluate_model(path: str | Path, router) -> ModelReport:
    """Score a ModelRouter on the same labeled set and the same field comparison as the rules.

    Asking ("clarify") or a refused query counts as correct only where the label
    is "no_query"; on a question labeled aggregation it is a failure to answer."""
    today, questions = load_questions(path)
    misroutes, misses, rejected, rows = [], [], [], []
    ok_route = ok_extract = n_extract = tok_in = tok_out = 0
    latency = 0.0
    for item in questions:
        d = router.decide(item["q"], today)
        tok_in, tok_out, latency = tok_in + d.input_tokens, tok_out + d.output_tokens, latency + d.latency_s
        got = model_route(d) if item["route"] == "no_query" else d.route
        route_ok = got == item["route"]
        if route_ok:
            ok_route += 1
        else:
            misroutes.append((item["q"], item["route"], got))
        if d.rejected:
            rejected.append((item["q"], d.rejected))
        expected, why = item.get("spec"), None
        if expected is not None:
            n_extract += 1
            if d.spec is None:
                why = f"rejected by the bound: {d.rejected}" if d.rejected else f"no query (route {d.route})"
            else:
                diffs = spec_diffs(expected, d.spec)
                why = "; ".join(diffs) or None
            if why:
                misses.append((item["q"], why))
            else:
                ok_extract += 1
        rows.append({
            "q": item["q"], "expected_route": item["route"], "route": d.route, "route_ok": route_ok,
            "tool": d.tool_name, "arguments": d.arguments, "rejected": d.rejected,
            "extraction_ok": None if expected is None else why is None, "extraction_miss": why,
            "input_tokens": d.input_tokens, "output_tokens": d.output_tokens, "latency_s": round(d.latency_s, 3),
        })
    return ModelReport(
        len(questions), ok_route, tuple(misroutes), n_extract, ok_extract, tuple(misses),
        tuple(rejected), tok_in, tok_out, latency, tuple(rows),
    )
