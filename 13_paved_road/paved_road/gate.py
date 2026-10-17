"""The evaluation gate: each use case's golden set, run through its real pipeline.

    offline  a scripted model replays each case's reference call, so everything after
             the model's choice runs for real: the router's parsing and coverage check,
             the tool's bound, the guardrails in order, Jira's permissions, the budget,
             approval and the post. No key, deterministic, so the threshold is 100% by
             default and anything less is a regression. Blocks the merge in CI.
    live     the configured model makes the choice. Scored twice: did the run end as
             expected, and did the model make the reference call. Needs a key; reports,
             does not block, because a model's choice varies from run to run.

A golden case:

    - id: count-open-elev
      as: station_supervisor
      question: "How many open ELEV incidents since October 1?"
      reference_call: {tool: count_jira_issues, args: {project: ELEV, ...}}
      approval: {approved: true, approver: maintenance_lead}     # writes only: decide, then check
      expect: {ended: answered, count: 3}

expect keys: ended, route, count, keys, guardrail (the one that stopped or halted
the run), answer_contains.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import yaml
from langchain_core.messages import AIMessage

from .build import build
from .spec import ConfigError, load_spec

EXPECT = {"ended", "route", "count", "keys", "guardrail", "answer_contains"}
CASE = {"id", "as", "question", "reference_call", "approval", "expect", "note"}
# Free text the model writes in its own words: a live call is not wrong for phrasing it differently.
FREE_TEXT = {"project_evidence", "query", "question", "body"}


@dataclass
class CaseResult:
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    call_matched: bool | None = None   # live only: the model made the reference call
    ended: str | None = None


@dataclass
class GateResult:
    use_case: str
    mode: str
    threshold: float
    cases: list[CaseResult]

    @property
    def passed_count(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def score(self) -> float:
        return self.passed_count / len(self.cases) if self.cases else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.cases) and self.score >= self.threshold

    def as_dict(self) -> dict:
        return {"use_case": self.use_case, "mode": self.mode, "threshold": self.threshold,
                "score": self.score, "passed": self.passed, "cases": [asdict(c) for c in self.cases]}


def _plain(value):
    """YAML reads 2026-10-01 as a date; tool arguments are JSON, so dates go back to strings."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def load_golden(path: Path, needs_reference: bool) -> list[dict]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(cases, list) or not cases:
        raise ConfigError(f"{path.name}: a list of at least one case")
    seen = set()
    for c in cases:
        unknown = set(c) - CASE
        if unknown:
            raise ConfigError(f"{path.name}: case {c.get('id')}: unknown key(s) {sorted(unknown)}")
        for k in ("id", "as", "question", "expect"):
            if k not in c:
                raise ConfigError(f"{path.name}: case {c.get('id')}: missing {k}")
        if c["id"] in seen:
            raise ConfigError(f"{path.name}: duplicate case id {c['id']}")
        seen.add(c["id"])
        bad = set(c["expect"]) - EXPECT
        if bad:
            raise ConfigError(f"{path.name}: case {c['id']}: unknown expectation(s) {sorted(bad)}")
        if needs_reference and not c.get("reference_call"):
            raise ConfigError(f"{path.name}: case {c['id']}: a model router's case needs a reference_call")
    return [_plain(c) for c in cases]


def scripted_for(case: dict):
    """A stand-in model whose one reply is the case's reference call."""
    from llm_provider import ScriptedChatModel

    call = case["reference_call"]
    model = ScriptedChatModel()
    model.load([AIMessage(content="", tool_calls=[{"name": call["tool"], "args": dict(call.get("args") or {}),
                                                   "id": "call_ref"}],
                          usage_metadata={"input_tokens": 600, "output_tokens": 40, "total_tokens": 640})])
    return model


def check(expect: dict, out: dict) -> list[str]:
    failures = []
    result = out.get("result")

    def differ(what, want, got):
        if want != got:
            failures.append(f"{what}: expected {want!r}, got {got!r}")

    if "ended" in expect:
        differ("ended", expect["ended"], out.get("ended"))
    if "route" in expect:
        differ("route", expect["route"], out.get("route"))
    if "count" in expect:
        differ("count", expect["count"], result.count if result is not None else None)
    if "keys" in expect:
        differ("keys", sorted(expect["keys"]), sorted(result.keys) if result is not None else None)  # any order
    if "guardrail" in expect:
        stopped = out.get("guardrail") or next(
            (r.guardrail for r in out.get("guardrail_records") or () if r.behavior != "allow"), None)
        differ("guardrail", expect["guardrail"], stopped)
    if "answer_contains" in expect and expect["answer_contains"] not in (out.get("answer") or ""):
        failures.append(f"answer does not contain {expect['answer_contains']!r}")
    return failures


