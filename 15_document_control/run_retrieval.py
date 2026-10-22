"""Retrieval over the intake index, measured on the development questions.

    python run_retrieval.py       # prints the headline numbers, writes results/retrieval.json

Offline. The vector half is llm_provider's hashing stand-in, so these numbers compare the
filters and the ranking over a lexical baseline, not a real embedding model. No answer
quality is measured here: generation runs only on the scripted model in tests, which
checks the plumbing, not the model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent / "12_integration", HERE.parent / "10_observability",
          HERE.parent / "07_rag"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from docctl.evaluate import summary  # noqa: E402
from docctl.intake import run_intake  # noqa: E402
from llm_provider import HashingEmbeddings  # noqa: E402

OUT = HERE / "results" / "retrieval.json"


def main() -> int:
    s = summary(run_intake(), HashingEmbeddings())
    OUT.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    r, st, a, ans, m = s["recall"]["all"], s["stale_versions"], s["access"], s["answerability"], s["migration"]
    print(f"recall: {r['found']} of {r['expected']} expected sections across {r['questions']} questions")
    print(f"stale versions returned by default: {st['returned_by_default']} "
          f"(control without the status filter: {st['control_without_the_status_filter']})")
    print(f"documents returned that the asker may not open: {a['unreadable_returned']} "
          f"(control with every group: {a['control_with_every_group']} forbidden sections)")
    print(f"answer or abstain correct: {ans['correct']} of {ans['of']}; threshold {ans['threshold_in_use']} "
          f"(midpoint {ans['coverage']['midpoint']})")
    print(f"routing: {s['routing']['correct']} of {s['routing']['of']}; candidate index flipped: "
          f"{m['candidate_flipped']}, bad model refused: {m['bad_model_refused']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
