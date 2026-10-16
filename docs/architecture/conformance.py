"""Standards conformance, computed from the repository rather than claimed.

    python docs/architecture/conformance.py          # print the scorecard; exit 1 on a failure
    python docs/architecture/conformance.py --write  # also write scorecard.md

For every standard and every module it applies to, one status:

    tested     tests enforce it for that module (standards.yaml: evidence)
    checked    a static check over the module's code passes (standards.yaml: checks)
    excepted   it does not, and an unexpired exception says who owns the gap and until when
    attested   the standard is enforced by design review only
    gap        none of the above: an unregistered nonconformance

The check fails on a gap, on a static check failing with no exception, on an
expired exception, and on a stale one: an exception for something that now
conforms must be closed, or the register stops meaning anything.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODULES = {"01": "01_prompt_chaining", "02": "02_routing", "03": "03_parallelization",
           "04": "04_orchestrator_workers", "05": "05_evaluator_optimizer", "06": "06_agents",
           "07": "07_rag", "08": "08_tools", "10": "10_observability", "11": "11_finops", "12": "12_integration"}


# ---------------------------------------------------------------- static checks

MODEL_CLASSES = {"ChatOpenAI", "ChatAnthropic", "AzureChatOpenAI", "OpenAI", "Anthropic"}


def model_construction(module: str) -> list[str]:
    """APP-05: no module builds a chat model itself; llm_provider.get_chat_model does.
    Returns path:line for every construction of a provider's model class outside tests."""
    found = []
    for path in sorted((ROOT / MODULES[module]).rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "tests" in rel.parts or path.name.startswith("test_"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in MODEL_CLASSES:
                    found.append(f"{rel}:{node.lineno}")
    return found


CHECKS = {"model_construction": model_construction}


# ---------------------------------------------------------------- the report

@dataclass
class Row:
    standard: str
    module: str
    status: str                       # tested, checked, excepted, attested, gap, failing
    detail: list[str] = field(default_factory=list)
    exception: str | None = None


@dataclass
class Report:
    rows: list[Row]
    expired: list[dict]
    stale: list[dict]
    unknown: list[dict]              # exceptions for a standard or module that does not apply

    @property
    def gaps(self) -> list[Row]:
        return [r for r in self.rows if r.status in ("gap", "failing")]

    @property
    def ok(self) -> bool:
        return not (self.gaps or self.expired or self.stale or self.unknown)

    def count(self, status: str, module: str | None = None, standard: str | None = None) -> int:
        return sum(r.status == status and module in (None, r.module) and standard in (None, r.standard)
                   for r in self.rows)


def load() -> tuple[list[dict], list[dict]]:
    return (yaml.safe_load((HERE / "standards.yaml").read_text())["standards"],
            yaml.safe_load((HERE / "exceptions.yaml").read_text())["exceptions"])


def evaluate(standards: list[dict], exceptions: list[dict], today: date,
             checks: dict = CHECKS) -> Report:
    excepted = {(e["standard"], m): e for e in exceptions for m in e["modules"]}
    rows = []
    for s in standards:
        for m in s["applies_to"]:
            e = excepted.get((s["id"], m))
            if m in s.get("evidence", {}):
                rows.append(Row(s["id"], m, "tested", list(s["evidence"][m]), e and e["id"]))
            elif s.get("checks"):
                found = [f for c in s["checks"] for f in checks[c](m)]
                if not found:
                    rows.append(Row(s["id"], m, "checked", [], e and e["id"]))
                else:
                    rows.append(Row(s["id"], m, "excepted" if e else "failing", found, e and e["id"]))
            elif not s.get("evidence"):
                rows.append(Row(s["id"], m, "attested"))
            elif e:
                rows.append(Row(s["id"], m, "excepted", [], e["id"]))
            else:
                rows.append(Row(s["id"], m, "gap"))
    applies = {(s["id"], m) for s in standards for m in s["applies_to"]}
    conforming = {(r.standard, r.module) for r in rows if r.status in ("tested", "checked")}
    expired = [e for e in exceptions if today > e["expires"]]
    stale = [e for e in exceptions if all((e["standard"], m) in conforming for m in e["modules"])]
    unknown = [e for e in exceptions if any((e["standard"], m) not in applies for m in e["modules"])]
    return Report(rows, expired, stale, unknown)


# ---------------------------------------------------------------- the scorecard

def scorecard(report: Report, standards: list[dict], exceptions: list[dict]) -> str:
    mods = sorted({r.module for r in report.rows})
    total = len(report.rows)
    conf = report.count("tested") + report.count("checked")
    out = ["# Standards conformance scorecard", "",
           "<!-- Written by conformance.py --write. Do not edit by hand. -->", "",
           f"Computed from the repository: {len(standards)} standards, {total} standard-module pairs.", "",
           "| Status | Pairs | Meaning |", "|---|---|---|",
           f"| Tested | {report.count('tested')} | Named tests enforce it for that module |",
           f"| Checked | {report.count('checked')} | A static check over the module's code passes |",
           f"| Excepted | {report.count('excepted')} | Not met; a registered exception owns the gap |",
           f"| Attested | {report.count('attested')} | Enforced by design review only |",
           f"| Gap | {len(report.gaps)} | Not met and not registered |", "",
           f"**Conforming: {conf} of {total} pairs ({conf / total:.0%}).** "
           f"Attested pairs are not counted as conforming.", "",
           "## By module", "", "| Module | Applicable | Conforming | Excepted | Attested | Gaps |", "|---|---|---|---|---|---|"]
    for m in mods:
        n = sum(r.module == m for r in report.rows)
        c = report.count("tested", m) + report.count("checked", m)
        g = sum(r.module == m for r in report.gaps)
        out.append(f"| {MODULES[m]} | {n} | {c} ({c / n:.0%}) | {report.count('excepted', m)} | "
                   f"{report.count('attested', m)} | {g} |")
    out += ["", "## By standard", "", "| Standard | " + " | ".join(mods) + " |", "|---|" + "---|" * len(mods)]
    mark = {"tested": "tested", "checked": "checked", "excepted": "exception", "attested": "review", "gap": "**gap**",
            "failing": "**failing**"}
    for s in standards:
        cells = {r.module: mark[r.status] for r in report.rows if r.standard == s["id"]}
        out.append(f"| {s['id']} | " + " | ".join(cells.get(m, "") for m in mods) + " |")
    out += ["", "## Exceptions", "", "| ID | Standard | Modules | Expires | Found by the check |", "|---|---|---|---|---|"]
    for e in exceptions:
        found = [d for r in report.rows if r.exception == e["id"] for d in r.detail]
        out.append(f"| {e['id']} | {e['standard']} | {', '.join(e['modules'])} | {e['expires']} | "
                   f"{', '.join(f'`{f}`' for f in found) or 'n/a'} |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write scorecard.md")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()
    standards, exceptions = load()
    report = evaluate(standards, exceptions, args.today)
    card = scorecard(report, standards, exceptions)
    if args.write:
        (HERE / "scorecard.md").write_text(card)
    print(card)
    for r in report.gaps:
        print(f"FAIL {r.standard} in {MODULES[r.module]}: {r.status}" + (f" ({', '.join(r.detail)})" if r.detail else ""))
    for e in report.expired:
        print(f"FAIL {e['id']} expired on {e['expires']}")
    for e in report.stale:
        print(f"FAIL {e['id']} is stale: {e['standard']} now conforms in {', '.join(e['modules'])}; close it")
    for e in report.unknown:
        print(f"FAIL {e['id']} names a standard or module that does not apply")
    return 0 if report.ok else 1
