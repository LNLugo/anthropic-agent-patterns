"""Comparison, the certificate and standard checks, and review: measured offline.

    python run_comparison.py           # writes results/comparison.json
    python run_comparison.py --live    # also runs the two model arms of the headline result,
                                       # three times, and records every reply in
                                       # results/comparison_live.json (needs OPENAI_API_KEY)

Offline, the model arms are scored from the recorded replies, so the numbers reproduce with
no key. Without a recording they are reported as not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent / "12_integration", HERE.parent / "10_observability",
          HERE.parent / "07_rag"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from docctl import headline  # noqa: E402
from docctl.checks_summary import summary  # noqa: E402

OUT = HERE / "results" / "comparison.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    if args.live:
        from llm_provider import get_chat_model
        from tracing import model_name

        llm = get_chat_model()
        headline.record_live(llm, args.runs, model_name(llm), date.today().isoformat())
        print(f"recorded {args.runs} live runs in {headline.RECORDED}")
    s = summary()
    OUT.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    v, h = s["version_vs_version"], s["version_vs_version"]["rules"]
    print(f"rules: {h['found']} of {h['planted']} planted changes found, {h['type_correct']} typed and "
          f"{h['severity_correct']} graded correctly, {h['false_findings']} false findings")
    print("model arms:", "recorded" if isinstance(v["model_arms"], dict) else v["model_arms"])
    c = s["certificate_vs_specification"]
    print(f"certificates: {c['planted_caught']} of {c['planted_arrived']} planted out-of-specification results "
          f"that arrived caught, {c['other_findings']} other findings")
    d = s["document_vs_standard"]
    print(f"standard: {d['planted_caught']} of {d['planted']} planted breaks caught, "
          f"{d['planted_passed_wrongly']} passed wrongly; corpus verdicts {d['corpus']['verdicts']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
