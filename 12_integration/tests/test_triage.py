"""An outage event starts triage as a service identity, through 08's guardrails, bound and
approval; the same outage arriving twice, from two sources, is triaged once."""

import json
from datetime import date

import pytest

from agent_tools.comments import CommentStore
from agent_tools.jira_mock import MockJira
from integration.adapters import detail_record, jira_webhook, parse_workorders, service_alert, sign
from integration.delivery import Broker, Consumer, DeadLetters, ProcessedStore
from integration.triage import Triage

FIXTURE = "fixtures/transit_jira.json"
TODAY = date(2026, 10, 12)
SECRET = b"shared-webhook-secret"


@pytest.fixture
def world(tmp_path):
    jira, users = MockJira.from_fixture(FIXTURE)
    store = CommentStore()
    triage = Triage(jira, users, store, tmp_path / "approvals.db", TODAY)
    broker = Broker()
    consumer = Consumer(broker, triage.handlers(), ProcessedStore(tmp_path / "p.db"), DeadLetters(tmp_path / "d.db"))
    return {"triage": triage, "broker": broker, "consumer": consumer, "store": store, "dead": consumer.dead}


def alert(alert_id="A-77", issue_key="ELEV-41", asset="ESC-127-02"):
    return service_alert({"alert_id": alert_id, "asset": asset, "station": "S127", "kind": "escalator_out_of_service",
                          "severity": "major", "raised_at": "2026-10-12T07:41:00Z", "issue_key": issue_key})


def webhook(key="ELEV-41", asset="ESC-127-02"):
    body = json.dumps({"timestamp": 1760300000000, "webhookEvent": "jira:issue_created",
                       "issue": {"key": key, "fields": {"project": {"key": key.split('-')[0]}, "labels": [asset]}}}).encode()
    return jira_webhook(body, sign(SECRET, body), SECRET)


def nightly():
    lines = [detail_record("WO-1001", "ESC-127-02", "S127", "OP", date(2026, 10, 9), "Step chain noise"),
             detail_record("WO-1002", "ESC-127-02", "S127", "CL", date(2026, 9, 30), "Comb plate replaced"),
             detail_record("WO-1003", "ELV-127-01", "S127", "IP", date(2026, 10, 7), "Door operator adjust")]
    return "H20261012\n" + "\n".join(lines) + f"\nT{len(lines):09d}\n"


def test_an_alert_becomes_a_waiting_triage_comment_built_from_all_three_sources(world):
    events, _ = parse_workorders(nightly())
    for e in events + [alert()]:
        world["broker"].publish(e)
    world["consumer"].run_until_idle()
    waiting = world["triage"].pending("triage-ELEV-41")
    assert waiting["requested_by"] == "svc-alert-triage" and waiting["issue_key"] == "ELEV-41"
    assert "Open ELEV incidents in the last 30 days: 3." in waiting["body"]       # ELEV-38, 40, 41
    assert "WO-1001 (Step chain noise)" in waiting["body"] and "WO-1002" not in waiting["body"]  # closed is left out
    assert world["store"].comments == []                                            # nothing posted yet


def test_the_service_cannot_approve_itself_and_a_person_posts_it_once(world):
    world["broker"].publish(alert())
    world["consumer"].run_until_idle()
    assert "cannot approve their own write" in world["triage"].decide("triage-ELEV-41", True, "svc-alert-triage")
    assert world["store"].comments == []


def test_a_person_approves_and_it_posts_as_the_service(world):
    world["broker"].publish(alert())
    world["consumer"].run_until_idle()
    answer = world["triage"].decide("triage-ELEV-41", True, "maintenance_lead")
    assert answer == "Posted to ELEV-41 as svc-alert-triage, approved by maintenance_lead."
    [c] = world["store"].comments
    assert c.request_id == "triage-ELEV-41"


def test_one_outage_from_two_sources_is_triaged_once(world):
    world["broker"].publish(alert())
    world["broker"].publish(webhook())              # Jira's webhook for the same incident
    world["broker"].publish(alert())                # and the alert redelivered
    s = world["consumer"].run_until_idle()
    assert world["triage"].requested == ["triage-ELEV-41"]
    assert s.duplicates_skipped == 1 and s.handled == 2   # two distinct events, one triage


def test_keyed_on_the_event_instead_the_approver_gets_two_requests(world, tmp_path):
    """The control: deduplicating events alone lets two sources raise the same work twice."""
    jira, users = MockJira.from_fixture(FIXTURE)
    triage = Triage(jira, users, CommentStore(), tmp_path / "control.db", TODAY, key_on="event")
    broker = Broker()
    consumer = Consumer(broker, triage.handlers(), ProcessedStore(tmp_path / "cp.db"), DeadLetters(tmp_path / "cd.db"))
    broker.publish(alert())
    broker.publish(webhook())
    consumer.run_until_idle()
    assert len(triage.requested) == 2


def test_a_crash_after_the_request_but_before_the_ack_does_not_ask_twice(world):
    world["broker"].publish(alert())
    d = world["broker"].receive()
    world["triage"].on_outage(d.event)          # handled; the process dies before the ack and the mark
    world["broker"].crash()
    world["consumer"].run_until_idle()          # redelivered
    assert world["triage"].requested == ["triage-ELEV-41"]


def test_an_event_cannot_point_the_service_at_a_restricted_project(world):
    """A forged or mistaken alert naming a SECOPS issue halts in the guardrail and goes
    straight to the dead-letter queue: no Jira query, no retry."""
    world["broker"].publish(alert(alert_id="A-90", issue_key="SECOPS-3"))
    s = world["consumer"].run_until_idle()
    [dead] = world["dead"].items()
    assert s.retries == 0 and dead["reason"] == "halted by guardrail restricted_project" and dead["attempts"] == 1


def test_an_outage_outside_this_deployment_is_refused_not_retried(world):
    world["broker"].publish(alert(alert_id="A-91", issue_key="BILL-101"))
    s = world["consumer"].run_until_idle()
    [dead] = world["dead"].items()
    assert s.retries == 0 and "outside this assistant's scope" in dead["reason"]


def test_each_triage_is_traced_as_the_service_identity(world, traces):
    world["broker"].publish(alert())
    world["consumer"].run_until_idle()
    ended = [t for t in traces.traces if t["ended_at"]]
    assert {t["metadata"]["caller"] for t in ended} == {"svc-alert-triage"}
    assert [s["name"] for s in traces.of_kind("handoff")] == ["approval_request"]
    assert len(traces.of_kind("tool")) == 1          # one count; the comment waits for a person
