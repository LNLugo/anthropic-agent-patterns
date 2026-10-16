"""Cost per correct answer from runs recorded against real models (no model is called)."""

import pytest

import run_cost_per_correct as cpc
from finops import PriceBook


@pytest.fixture(scope="module")
def results():
    book = PriceBook.load()
    rows, checks = cpc.router_rows(book)
    return {"rows": rows, "checks": checks, "vs": cpc.versus_rules(rows), "cover": cpc.coverage_check_price(rows),
            "rag": cpc.rag_row(book)}


def row(results, set_name, option):
    return next(r for r in results["rows"] if r["set"] == set_name and r["option"] == option)


def test_the_price_file_reproduces_every_cost_recorded_in_08(results):
    assert len(results["checks"]) == 9
    assert max(c["relative_difference"] for c in results["checks"]) < 0.01


def test_gpt_4o_mini_is_now_priced_from_the_file(results):
    """08 recorded no cost for gpt-4o-mini; the file prices it."""
    mini = row(results, "heldout", "gpt-4o-mini")
    assert mini["cost_per_question"] == pytest.approx(PriceBook.load().cost("gpt-4o-mini", 644, 42))


def test_on_held_out_questions_luna_is_both_cheaper_and_more_accurate_than_4o_mini(results):
    luna, mini = row(results, "heldout", "gpt-6-luna, check off"), row(results, "heldout", "gpt-4o-mini")
    assert luna["cost_per_question"] < mini["cost_per_question"]
    assert min(luna["correct"]) >= max(mini["correct"])


def test_rules_are_free_but_each_extra_correct_route_has_a_price(results):
    assert row(results, "heldout", "rules")["correct"] == [9]
    on = next(v for v in results["vs"] if v["set"] == "heldout" and v["option"] == "gpt-6-luna, check on")
    assert on["accuracy_gain"] == pytest.approx(0.5)
    assert on["cost_per_extra_correct"] == pytest.approx(2 * row(results, "heldout", "gpt-6-luna, check on")["cost_per_question"])


def test_where_the_model_is_no_better_than_rules_no_price_is_claimed(results):
    off = next(v for v in results["vs"] if v["set"] == "coverage" and v["option"] == "gpt-6-luna, check off")
    assert off["accuracy_gain"] < 0 and off["cost_per_extra_correct"] is None


def test_the_coverage_check_costs_more_per_question_and_less_per_correct_route(results):
    c = next(c for c in results["cover"] if c["set"] == "coverage")
    assert c["per_question_change"] > 0 and c["per_correct_change"] < 0


def test_rag_cost_per_correct_answer(results):
    rag = results["rag"]
    per_q = 1190 * 0.15e-6 + 100 * 0.60e-6
    assert rag["cost_per_question"] == pytest.approx(per_q)
    assert rag["correct_strict"] == 200 and rag["cost_per_correct_strict"] == pytest.approx(per_q * 300 / 200)
