"""The navigator's health checks: one signal per failure mode in RUNBOOK.md.

    parse:<source>     share of lines in each log that parse        alarm below 99%
    fresh:<source>     hours since each feed's newest line           alarm above its limit
    graph store        nodes and edges read back = nodes and edges written
    permissions        lines or pages returned that the asker may not read   alarm above 0
    regression         held-out 3 evidence found by the graph walk   alarm below the recorded score

Each check returns its value next to its threshold, so an alarm says how far off it is.
run_health_check.py runs them on the fixtures as they are, then once per injected fault,
and records which check caught which fault.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .corpus import FIXTURES, NOW, SOURCES, Item, parse_line
from .evaluate import score, summarise

MIN_PARSED = 0.99
# How old each feed's newest line may be before it counts as stopped. Streams log all day;
# the asset inventory is a snapshot, refreshed monthly.
MAX_LAG_HOURS = {"vpn": 6, "firewall": 6, "syslog": 6, "auth": 6, "inventory": 24 * 31}
RECORDED = Path(__file__).resolve().parents[1] / "results" / "held_out_3.json"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    value: float | None        # None when there is nothing to measure
    threshold: str
    detail: str = ""


def parse_coverage(folder: Path = FIXTURES) -> list[Check]:
    """The data contract. The loader stops at the first line it cannot parse, so a rebuild on a
    changed feed fails rather than indexing half of it; this says which feed, and how much."""
    out = []
    for source in SOURCES:
        lines = [x for x in (folder / "logs" / f"{source}.log").read_text(encoding="utf-8").splitlines()
                 if x.strip()]
        bad = []
        for n, line in enumerate(lines, 1):
            try:
                parse_line(source, n, line)
            except ValueError:
                bad.append(n)
        share = (len(lines) - len(bad)) / len(lines) if lines else 0.0
        detail = f"{len(bad)} of {len(lines)} lines unparsed" + (f", first at line {bad[0]}" if bad else "")
        out.append(Check(f"parse:{source}", share >= MIN_PARSED, round(share, 4), f">= {MIN_PARSED}", detail))
    return out


def freshness(items: list[Item], as_of: datetime = NOW) -> list[Check]:
    """A feed that stops looks healthy everywhere else: every line it did send still parses."""
    newest: dict[str, str] = {}
    for it in items:
        if it.kind == "event":
            s = it.attrs["source"]
            newest[s] = max(newest.get(s, ""), it.attrs["ts"])
    out = []
    for source, limit in MAX_LAG_HOURS.items():
        if source not in newest:
            out.append(Check(f"fresh:{source}", False, None, f"<= {limit}h", "no lines at all"))
            continue
        lag = max(0.0, (as_of - datetime.strptime(newest[source], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600)
        out.append(Check(f"fresh:{source}", lag <= limit, round(lag, 1), f"<= {limit}h",
                         f"newest line {newest[source]}"))
    return out


def graph_store(written, read) -> Check:
    """What the walk runs on is what the store returns, so a partial save shrinks every answer."""
    w, r = (written.number_of_nodes(), written.number_of_edges()), (read.number_of_nodes(), read.number_of_edges())
    return Check("graph store", w == r, float(r[0]), f"= {w[0]} nodes, {w[1]} edges",
                 f"read back {r[0]} nodes, {r[1]} edges")


def permissions(runs: dict, questions: list[dict]) -> Check:
    """Every question that names lines its asker may not read, asked of every retriever."""
    leaked = sorted({f for run in runs.values() for s in score(run, questions) for f in s.forbidden})
    return Check("permissions", not leaked, float(len(leaked)), "= 0",
                 ", ".join(leaked[:5]) + (" ..." if len(leaked) > 5 else ""))


def recorded_found(path: Path = RECORDED) -> int:
    d = json.loads(path.read_text(encoding="utf-8"))
    return d["retrievers"]["graph (default)"]["summary"]["held_out_3"]["multi_hop"]["found"]


def regression(run, questions: list[dict], recorded: int | None = None) -> Check:
    """The release gate: held-out 3, multi-hop, must not score below what was recorded when it
    was first scored."""
    recorded = recorded_found() if recorded is None else recorded
    m = summarise(score(run, [q for q in questions if q["set"] == "held_out_3"]))["held_out_3"]["multi_hop"]
    return Check("regression", m["found"] >= recorded, float(m["found"]), f">= {recorded}",
                 f"{m['found']} of {m['expected']} expected items found")


def as_rows(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
