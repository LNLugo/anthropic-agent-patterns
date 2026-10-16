"""End-to-end answer eval (Claude or OpenAI): citations, abstention, tokens, latency, cost.

    export OPENAI_API_KEY=...         # or ANTHROPIC_API_KEY with --provider anthropic
    python run_answer.py --cuad data/cuad/test.json --config configs/06_bm25_rerank.yaml \
        --provider anthropic --contracts 10 --queries 60 \
        --input-price <usd per Mtok> --output-price <usd per Mtok>

Samples a balanced set of answerable and unanswerable queries. Reports:
  answer rate on answerable queries      (did it find the clause when it exists)
  abstain rate on unanswerable queries   (did it refuse when the clause is absent)
  citation validity                      (citations that resolve to shown text)
  citation precision                     (valid citations overlapping gold spans)
  tokens, p50/p95 latency, and cost per query when prices are supplied

Prices are arguments on purpose: check the current Anthropic pricing page
rather than trusting a number baked into code.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent))

from rag.citations import citation_precision  # noqa: E402
from rag.config import load_config  # noqa: E402
from rag.data import load_cuad  # noqa: E402
from rag.graph import PROVIDERS, answer_query, build_graph, make_llm  # noqa: E402
from rag.ledger import ContextLedger  # noqa: E402
from rag.metrics import _pct  # noqa: E402
from rag.pipeline import build_index  # noqa: E402


def model_name(provider: str, model: str | None) -> str:
    """The model make_llm calls, resolved the same way, so the price file can name it."""
    if provider == "anthropic":
        return model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    return model or os.environ.get("OPENAI_MODEL", "gpt-6-luna")


def priced(model, ledger):
    """The ledger's cost from 11_finops/prices.yaml, or None (unpriced) for a model not in it."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "11_finops"))
    from finops import PriceBook, Unpriced
    try:
        return PriceBook.load().cost(model, ledger.input_tokens, ledger.output_tokens)
    except Unpriced:
        return None


def sample_balanced(queries, n: int, seed: int):
    rng = random.Random(seed)
    pos = [q for q in queries if q.answerable]
    neg = [q for q in queries if not q.answerable]
    half = n // 2
    return rng.sample(pos, min(half, len(pos))) + rng.sample(neg, min(n - half, len(neg)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuad", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--contracts", type=int, default=10)
    ap.add_argument("--queries", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="openai")
    ap.add_argument("--model", default=None, help="defaults to $CLAUDE_MODEL or $OPENAI_MODEL")
    ap.add_argument("--input-price", type=float, default=None, help="USD per million input tokens")
    ap.add_argument("--output-price", type=float, default=None, help="USD per million output tokens")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    cfg = load_config(args.config)
    contracts, queries = load_cuad(args.cuad, args.contracts, args.seed)
    index = build_index(contracts, cfg)
    graph = build_graph(index.retriever, make_llm(args.provider, args.model), k=cfg["k"])
    tag = f"{cfg['name']}_{args.provider}"
    sample = sample_balanced(queries, args.queries, args.seed)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / f"answers_{tag}.jsonl"
    rows = []
    with ledger_path.open("w") as fh:
        for i, q in enumerate(sample, 1):
            ledger = ContextLedger(q.id)
            final = answer_query(graph, q, ledger, group_id=tag)
            checked = final.get("checked", [])
            row = {
                "query_id": q.id,
                "category": q.category,
                "answerable": q.answerable,
                "status": final["status"],
                "answer": final.get("answer", ""),
                "reason": final.get("reason", ""),
                "citations": [
                    {"chunk_id": c.citation.chunk_id, "status": c.status, "span": c.span} for c in checked
                ],
                "citation_precision": citation_precision(checked, q.gold_spans) if q.answerable else None,
                "cost_usd": ledger.cost(args.input_price, args.output_price) if args.input_price is not None
                            else priced(model_name(args.provider, args.model), ledger),
                "ledger": ledger.to_dict(),
            }
            fh.write(json.dumps(row) + "\n")
            rows.append(row)
            print(f"[{i}/{len(sample)}] {q.id} -> {row['status']}", flush=True)

    pos = [r for r in rows if r["answerable"]]
    neg = [r for r in rows if not r["answerable"]]
    cits = [c for r in rows for c in r["citations"]]
    precs = [r["citation_precision"] for r in pos if r["citation_precision"] is not None]
    lat = [r["ledger"]["latency_ms"] for r in rows]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    summary = {
        "config": cfg["name"],
        "provider": args.provider,
        "model": args.model or ("$CLAUDE_MODEL" if args.provider == "anthropic" else "$OPENAI_MODEL or gpt-6-luna"),
        "queries": len(rows),
        "answer_rate_on_answerable": mean(r["status"] == "answered" for r in pos) if pos else None,
        "abstain_rate_on_unanswerable": mean(r["status"] == "abstained" for r in neg) if neg else None,
        "citation_validity": mean(c["status"] == "valid" for c in cits) if cits else None,
        "citation_precision_mean": mean(precs) if precs else None,
        "input_tokens_mean": mean(r["ledger"]["input_tokens"] for r in rows),
        "output_tokens_mean": mean(r["ledger"]["output_tokens"] for r in rows),
        "latency_ms_p50": _pct(lat, 0.5),
        "latency_ms_p95": _pct(lat, 0.95),
        "cost_usd_per_query": mean(costs) if costs else None,
    }
    (out / f"answers_{tag}_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
