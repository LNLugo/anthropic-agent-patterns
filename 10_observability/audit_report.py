"""Answer audit questions from trace files alone: no access to the running system.

    python run_leak_check.py --keep traces_demo     # produce some traces, offline
    python audit_report.py traces_demo/sensitive_off

For each identity: how many runs, model calls and tokens; which tools ran and how
they ended; every guardrail verdict by behaviour; and who requested, approved or
declined each write. Then every halt and every declined write, with who and why.
Works on traces written with sensitive data off, because none of it needs content.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from tracing.files import identity, load_traces as load


def report(traces: dict, spans: list[dict]) -> dict:
    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["trace_id"]].append(s)
    people: dict[str, Counter] = defaultdict(Counter)
    halts, declines = [], []
    for tid, t in traces.items():
        mine = by_trace[tid]
        who = identity(t, mine)
        c = people[who]
        c["runs"] += 1
        c[f"runs {t['status']}"] += 1
        for s in mine:
            d = s["data"]
            if s["kind"] == "generation":
                c["model calls"] += 1
                c["input tokens"] += d.get("input_tokens", 0)
                c["output tokens"] += d.get("output_tokens", 0)
            elif s["kind"] == "tool":  # a tool runs as its caller, who may not be the trace's identity
                people[d.get("caller") or who][f"tool {s['name']}: {d.get('outcome', '?')}"] += 1
            elif s["kind"] == "guardrail":
                c[f"guardrail {d.get('behavior', '?')}"] += 1
                if d.get("behavior") == "raise_exception":
                    halts.append({"identity": who, "guardrail": s["name"], "workflow": t["workflow"],
                                  "at": s["started_at"], "found": d.get("output_info")})
            elif s["kind"] == "handoff" and d.get("to_agent") == "human_approver":
                c["writes requested"] += 1
            elif s["kind"] == "custom" and s["name"] == "approval":
                c[f"writes {d['decision']} (as approver)"] += 1
                if d["decision"] == "declined":
                    declines.append({"requested_by": d.get("requested_by"), "approver": d.get("approver"),
                                     "issue": d.get("issue_key"), "self_approval": d.get("self_approval", False),
                                     "at": s["started_at"]})
    return {"identities": {k: dict(v) for k, v in sorted(people.items())}, "halts": halts, "declined_writes": declines}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default="traces")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()
    traces, spans = load(Path(args.folder))
    if not traces:
        raise SystemExit(f"no traces-*.jsonl under {args.folder}")
    r = report(traces, spans)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    print(f"{len(traces)} traces, {len(spans)} spans, from {args.folder}\n")
    for who, c in r["identities"].items():
        print(who)
        for k, v in sorted(c.items()):
            print(f"  {k:42} {v}")
    print("\nHalts (raise_exception):")
    for h in r["halts"] or [{"identity": "none"}]:
        print("  " + (f"{h['at']}  {h['identity']} in {h['workflow']}: {h['guardrail']} {h['found']}" if "at" in h else "none"))
    print("\nDeclined writes:")
    for d in r["declined_writes"] or [None]:
        print("  " + (f"{d['at']}  {d['issue']} requested by {d['requested_by']}, declined by {d['approver']}"
                      + (" (self-approval refused)" if d["self_approval"] else "") if d else "none"))


if __name__ == "__main__":
    main()
