"""Budgets per identity, checked before every model call, measured from the trace."""

from datetime import date

import pytest

import run_budget_check as rb
from finops import BudgetPolicy, PriceBook, SpendMeter

DAY = date(2026, 10, 12)


def gen_span(tid, model="gpt-6-luna", i=728, o=39):
    return {"type": "span", "trace_id": tid, "kind": "generation", "name": "router",
            "data": {"model": model, "input_tokens": i, "output_tokens": o}}


def trace(tid, **metadata):
    return {"type": "trace", "trace_id": tid, "workflow": "assistant", "metadata": metadata}


@pytest.fixture
def meter():
    return SpendMeter(PriceBook.load(), today=lambda: DAY)


def test_the_meter_prices_each_call_against_whoever_the_run_was_for(meter):
    one = PriceBook.load().cost("gpt-6-luna", 728, 39)
    for tid, who in (("t1", "ana"), ("t2", "ana"), ("t3", "bo")):
        meter.export(trace(tid, caller=who))
        meter.export(gen_span(tid))
    assert meter.spent("ana") == pytest.approx(2 * one) and meter.spent("bo") == pytest.approx(one)


def test_an_mcp_call_is_charged_to_the_token_subject(meter):
    meter.export(trace("t1", transport="mcp"))
    meter.export({"type": "span", "trace_id": "t1", "kind": "guardrail", "name": "token_scope",
                  "data": {"subject": "new_hire"}})
    meter.export(gen_span("t1"))
    assert meter.spent("new_hire") > 0 and meter.spent("(unknown)") == 0


def test_the_policy_allows_warns_then_refuses(meter):
    one = PriceBook.load().cost("gpt-6-luna", 728, 39)
    policy = BudgetPolicy(meter, {"ana": 3 * one}, reserve=one, warn_at=0.8)
    meter.export(trace("t", caller="ana"))
    verdicts = []
    for _ in range(4):
        behavior, message, info = policy.check("ana", "gpt-6-luna")
        verdicts.append((behavior, info.get("warning")))
        if behavior == "allow":
            meter.export(gen_span("t"))
    assert verdicts == [("allow", False), ("allow", False), ("allow", True), ("reject_content", None)]
    assert meter.spent("ana") <= 3 * one + 1e-12  # never over the limit


def test_no_limit_means_no_check(meter):
    assert BudgetPolicy(meter, {"ana": 1.0}).check("bo", "anything")[0] == "allow"


def test_an_unpriced_model_is_refused_before_it_is_called(meter):
    behavior, message, info = BudgetPolicy(meter, {"ana": 1.0}).check("ana", "claude-sonnet-5-5")
    assert behavior == "reject_content" and info["reason"] == "model_unpriced" and "no price" in message


def test_unpriced_spend_earlier_today_stops_further_calls(meter):
    meter.export(trace("t", caller="ana"))
    meter.export(gen_span("t", model="some-new-model"))
    behavior, _, info = BudgetPolicy(meter, {"ana": 1.0}).check("ana", "gpt-6-luna")
    assert behavior == "reject_content" and info["reason"] == "spend_unknown"


def test_the_budget_resets_the_next_day():
    clock = rb.Clock(DAY)
    meter = SpendMeter(PriceBook.load(), today=clock)
    meter.export(trace("t", caller="ana"))
    meter.export(gen_span("t"))
    policy = BudgetPolicy(meter, {"ana": 0.0001}, reserve=0.0001)
    assert policy.check("ana", "gpt-6-luna")[0] == "reject_content"
    clock.day = date(2026, 10, 13)
    assert policy.check("ana", "gpt-6-luna")[0] == "allow"


# ---------------------------------------------------------------- the measured scenarios, through 08

@pytest.fixture(autouse=True)
def _restore_tracer():
    from tracing import set_tracer

    yield
    set_tracer(None)


def test_a_daily_budget_stops_at_the_expected_question_and_calls_nothing_after():
    d, control = rb.daily_limit(True), rb.daily_limit(False)
    # $0.001 a day at $0.0000923 a call, reserving one call: 10 fit, the 11th would not
    assert (d["billing_oncall_answered"], d["first_refused_at"], d["billing_oncall_refused"]) == (10, 11, 2)
    assert d["model_calls"] == 12 and control["model_calls"] == 14   # 10 + new_hire's 2, against 12 + 2
    assert d["billing_oncall_spent"] <= 0.001 and d["warned_runs"] == [9, 10]
    assert d["new_hire_answered"] == 2 and d["next_day"] == "answered"


def test_a_budget_stops_a_runaway_loop_mid_run():
    b, control = rb.runaway_loop(True), rb.runaway_loop(False)
    assert (b["status"], b["turns"], b["model_calls"]) == ("budget", 3, 2)
    assert (control["status"], control["model_calls"]) == ("max_turns", 4)


def test_switching_to_an_unpriced_model_is_refused_with_no_call():
    u = rb.unpriced_model()
    assert u["stop_reason"] == "budget" and u["model_calls"] == 0
