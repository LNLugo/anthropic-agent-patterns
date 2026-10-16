"""Which history does the model see, and what does it cost, as a conversation grows?

    python run_memory_benchmark.py          # writes results/memory_benchmark.json

Builds conversations of 10 to 240 exchanges: six things the user establishes at
the start (fixtures/memory_benchmark.json), then filler questions that reuse the
same projects, months and priorities as distractors, then the six later
questions, each needing one earlier exchange. For each strategy and length:

    recall           the text the question needs (the earlier answer, or the instruction)
                     is in the context built for it, verbatim or in a summary
    context tokens   what the strategy puts in front of the model per question
                     (estimated at 4 characters a token, the same for every strategy)
    context cost     those tokens at gpt-6-luna's input price, from 11_finops/prices.yaml

No model is called. The compaction summary is a deterministic stand-in (first
sentences, newest first, within a token budget); a model summarizer can do
better or worse and is measured only in a live run.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT / "10_observability", ROOT / "11_finops"):
    sys.path.insert(0, str(p))

from finops import PriceBook  # noqa: E402
from memory import InMemorySession  # noqa: E402
from memory.strategies import (append_all, compact, context_tokens, is_standing, retrieve,  # noqa: E402
                               retrieve_pinned, window)

BENCH = json.loads((HERE / "fixtures" / "memory_benchmark.json").read_text())
LENGTHS = (10, 30, 60, 120, 240)
SEEDS = (11, 12, 13, 14, 15)   # five different filler conversations per length
STRATEGIES = {
    "append all": append_all,
    "last 10 items": window(10),
    "append and compact": compact(keep=6, budget=120),
    "retrieve relevant": retrieve(k=3, recent=4),
    "retrieve relevant + pinned": retrieve_pinned(k=3, recent=4),
}


def filler(n: int, seed: int = 11) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    noise = BENCH["noise"]
    out = []
    for _ in range(n):
        q, a = rng.choice(noise["templates"])
        slots = {"project": rng.choice(noise["projects"]), "month": rng.choice(noise["months"]),
                 "priority": rng.choice(noise["priorities"]), "n": rng.randint(0, 9), "k": rng.randint(1, 4),
                 "station": rng.choice(["114", "127", "203", "305"]), "trend": rng.choice(["up", "down", "flat"])}
        out.append((q.format(**slots), a.format(**slots)))
    return out


def conversation(exchanges: int, seed: int = 11) -> tuple[InMemorySession, dict]:
    """The facts first, filler after, so every fact is as old as the conversation allows."""
    s = InMemorySession(f"bench-{exchanges}", "ana")
    where = {}
    for f in BENCH["facts"]:
        s.add_items([{"role": "user", "content": f["user"], "pinned": is_standing(f["user"])},
                     {"role": "assistant", "content": f["assistant"]}])
        where[f["id"]] = s.get_items(limit=2)[0]["seq"]
    for q, a in filler(max(0, exchanges - len(BENCH["facts"])), seed):
        s.add_items([{"role": "user", "content": q, "pinned": is_standing(q)}, {"role": "assistant", "content": a}])
    return s, where


def run() -> dict:
    book = PriceBook.load()
    results = {}
    for name, strategy in STRATEGIES.items():
        rows = []
        for n in LENGTHS:
            hits, sizes = {f["id"]: 0 for f in BENCH["facts"]}, []
            for seed in SEEDS:
                session, _ = conversation(n, seed)
                history = session.get_items()
                for f in BENCH["facts"]:
                    ctx = strategy(history, f["probe"])
                    hits[f["id"]] += any(f["needs"] in i["content"] for i in ctx)
                    sizes.append(context_tokens(ctx))
            t = mean(sizes)
            rows.append({"exchanges": n, "recall": sum(hits.values()), "of": len(hits) * len(SEEDS), "hits": hits,
                         "context_tokens": round(t), "context_cost_per_1000_questions": book.cost("gpt-6-luna", round(t), 0) * 1000})
        results[name] = rows
    return results


def main() -> int:
    results = run()
    kinds = {f["id"]: f["kind"] for f in BENCH["facts"]}
    print(f"{'':28}" + "".join(f"{n:>10}" for n in LENGTHS) + "   exchanges")
    for name, rows in results.items():
        print(f"{name:28}" + "".join(f"{r['recall']:>7}/{r['of']}" for r in rows) + f"   recall over {len(SEEDS)} conversations")
        print(f"{'':28}" + "".join(f"{r['context_tokens']:>10}" for r in rows) + "   context tokens")
    print(f"\nPer question, summed over all lengths and conversations (of {len(LENGTHS) * len(SEEDS)}):")
    names = list(results)
    print(f"  {'':36}" + "".join(f"{n[:14]:>16}" for n in names))
    for fid, kind in kinds.items():
        print(f"  {fid + ' (' + kind[:18] + ')':36}" + "".join(
            f"{sum(r['hits'][fid] for r in results[n]):>16}" for n in names))
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "memory_benchmark.json").write_text(json.dumps(results, indent=2) + "\n")
    print("\nWrote results/memory_benchmark.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
