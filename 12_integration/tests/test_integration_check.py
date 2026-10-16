"""The measured night: each guarantee against a control without it."""

import pytest

import run_integration_check as night


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    for name, kw in (("design", {}), ("no_store", {"dedupe": False}), ("event_key", {"key_on": "event"}),
                     ("no_marker", {"marker": False})):
        out[name] = night.night(tmp_path_factory.mktemp(name), **kw)
    return out


def test_four_outages_become_four_requests_and_four_comments(runs):
    d = runs["design"]
    assert (d["approval_requests"], d["comments_posted"]) == (4, 4)
    assert d["crashed_after"] and d["published"] == 60 and d["work_orders_loaded"] == 38


def test_every_handler_run_and_jira_query_is_accounted_for(runs):
    d = runs["design"]
    assert d["handler_runs"] == 48 + d["retries"] + 1        # every message, each retry, the redelivery after the crash
    assert d["jira_searches"] == 4 + 2                         # one count per outage, plus the two timeouts
    assert d["duplicates_skipped"] == 12 and d["retries"] == 2


def test_the_bad_inputs_are_dead_lettered_with_their_reasons(runs):
    reasons = runs["design"]["dead_letters"]
    assert reasons[:2] == ["line 13: unknown status 'XX'", "line 25: opened date '20261399' is not a real date"]
    assert reasons[2] == "halted by guardrail restricted_project"
    assert "BILL is outside this assistant's scope" in reasons[3] and len(reasons) == 4


def test_without_the_store_the_answer_is_the_same_and_only_work_is_repeated(runs):
    """The incident key, not the store, is what keeps triage to one per outage."""
    d, c = runs["design"], runs["no_store"]
    assert (c["approval_requests"], c["comments_posted"]) == (4, 4)
    assert c["handler_runs"] == d["handler_runs"] + d["duplicates_skipped"] and c["jira_searches"] == d["jira_searches"]


def test_keyed_on_events_two_sources_double_the_work_for_people_and_jira(runs):
    c = runs["event_key"]
    assert (c["approval_requests"], c["comments_posted"]) == (8, 8) and c["jira_searches"] == 10


def test_without_the_marker_one_lost_response_posts_twice(runs):
    c = runs["no_marker"]
    assert (c["approval_requests"], c["comments_posted"]) == (4, 5)
