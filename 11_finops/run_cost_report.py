"""What the traced runs cost, by identity, workflow and how each run ended.

    python run_cost_report.py ../08_tools/traces        # any folder of traces-*.jsonl

Reads only the trace files (10_observability). Every model call is priced from
prices.yaml; a total that includes a call to an unpriced model is shown as
unpriced, with the models to add, never as a partial sum.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "10_observability"))

from finops import PriceBook  # noqa: E402
from finops.costing import cost_runs, rollup, spend_without_an_answer  # noqa: E402
from tracing import load_traces  # noqa: E402


def money(x: float | None) -> str:
    return "unpriced" if x is None else f"${x:.6f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default="traces")
    ap.add_argument("--prices", default=str(HERE / "prices.yaml"))
    args = ap.parse_args()
    traces, spans = load_traces(args.folder)
    if not traces:
        raise SystemExit(f"no traces-*.jsonl under {args.folder}")
    runs = cost_runs(traces, spans, PriceBook.load(args.prices))
    print(f"{len(runs)} runs from {args.folder}\n")
    for by, title in (("identity", "By identity"), ("workflow", "By workflow"), ("ended", "By how the run ended")):
        print(title)
        for g in rollup(runs, by):
            missing = sorted(m if m else "(model not recorded in the span)" for m in g.unpriced_models)
            extra = f"   {g.unpriced_runs} unpriced run(s): {', '.join(missing)}" if g.unpriced_runs else ""
            print(f"  {g.key:28} runs {g.runs:>3}  calls {g.model_calls:>3}  tokens {g.input_tokens:>7}/{g.output_tokens:<6}"
                  f" {money(g.cost):>12}  per run {money(g.cost_per_run):>12}{extra}")
        print()
    wasted, share = spend_without_an_answer(runs)
    print("Spend on runs that ended without an answer: "
          + ("unpriced" if wasted is None else f"{money(wasted)} ({share:.0%} of spend)" if share is not None else money(wasted)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
