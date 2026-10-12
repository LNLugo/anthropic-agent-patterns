from datetime import date
from pathlib import Path

import pytest

from agent_tools.evaluate import evaluate
from agent_tools.router import NeedsClarification, classify, extract, time_window

QUESTIONS = Path(__file__).parent / "fixtures" / "router_questions.json"
TODAY = date(2026, 10, 12)


@pytest.mark.parametrize("q, route", [
    ("How many P1 incidents did the billing platform have last quarter?", "aggregation"),
    ("List open incidents for payments this month", "aggregation"),
    ("How do I rotate the billing API credentials?", "knowledge"),
    ("How many steps are in the deploy pipeline?", "knowledge"),       # counting cue, no issue cue
    ("What is the process for declaring a P1 incident?", "knowledge"),  # issue cue, no counting cue
])
def test_classify_needs_both_cues(q, route):
    assert classify(q) == route


def test_router_accuracy_on_labeled_set():
    """A measured baseline, not a target the rules were tuned to. The misses are
    the labeled hard cases, and they are what a model based router must beat."""
    report = evaluate(QUESTIONS)
    assert report.router_accuracy >= 0.85, report.misroutes
    assert report.extraction_accuracy >= 0.85, report.extraction_misses


# ---------------------------------------------------------------- dates

@pytest.mark.parametrize("phrase, start, before", [
    ("last quarter", date(2026, 7, 1), date(2026, 10, 1)),
    ("this quarter", date(2026, 10, 1), date(2026, 10, 13)),
    ("last month", date(2026, 9, 1), date(2026, 10, 1)),
    ("this month", date(2026, 10, 1), date(2026, 10, 13)),
    ("Q2 2026", date(2026, 4, 1), date(2026, 7, 1)),
    ("Q4", date(2026, 10, 1), date(2027, 1, 1)),
    ("last 30 days", date(2026, 9, 13), date(2026, 10, 13)),
    ("this year", date(2026, 1, 1), date(2026, 10, 13)),
    ("in 2025", date(2025, 1, 1), date(2026, 1, 1)),
])
def test_time_windows(phrase, start, before):
    got_start, got_before, note = time_window(f"incidents {phrase}", TODAY)
    assert (got_start, got_before, note) == (start, before, None)


def test_last_quarter_crosses_the_year_boundary():
    start, before, _ = time_window("last quarter", date(2026, 1, 15))
    assert (start, before) == (date(2025, 10, 1), date(2026, 1, 1))


def test_missing_period_defaults_and_says_so():
    start, before, note = time_window("open incidents in payments", TODAY)
    assert (before - start).days == 90 and "90 days" in note


# ---------------------------------------------------------------- extraction

def test_headline_question():
    ex = extract("How many P1 incidents did the billing platform have last quarter?", TODAY)
    s = ex.spec
    assert (s.project, s.issue_type, s.priority) == ("BILL", "Incident", "P1")
    assert (s.created_from, s.created_before) == (date(2026, 7, 1), date(2026, 10, 1))
    assert ex.assumptions == []


def test_bare_priority_is_read_as_incidents_and_noted():
    ex = extract("Any open P1s in billing this quarter?", TODAY)
    assert ex.spec.issue_type == "Incident"
    assert any("P1" in a for a in ex.assumptions)


def test_missing_project_asks_instead_of_guessing():
    with pytest.raises(NeedsClarification, match="Which project"):
        extract("How many P1 incidents last quarter?", TODAY)
