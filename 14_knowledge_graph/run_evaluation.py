"""Measure vector-only against graph-expanded retrieval on the questions' expected evidence.

    python run_evaluation.py                     # offline: the hashing stand-in, in memory
    python run_evaluation.py --live              # OpenAI embeddings, Upstash Vector and Redis (keys needed)
    python run_evaluation.py --out results/x.json

Offline, the embedding model is a lexical stand-in, so the offline numbers compare the
retrievers over a word-matching baseline, not over a real model. --live repeats the same
runs with text-embedding-3-small, with the vectors in Upstash Vector and the graph read
back from Upstash Redis.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent / "10_observability"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from netgraph.corpus import load, load_questions  # noqa: E402
from netgraph.evaluate import configs, score, summarise  # noqa: E402
from netgraph.graph import build  # noqa: E402
from netgraph.retrieve import Retriever  # noqa: E402
from netgraph.stores import InMemoryGraphStore, InMemoryVectorStore  # noqa: E402

K = 10


def setup(live: bool):
    from llm_provider import get_embeddings

    items = load()
    graph = build(items)
    if live:
        from netgraph.stores import UpstashGraphStore, UpstashVectorStore

        store, gstore, embedder = UpstashVectorStore(), UpstashGraphStore(), get_embeddings("openai")
    else:
        store, gstore, embedder = InMemoryVectorStore(), InMemoryGraphStore(), get_embeddings("scripted")
    gstore.save(graph)
    graph = gstore.load()                      # retrieval walks the graph as the store returns it
    r = Retriever(items, store, embedder, graph)
    if live:
        store.reset()
    t0 = time.perf_counter()
    r.index()
    return r, getattr(embedder, "model", "unknown"), time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    from tracing import Tracer, set_tracer

    set_tracer(Tracer(exporters=[]))           # the evaluation's own spans are not kept
    r, model, index_s = setup(args.live)
    questions = load_questions()
    report = {"measured": date.today().isoformat(), "embedding_model": model, "k": K,
              "store": "upstash" if args.live else "memory", "index_seconds": round(index_s, 1), "retrievers": {}}
    for name, run in configs(r, K).items():
        scores = score(run, questions)
        report["retrievers"][name] = {"summary": summarise(scores), "questions": {
            s.qid: {"found": s.found, "expected": s.expected, "missed": s.missed, "forbidden": s.forbidden}
            for s in scores}}
    out = args.out or HERE / "results" / ("retrieval_live.json" if args.live else "retrieval.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{'retriever':18} {'split':12} {'multi-hop':>11} {'single-hop':>11} {'precision':>9} forbidden")
    for name, res in report["retrievers"].items():
        for split, s in res["summary"].items():
            m, sh = s["multi_hop"], s["single_hop"]
            print(f"{name:18} {split:12} {m['found']:>4}/{m['expected']:<6} {sh['found']:>4}/{sh['expected']:<6} "
                  f"{s['all']['precision']:>9} {s['all']['forbidden']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
