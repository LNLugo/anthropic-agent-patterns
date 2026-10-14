"""Router and extraction accuracy on the labeled question set.

    python run_router_eval.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_tools.evaluate import evaluate

DEFAULT = Path(__file__).parent / "tests" / "fixtures" / "router_questions.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", type=Path, default=DEFAULT)
    args = ap.parse_args()

    try:
        r = evaluate(args.questions)
    except ValueError as e:
        print(f"Cannot score {args.questions.name}: {e}")
        return 1
    print(f"Router:     {r.routed_correctly}/{r.total} routed correctly ({r.router_accuracy:.0%})")
    print(f"Extraction: {r.extracted_correctly}/{r.extraction_total} queries extracted exactly ({r.extraction_accuracy:.0%})")
    if r.misroutes:
        print("\nMisroutes:")
        for q, want, got in r.misroutes:
            print(f"  [{want} -> {got}] {q}")
    if r.extraction_misses:
        print("\nExtraction misses:")
        for q, why in r.extraction_misses:
            print(f"  {q}\n      {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
