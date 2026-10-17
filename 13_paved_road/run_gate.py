"""Run the evaluation gate over every use case (or the ones named).

    python run_gate.py                            # offline, every use case; exits 1 below a threshold
    python run_gate.py --mode live                # the configured model chooses; reports, exits 0
    python run_gate.py ../use_cases/transit --report gate.json

In GitHub Actions the table is also written to the job summary.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from paved_road import ROOT
from paved_road.gate import run, summary, to_json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("use_cases", nargs="*", type=Path, help="use case folders; default: every folder in use_cases/")
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--report", type=Path, help="also write the results as JSON")
    args = ap.parse_args()

    folders = args.use_cases or sorted(p.parent for p in (ROOT / "use_cases").glob("*/use_case.yaml"))
    if not folders:
        print("no use cases found")
        return 1
    results = [run(f, args.mode) for f in folders]
    table = summary(results)
    print(table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"## Evaluation gate ({args.mode})\n\n{table}")
    if args.report:
        args.report.write_text(to_json(results) + "\n", encoding="utf-8")
    if args.mode == "offline" and not all(r.passed for r in results):
        print("Gate failed: a use case is below its offline threshold.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
