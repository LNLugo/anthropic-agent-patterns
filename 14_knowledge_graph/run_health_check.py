"""Run every health check on the fixtures as they are, then once per injected fault.

    python run_health_check.py           # prints the table, writes results/health.json

A check that never fires proves nothing, so each fault in RUNBOOK.md is planted here on a
copy of the data and the run records which checks caught it. Offline only: the hashing
stand-in and in-memory stores, no key, no network.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent / "10_observability"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from netgraph import health  # noqa: E402
from netgraph.corpus import FIXTURES, load, load_questions  # noqa: E402
from netgraph.graph import build  # noqa: E402
from netgraph.retrieve import Retriever  # noqa: E402
from netgraph.stores import InMemoryGraphStore, InMemoryVectorStore  # noqa: E402

K = 10


class LossyGraphStore(InMemoryGraphStore):
    """A save that stops partway: the last 5% of nodes, and their edges, never arrive."""

    def save(self, graph):
        keep = sorted(graph.nodes)[: int(graph.number_of_nodes() * 0.95)]
        super().save(graph.subgraph(keep).copy())


# ---------------------------------------------------------------- the faults

def vendor_format_change(folder: Path) -> None:
    """A firmware upgrade at noon on the 14th changes the VPN gateway's line format."""
    path = folder / "logs" / "vpn.log"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) (\S+)(.*)$", line)
        if m and (m[1], m[2]) >= ("2026-10-14", "12:00:00"):
            line = f"{m[1]}T{m[2]}Z vpn-gw {m[3].lower()}{m[4]}"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def feed_stopped(folder: Path) -> None:
    """The firewall's log forwarder stops at 11:00 on the 14th."""
    path = folder / "logs" / "firewall.log"
    kept = [x for x in path.read_text(encoding="utf-8").splitlines() if x[:19] < "2026-10-14 11:00:00"]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def readers_mistagged(items):
    """An ingestion change tags the auth log readable by network operations."""
    return [replace(i, readers=i.readers | {"netops"}) if i.attrs.get("source") == "auth" else i for i in items]


SCENARIOS = {
    "healthy": {},
    "vendor changes the VPN log format": {"files": vendor_format_change},
    "firewall feed stops": {"files": feed_stopped},
    "auth log tagged readable by netops": {"items": readers_mistagged},
    "graph store save stops partway": {"store": LossyGraphStore},
    "walk retuned without re-measuring (4 hops to 1)": {"hops": 1},
}


def run(name: str, fault: dict) -> dict:
    questions = load_questions()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "fixtures"
        shutil.copytree(FIXTURES, folder)
        if "files" in fault:
            fault["files"](folder)
        checks = health.parse_coverage(folder)
        try:
            items = load(folder)
        except ValueError as e:                # the loader refuses: nothing half-built is indexed
            return {"scenario": name, "checks": health.as_rows(checks),
                    "not_run": f"the rebuild stops: {e}"}
    from llm_provider import HashingEmbeddings

    items = fault.get("items", lambda x: x)(items)
    checks += health.freshness(items)
    written = build(items)
    store = fault.get("store", InMemoryGraphStore)()
    store.save(written)
    graph = store.load()
    checks.append(health.graph_store(written, graph))
    r = Retriever(items, InMemoryVectorStore(), HashingEmbeddings(), graph)
    r.index()
    hops = fault.get("hops", 4)
    walk = lambda q, g: r.graph_expanded(q, g, K, hops=hops)  # noqa: E731
    runs = {"vector": lambda q, g: r.vector_only(q, g, K), "graph": walk}
    for check, call in (("permissions", lambda: health.permissions(runs, questions)),
                        ("regression", lambda: health.regression(walk, questions))):
        try:
            checks.append(call())
        except Exception as e:                 # a check that cannot run is an alarm, not a pass
            checks.append(health.Check(check, False, None, "runs", f"raised {type(e).__name__}: {e}"))
    return {"scenario": name, "checks": health.as_rows(checks)}


def main() -> int:
    from tracing import Tracer, set_tracer

    set_tracer(Tracer(exporters=[]))
    report = {"scenarios": [run(name, fault) for name, fault in SCENARIOS.items()]}
    out = HERE / "results" / "health.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for s in report["scenarios"]:
        alarms = [c for c in s["checks"] if not c["ok"]]
        print(f"\n{s['scenario']}: {len(alarms)} alarm(s)" + (f"; {s['not_run']}" if "not_run" in s else ""))
        for c in alarms:
            print(f"  {c['name']:14} {c['value']} (needs {c['threshold']})  {c['detail']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
