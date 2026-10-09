"""Retrieval ablation on CUAD: one row per config, same contracts, same queries.

    python run_eval.py --cuad data/cuad/test.json --configs configs/*.yaml --contracts 25

Writes results/<config>.json and results/summary.md. Only answerable queries
are scored here (a query with no gold span has nothing to retrieve);
unanswerable queries are used by run_answer.py to test abstention.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rag.config import load_config  # noqa: E402
from rag.data import load_cuad  # noqa: E402
from rag.metrics import aggregate, score_query  # noqa: E402
from rag.pipeline import build_index  # noqa: E402


def run_config(cfg: dict, contracts, queries) -> dict:
    index = build_index(contracts, cfg)
    k = cfg["k"]
    results, by_cat = [], defaultdict(list)
    for q in queries:
        t0 = time.perf_counter()
        hits = index.retriever.retrieve(q.text, q.contract_id, k)
        ms = (time.perf_counter() - t0) * 1000
        r = score_query(q.id, q.gold_spans, [h.chunk for h in hits], ms)
        results.append(r)
        by_cat[q.category].append(r)
    return {
        "config": cfg,
        "retriever": index.retriever.name,
        "chunks_indexed": len(index.chunks),
        "index_build_seconds": round(index.build_seconds, 3),
        "metrics": aggregate(results),
        "by_category": {c: aggregate(rs) for c, rs in sorted(by_cat.items())},
        "per_query": [r.__dict__ for r in results],
    }


def summary_table(rows: list[dict], meta: dict) -> str:
    lines = [
        "# Retrieval ablation on CUAD",
        "",
        f"Run {meta['run_at']} on {meta['contracts']} contracts (seed {meta['seed']}), "
        f"{meta['queries']} answerable queries, file `{meta['cuad_file']}`.",
        "",
        "| Config | Retriever | k | Hit@k | Span recall@k | MRR | Est. context tokens / query | p95 retrieval ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m, c = r["metrics"], r["config"]
        lines.append(
            f"| {c['name']} | `{r['retriever']}` | {c['k']} | {m['hit']:.3f} | {m['span_recall']:.3f} | "
            f"{m['mrr']:.3f} | {m['est_context_tokens_mean']:.0f} | {m['latency_ms_p95']:.1f} |"
        )
    lines += [
        "",
        "Hit@k: any top-k item overlaps a lawyer-annotated span. Span recall@k: share of annotated spans touched. "
        "Context tokens are estimated as characters / 4; exact token counts come from run_answer.py.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuad", required=True, help="path to CUAD test.json (SQuAD format)")
    ap.add_argument("--configs", nargs="+", required=True)
    ap.add_argument("--contracts", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    contracts, queries = load_cuad(args.cuad, args.contracts, args.seed)
    answerable = [q for q in queries if q.answerable]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "contracts": len(contracts),
        "seed": args.seed,
        "queries": len(answerable),
        "cuad_file": Path(args.cuad).name,
        "python": platform.python_version(),
    }

    rows = []
    for path in args.configs:
        cfg = load_config(path)
        print(f"running {cfg['name']} ...", flush=True)
        row = run_config(cfg, contracts, answerable)
        row["meta"] = meta
        (out / f"{cfg['name']}.json").write_text(json.dumps(row, indent=2))
        m = row["metrics"]
        print(f"  hit@{cfg['k']}={m['hit']:.3f} span_recall={m['span_recall']:.3f} mrr={m['mrr']:.3f}")
        rows.append(row)

    (out / "summary.md").write_text(summary_table(rows, meta))
    print(f"wrote {out / 'summary.md'}")


if __name__ == "__main__":
    main()
