"""Model router vs. the rules baseline, on the same labeled questions.

    python run_model_router_eval.py                                  # LLM_PROVIDER, default openai / gpt-6-luna
    python run_model_router_eval.py --input-price 0.15 --output-price 0.60
    python run_model_router_eval.py --provider anthropic --model claude-haiku-5-5

The model picks one of three tools (count_jira_issues, search_wiki,
ask_clarification); the tool it picks is the route, and its arguments go
through the same validator as the rules. Prints both results side by side and
writes one record per question to results/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # llm_provider.py at the repo root

from llm_provider import get_chat_model  # noqa: E402

from agent_tools.evaluate import evaluate, evaluate_model  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.model_router import ModelRouter  # noqa: E402

QUESTIONS = HERE / "tests" / "fixtures" / "router_questions.json"
JIRA = HERE / "tests" / "fixtures" / "mini_jira.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("--provider", default=None, help="openai or anthropic (default: $LLM_PROVIDER, then openai)")
    ap.add_argument("--model", default=None, help="defaults to $OPENAI_MODEL or $CLAUDE_MODEL")
    ap.add_argument("--input-price", type=float, default=None, help="USD per million input tokens")
    ap.add_argument("--output-price", type=float, default=None, help="USD per million output tokens")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    args = ap.parse_args()

    llm = get_chat_model(args.provider, args.model)
    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", "unknown")
    jira, _ = MockJira.from_fixture(JIRA)
    try:
        rules = evaluate(args.questions)
    except ValueError as e:
        print(f"Cannot score {args.questions.name}: {e}")
        return 1
    model = evaluate_model(args.questions, ModelRouter(llm, BoundedJiraTool(jira)))

    n = model.total
    latencies = [r["latency_s"] for r in model.rows]
    print(f"Model: {model_name}   Questions: {args.questions.name}\n")
    print(f"{'':28}{'Rules':>12}{'Model':>12}")
    print(f"{'Routed correctly':28}{f'{rules.routed_correctly}/{n}':>12}{f'{model.routed_correctly}/{n}':>12}")
    print(f"{'Query extracted exactly':28}{f'{rules.extracted_correctly}/{rules.extraction_total}':>12}"
          f"{f'{model.extracted_correctly}/{model.extraction_total}':>12}")
    print(f"{'Rejected by the bound':28}{'n/a':>12}{len(model.rejected_by_bound):>12}")
    print(f"{'Tokens per question (in/out)':28}{'0':>12}{f'{model.input_tokens // n}/{model.output_tokens // n}':>12}")
    print(f"{'Latency p50 / max (s)':28}{'~0':>12}"
          f"{f'{statistics.median(latencies):.2f}/{max(latencies):.2f}':>12}")
    cost = None
    if args.input_price is not None and args.output_price is not None:
        cost = model.cost_usd(args.input_price, args.output_price)
        print(f"{'Cost per question (USD)':28}{'0':>12}{cost / n:>12.6f}")

    for title, items in (("Misroutes", model.misroutes), ("Extraction misses", model.extraction_misses),
                         ("Rejected by the bound", model.rejected_by_bound)):
        if items:
            print(f"\n{title}:")
            for item in items:
                print("  " + item[0] + ("" if len(item) < 2 else f"\n      {' -> '.join(item[1:])}"))

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out / f"model_router_{str(model_name).replace('/', '_')}_{args.questions.stem}_{stamp}.json"
    out.write_text(json.dumps({
        "model": model_name, "run_at": datetime.now().isoformat(timespec="seconds"),
        "questions": str(args.questions.name),
        "rules": {"routed": rules.routed_correctly, "extracted": rules.extracted_correctly,
                  "extraction_total": rules.extraction_total},
        "model_result": {"routed": model.routed_correctly, "extracted": model.extracted_correctly,
                         "rejected_by_bound": len(model.rejected_by_bound),
                         "input_tokens": model.input_tokens, "output_tokens": model.output_tokens,
                         "latency_p50_s": round(statistics.median(latencies), 3), "cost_usd": cost},
        "rows": list(model.rows),
    }, indent=2), encoding="utf-8")
    print(f"\nPer question results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
