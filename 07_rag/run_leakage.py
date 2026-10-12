"""Permission leakage check: every persona asks every probe query.

Prints restricted chunks returned with the permission filter on and off. The
"off" row is the positive control: it shows the check can see a leak, so the
"on" row's zero means something.

    python run_leakage.py
    python run_leakage.py --wiki tests/fixtures/mini_confluence.json --k 5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag.acl import PermissionFilteredBM25, build_index, effective_gates, load_wiki, measure_leakage

DEFAULT = Path(__file__).parent / "tests" / "fixtures" / "mini_confluence.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wiki", type=Path, default=DEFAULT)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    wiki, users = load_wiki(args.wiki)
    probes = json.loads(args.wiki.read_text(encoding="utf-8"))["probe_queries"]
    index = build_index(wiki)
    personas = list(users.values())

    restricted_pages = sum(1 for pid in wiki.pages if len(effective_gates(pid, wiki)) > 1)
    print(f"{len(wiki.pages)} pages in {len(wiki.spaces)} spaces ({restricted_pages} with page or inherited restrictions), "
          f"{len(index.chunks)} chunks, {len(personas)} personas, {len(probes)} probe queries, k={args.k}\n")
    print(f"{'Filter':<8} {'Queries':>8} {'Chunks returned':>16} {'Restricted returned':>20}")

    rows = {}
    for label, enforce in (("off", False), ("on", True)):
        r = measure_leakage(PermissionFilteredBM25(index, enforce=enforce), index, personas, probes, k=args.k)
        rows[label] = r
        print(f"{label:<8} {r.queries:>8} {r.chunks_returned:>16} {r.restricted_returned:>20}")

    if rows["on"].restricted_returned:
        print("\nLEAK with filter on:")
        for user, query, chunk in rows["on"].leaked:
            print(f"  {user} <- {chunk}  ({query})")
        return 1
    if not rows["off"].restricted_returned:
        print("\nPositive control found no leaks: the probe set cannot detect a broken filter.")
        return 1
    print("\nPASS: zero restricted chunks returned with the filter on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
