"""Three sources, one envelope; a bad record is rejected with its reason, never guessed."""

import json
from datetime import date

import pytest

from integration.adapters import (detail_record, jira_webhook, parse_workorders, service_alert, sign)
from integration.envelope import Event, InvalidEvent

SECRET = b"shared-webhook-secret"


def nightly(*details, count=None):
    body = "\n".join(details)
    return f"H20261012EAM-NIGHTLY\n{body}\nT{(len(details) if count is None else count):09d}\n"


WO1 = detail_record("WO-1001", "ESC-127-02", "S127", "OP", date(2026, 10, 9), "Step chain noise, lower landing")
WO2 = detail_record("WO-1002", "ELV-127-01", "S127", "CL", date(2026, 9, 30), "Door sensor replaced")


def test_a_nightly_file_becomes_one_event_per_work_order():
    events, rejected = parse_workorders(nightly(WO1, WO2))
    assert rejected == [] and [e.subject for e in events] == ["ESC-127-02", "ELV-127-01"]
    e = events[0]
    assert (e.source, e.type, e.id) == ("legacy/eam-nightly", "workorder.upserted", "WO-1001@20261012")
    assert e.data == {"work_order": "WO-1001", "asset": "ESC-127-02", "station": "S127", "status": "open",
                      "opened": "2026-10-09", "description": "Step chain noise, lower landing"}


def test_a_bad_record_is_rejected_alone_and_the_rest_go_through():
    bad = WO2[:27] + "ZZ" + WO2[29:]
    events, rejected = parse_workorders(nightly(WO1, bad))
    assert len(events) == 1 and len(rejected) == 1
    assert rejected[0].where == "line 3" and "unknown status 'ZZ'" in rejected[0].reason and rejected[0].raw == bad


def test_a_truncated_file_is_rejected_whole():
    """The trailer says how many records were sent. Fewer means the transfer was cut off:
    loading part of a night would look like a quiet night."""
    events, rejected = parse_workorders(nightly(WO1, count=2))
    assert events == [] and "trailer says 2 records, file has 1" in rejected[0].reason
    events, rejected = parse_workorders("H20261012\n" + WO1 + "\n")
    assert events == [] and "missing header or trailer" in rejected[0].reason


def webhook(key="ELEV-41", label="ESC-127-02"):
    return json.dumps({"timestamp": 1760300000000, "webhookEvent": "jira:issue_created",
                       "issue": {"key": key, "fields": {"project": {"key": "ELEV"}, "labels": [label]}}}).encode()


def test_a_signed_jira_webhook_becomes_an_incident_event():
    body = webhook()
    e = jira_webhook(body, sign(SECRET, body), SECRET)
    assert (e.source, e.type, e.subject) == ("saas/jira", "incident.created", "ELEV-41")
    assert e.data["asset"] == "ESC-127-02" and e.data["project"] == "ELEV"


@pytest.mark.parametrize("signature", [None, "", "sha256=00", sign(b"wrong-secret", webhook())])
def test_an_unsigned_or_forged_webhook_is_refused(signature):
    with pytest.raises(InvalidEvent, match="signature"):
        jira_webhook(webhook(), signature, SECRET)


def test_a_body_changed_after_signing_is_refused():
    body = webhook()
    signature = sign(SECRET, body)
    with pytest.raises(InvalidEvent, match="signature"):
        jira_webhook(webhook(key="ELEV-99"), signature, SECRET)


def test_a_service_alert_is_already_event_shaped():
    e = service_alert({"alert_id": "A-77", "asset": "ESC-127-02", "station": "S127", "kind": "escalator_out_of_service",
                       "severity": "major", "raised_at": "2026-10-12T07:41:00Z", "issue_key": "ELEV-41"})
    assert (e.type, e.id, e.data["issue_key"]) == ("alert.raised", "A-77", "ELEV-41")
    with pytest.raises(InvalidEvent, match="severity"):
        service_alert({"alert_id": "A-78", "asset": "x", "station": "S1", "kind": "k", "severity": "apocalyptic",
                       "raised_at": "2026-10-12T07:41:00Z"})


def test_the_envelope_refuses_what_it_cannot_identify():
    with pytest.raises(InvalidEvent, match="id"):
        Event("", "s", "t", "x", "2026-10-12T00:00:00Z")
    with pytest.raises(InvalidEvent, match="RFC 3339"):
        Event("1", "s", "t", "x", "yesterday")
    assert Event("7", "a", "t", "x", "2026-10-12T00:00:00Z").key != Event("7", "b", "t", "x", "2026-10-12T00:00:00Z").key