def _same_call(case: dict, decision) -> bool:
    ref = case["reference_call"]
    if decision is None or decision.tool_name != ref["tool"]:
        return False
    strip = lambda a: {k: v for k, v in (a or {}).items() if k not in FREE_TEXT and v is not None}  # noqa: E731
    return strip(_plain(decision.arguments)) == strip(ref.get("args"))


def run_case(folder: Path, case: dict, mode: str, workdir: Path, llm_for=None) -> CaseResult:
    """llm_for(case) -> the router's model. Offline: a scripted reference call. Live: None, so
    build() asks llm_provider for the configured model (tests pass a stand-in)."""
    spec = load_spec(folder)
    from tracing import InMemoryExporter

    if llm_for is None:
        llm_for = scripted_for if mode == "offline" else (lambda case: None)
    llm = llm_for(case) if spec.router.kind == "model" else None
    uc = build(folder, llm=llm, state_dir=workdir / case["id"], exporters=[InMemoryExporter()],
               today=spec.gate.today)
    seen = []
    if spec.router.kind == "model":
        decide = uc.router.decide

        def recording(*a, **kw):
            d = decide(*a, **kw)
            seen.append(d)
            return d

        uc.router.decide = recording
    thread = f"gate-{case['id']}" if spec.comment else None
    try:
        out = uc.ask(case["question"], case["as"], thread_id=thread)
        if case.get("approval"):
            if out["ended"] != "pending_approval":
                return CaseResult(case["id"], False, [f"expected to wait for approval, ended {out['ended']}"],
                                  ended=out["ended"])
            a = case["approval"]
            out = uc.decide(thread, approved=a["approved"], approver=a["approver"], reason=a.get("reason", ""))
    except Exception as e:  # a crash is a failed case, not a crashed gate
        return CaseResult(case["id"], False, [f"{type(e).__name__}: {e}"])
    failures = check(case["expect"], out)
    matched = None
    if mode == "live" and spec.router.kind == "model":
        matched = _same_call(case, seen[0] if seen else None)
    if mode == "offline" and getattr(llm, "remaining_steps", 0):
        failures.append("the reference call was never asked for")
    return CaseResult(case["id"], not failures, failures, matched, out.get("ended"))


def run(folder: str | Path, mode: str = "offline", llm_for=None) -> GateResult:
    if mode not in ("offline", "live"):
        raise ValueError("mode: offline or live")
    folder = Path(folder).resolve()
    spec = load_spec(folder)
    cases = load_golden(spec.golden, needs_reference=spec.router.kind == "model")
    with tempfile.TemporaryDirectory() as tmp:
        results = [run_case(folder, c, mode, Path(tmp), llm_for) for c in cases]
    threshold = spec.gate.offline_threshold if mode == "offline" else spec.gate.live_threshold
    return GateResult(spec.name, mode, threshold, results)


def summary(results: list[GateResult]) -> str:
    lines = ["| Use case | Mode | Passed | Score | Threshold | Gate |", "|---|---|---|---|---|---|"]
    for r in results:
        verdict = "pass" if r.passed else ("**fail**" if r.mode == "offline" else "below (reported)")
        lines.append(f"| {r.use_case} | {r.mode} | {r.passed_count}/{len(r.cases)} | {r.score:.0%} | "
                     f"{r.threshold:.0%} | {verdict} |")
    if any(r.mode == "live" for r in results):
        lines.append("")
        for r in results:
            calls = [c.call_matched for c in r.cases if c.call_matched is not None]
            if calls:
                lines.append(f"{r.use_case}: the model made the reference call in {sum(calls)} of {len(calls)} cases.")
    failed = [(r.use_case, c) for r in results for c in r.cases if not c.passed]
    if failed:
        lines += ["", "Failed cases:", ""]
        lines += [f"- {uc} / {c.id}: {'; '.join(c.failures)}" for uc, c in failed]
    return "\n".join(lines) + "\n"


def to_json(results: list[GateResult]) -> str:
    return json.dumps([r.as_dict() for r in results], indent=2)
