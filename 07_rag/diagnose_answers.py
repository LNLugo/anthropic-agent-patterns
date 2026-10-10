"""Attribute every answer-eval outcome to a cause.

    python diagnose_answers.py --cuad data/cuad/test.json --config configs/06_bm25_rerank.yaml \
        --answers results/answers_06_bm25_rerank_openai.jsonl --contracts 102

Uses the context ledger (which chunks the model was shown) and the CUAD gold
spans to separate retrieval failures from model failures:

  clause exists
    correct        answered, and a valid citation lands on the lawyer's span
    wrong cite     answered, but no valid citation lands on the lawyer's span
    retrieval miss abstained; the clause was never in the model's context
    quote failure  abstained; clause was shown, but no quote matched the text
    model missed   abstained; clause was shown, and the model said it was absent
  clause absent
    correct abstain
    false answer   answered a clause the contract does not contain

--contracts and --seed must match the run that produced the answers file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rag.chunking import chunk_contract  # noqa: E402
from rag.config import load_config  # noqa: E402
from rag.data import load_cuad  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuad", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--contracts", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    cfg = load_config(args.config)
    ch = cfg["chunking"]
    contracts, queries = load_cuad(args.cuad, args.contracts, args.seed)
    gold = {q.id: q.gold_spans for q in queries}
    chunks = {}
    for c in contracts:
        kids, parents = chunk_contract(c.text, c.id, ch["size"], ch["overlap"], ch.get("parent_size"))
        chunks.update({k.id: k for k in kids})
        chunks.update(parents)

    rows = [json.loads(line) for line in Path(args.answers).read_text(encoding="utf-8").splitlines() if line.strip()]
    pos, neg, cit = Counter(), Counter(), Counter()
    n_pos = n_neg = unmatched = 0
    for r in rows:
        if r["query_id"] not in gold:
            unmatched += 1
            continue
        for c in r["citations"]:
            cit[c["status"]] += 1
        if not r["answerable"]:
            n_neg += 1
            neg["correct abstain" if r["status"] == "abstained" else "false answer"] += 1
            continue
        n_pos += 1
        spans = gold[r["query_id"]]
        shown_ids = next((s["chunk_ids"] for s in r["ledger"]["stages"] if s["stage"] == "retrieve"), [])
        shown = any(chunks[i].overlaps(g) for i in shown_ids if i in chunks for g in spans)
        if r["status"] == "answered":
            on_gold = any(
                c["status"] == "valid" and c["span"] and any(c["span"][0] < e and s < c["span"][1] for s, e in spans)
                for c in r["citations"]
            )
            pos["correct" if on_gold else "wrong cite"] += 1
        elif not shown:
            pos["retrieval miss"] += 1
        elif r["reason"] == "no_valid_citation":
            pos["quote failure"] += 1
        else:
            pos["model missed"] += 1

    if unmatched:
        print(f"WARNING: {unmatched} rows did not match; check --contracts and --seed match the run\n")

    def show(title, counts, n, order, unit="queries"):
        print(f"{title} ({n} {unit})")
        for k in order:
            v = counts.get(k, 0)
            print(f"  {k:<16}{v:>5}  {v / n:6.1%}" if n else f"  {k:<16}{v:>5}")
        print()

    show("Clause exists", pos, n_pos, ["correct", "wrong cite", "retrieval miss", "quote failure", "model missed"])
    show("Clause absent", neg, n_neg, ["correct abstain", "false answer"])
    total = sum(cit.values())
    show("Citations", cit, total, ["valid", "quote_not_found", "unknown_chunk", "quote_too_short"], "citations")


if __name__ == "__main__":
    main()
