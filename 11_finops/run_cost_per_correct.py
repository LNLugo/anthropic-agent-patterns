"""Cost per correct answer, from runs already recorded against real models.

    python run_cost_per_correct.py        # writes results/cost_per_correct.json

Cost per question says what a call costs. Cost per correct answer says what a
right answer costs, which is the number to compare options on: a cheaper
option that is wrong more often can cost more per right answer.

Inputs, none of them re-run here:
  * 08's model router on three question sets (08_tools/results_published.json):
    mean tokens per question and how many routed correctly, for gpt-6-luna with
    and without the coverage check, and gpt-4o-mini
  * 08's rules router on the same sets, scored here offline (it makes no model call)
  * 07's answer evaluation, 300 CUAD queries on gpt-4o-mini (inputs/07_answer_eval.json)

Prices come only from prices.yaml. The script also checks that the file
reproduces the costs recorded when 08 was run with prices typed on the command line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "08_tools"))

from finops import PriceBook, Unpriced  # noqa: E402

PUBLISHED = ROOT / "08_tools" / "results_published.json"
FIXTURES = ROOT / "08_tools" / "tests" / "fixtures"


def rules_correct(file: str) -> tuple[int, int]:
    from agent_tools.evaluate import evaluate

    r = evaluate(FIXTURES / file)
    return r.routed_correctly, r.total


def router_rows(book: PriceBook) -> tuple[list[dict], list[dict]]:
    published = json.loads(PUBLISHED.read_text())
    rows, checks = [], []
    for set_name, s in published["sets"].items():
        correct, total = rules_correct(s["file"])
        rows.append({"set": set_name, "option": "rules", "model": None, "runs": 1, "total": total,
                     "correct": [correct], "cost_per_question": 0.0, "cost_per_correct": 0.0})
        for label, m in s["models"].items():
            model = label.split(",")[0].strip()
            per_q, per_correct, correct_runs = [], [], []
            for run in m["runs"]:
                try:
                    c = book.cost(model, run["tokens_in"], run["tokens_out"])
                except Unpriced:
                    c = None
                per_q.append(c)
                correct_runs.append(run["routed"])
                per_correct.append(None if c is None or not run["routed"] else c * run["total"] / run["routed"])
                if c is not None and run.get("cost_per_question_usd") is not None:
                    recorded = run["cost_per_question_usd"]
                    checks.append({"set": set_name, "option": label, "recorded": recorded, "from_file": c,
                                   "relative_difference": abs(c - recorded) / recorded})
            priced = None not in per_q
            rows.append({"set": set_name, "option": label, "model": model, "runs": len(m["runs"]),
                         "total": m["runs"][0]["total"], "correct": correct_runs,
                         "cost_per_question": mean(per_q) if priced else None,
                         "cost_per_correct": mean(per_correct) if priced and None not in per_correct else None})
    return rows, checks


def versus_rules(rows: list[dict]) -> list[dict]:
    """Rules cost nothing per call but get more wrong, so cost per correct answer alone
    would always favour them. This is what each extra correct answer costs over rules."""
    out = []
    for r in rows:
        if r["option"] == "rules" or r["cost_per_question"] is None:
            continue
        rules = next(x for x in rows if x["set"] == r["set"] and x["option"] == "rules")
        gain = (mean(r["correct"]) - rules["correct"][0]) / r["total"]   # extra correct answers per question
        out.append({"set": r["set"], "option": r["option"], "accuracy_gain": gain,
                    "cost_per_extra_correct": r["cost_per_question"] / gain if gain > 0 else None,
                    "cost_per_million_questions": r["cost_per_question"] * 1_000_000})
    return out


def rag_row(book: PriceBook) -> dict:
    e = json.loads((HERE / "inputs" / "07_answer_eval.json").read_text())
    per_q = book.cost(e["model"], e["input_tokens_per_query"], e["output_tokens_per_query"])
    strict = sum(e["correct"].values())
    lenient = strict + sum(e["lenient_also_correct"].values())
    return {"set": "07 CUAD answers", "model": e["model"], "queries": e["queries"], "correct_strict": strict,
            "correct_lenient": lenient, "cost_per_question": per_q,
            "cost_per_correct_strict": per_q * e["queries"] / strict,
            "cost_per_correct_lenient": per_q * e["queries"] / lenient,
            "spend_on_wrong_strict_share": 1 - strict / e["queries"]}


def coverage_check_price(rows: list[dict]) -> list[dict]:
    """What the coverage check costs per question, and what it does to cost per correct answer."""
    out = []
    for set_name in ("heldout", "coverage"):
        off = next(r for r in rows if r["set"] == set_name and r["option"] == "gpt-6-luna, check off")
        on = next(r for r in rows if r["set"] == set_name and r["option"] == "gpt-6-luna, check on")
        out.append({"set": set_name,
                    "per_question_change": on["cost_per_question"] / off["cost_per_question"] - 1,
                    "per_correct_change": on["cost_per_correct"] / off["cost_per_correct"] - 1,
                    "correct_off": off["correct"], "correct_on": on["correct"]})
    return out


def usd(x: float | None, digits: int = 6) -> str:
    return "unpriced" if x is None else f"${x:.{digits}f}"


def main() -> int:
    book = PriceBook.load()
    rows, checks = router_rows(book)
    rag = rag_row(book)
    cover = coverage_check_price(rows)
    vs = versus_rules(rows)

    print("08 routing: cost per question and per correct route (mean over runs)")
    print(f"  {'set':12} {'option':24} {'correct':>14} {'per question':>13} {'per correct':>13}")
    for r in rows:
        correct = ", ".join(f"{c}/{r['total']}" for c in r["correct"])
        print(f"  {r['set']:12} {r['option']:24} {correct:>14} {usd(r['cost_per_question']):>13} {usd(r['cost_per_correct']):>13}")
    print("\nAgainst the free rules router: what each extra correct route costs")
    for v in vs:
        extra = usd(v["cost_per_extra_correct"]) if v["cost_per_extra_correct"] else "no gain"
        print(f"  {v['set']:12} {v['option']:24} accuracy {v['accuracy_gain']:+.0%}, {extra} per extra correct,"
              f" ${v['cost_per_million_questions']:.0f} per million questions")
    print("\nThe coverage check (gpt-6-luna):")
    for c in cover:
        print(f"  {c['set']:9} per question {c['per_question_change']:+.1%}, per correct route {c['per_correct_change']:+.1%}"
              f"  (correct off {c['correct_off']}, on {c['correct_on']})")
    print(f"\n07 answers ({rag['model']}, {rag['queries']} queries): {usd(rag['cost_per_question'])} per query, "
          f"{usd(rag['cost_per_correct_strict'])} per correct answer ({rag['correct_strict']} correct), "
          f"{usd(rag['cost_per_correct_lenient'])} lenient ({rag['correct_lenient']})")
    worst = max(c["relative_difference"] for c in checks)
    print(f"\nPrice file vs costs recorded in 08: {len(checks)} runs, largest difference {worst:.1%}"
          " (the recorded costs used exact token totals; the file is applied to rounded means)")

    result = {"prices": {m: {"input": p.input, "cached_input": p.cached_input, "output": p.output,
                             "source": p.source, "checked": p.checked.isoformat()} for m, p in book.prices.items()},
              "routing": rows, "versus_rules": vs, "coverage_check": cover, "rag_answers": rag, "reproduces_recorded_costs": checks}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "cost_per_correct.json").write_text(json.dumps(result, indent=2) + "\n")
    print("\nWrote results/cost_per_correct.json")
    return 0 if worst < 0.02 else 1


if __name__ == "__main__":
    raise SystemExit(main())
