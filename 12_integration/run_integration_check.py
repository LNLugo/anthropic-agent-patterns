"""One night of operations through the whole pipeline, with failures injected on purpose.

    python run_integration_check.py       # writes results/integration_check.json

Inputs, all synthetic: a nightly work-order file of 40 records (2 malformed), and
4 real outages, each arriving twice (a service alert on the bus and Jira's
webhook). On top of that: a quarter of all messages redelivered, a consumer crash
mid-handling, Jira timing out twice, a forged alert pointing at a SECOPS issue,
and an alert for a project this deployment does not serve. Then a person
approves every triage comment, and the first post's response is lost.

Each guarantee is measured against a control without it.
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT, ROOT / "08_tools", ROOT / "10_observability"):
    sys.path.insert(0, str(p))

from agent_tools.comments import CommentStore, CommentTool  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from integration.adapters import detail_record, jira_webhook, parse_workorders, service_alert, sign  # noqa: E402
from integration.delivery import Broker, Clock, Consumer, DeadLetters, ProcessedStore  # noqa: E402
from integration.triage import Triage  # noqa: E402
from tracing import InMemoryExporter, Tracer, set_tracer  # noqa: E402

TODAY = date(2026, 10, 12)
SECRET = b"shared-webhook-secret"
OUTAGES = [("ELEV-38", "ESC-203-01", "S203"), ("ELEV-40", "ELV-127-01", "S127"),
           ("ELEV-41", "ESC-127-02", "S127"), ("SIG-12", "TC-203-04", "S203")]


def nightly() -> str:
    rng = random.Random(7)
    assets = [a for _, a, _ in OUTAGES] + ["ESC-114-01", "ELV-114-01", "ESC-305-02"]
    lines = []
    for n in range(40):
        asset = assets[n % len(assets)]
        lines.append(detail_record(f"WO-{2000 + n}", asset, asset.split("-")[1].rjust(4, "S")[:4].replace(" ", "S"),
                                   rng.choice(["OP", "IP", "CL"]), date(2026, 9, 1 + n % 28), f"Task {n} on {asset}"))
    lines[11] = lines[11][:27] + "XX" + lines[11][29:]          # unknown status
    lines[23] = lines[23][:29] + "20261399" + lines[23][37:]    # impossible date
    return "H20261012\n" + "\n".join(lines) + f"\nT{len(lines):09d}\n"


def outage_events():
    events = []
    for n, (key, asset, station) in enumerate(OUTAGES):
        events.append(service_alert({"alert_id": f"A-{100 + n}", "asset": asset, "station": station,
                                     "kind": "out_of_service", "severity": "major",
                                     "raised_at": "2026-10-12T07:00:00Z", "issue_key": key}))
        body = json.dumps({"timestamp": 1760300000000 + n, "webhookEvent": "jira:issue_created",
                           "issue": {"key": key, "fields": {"project": {"key": key.split("-")[0]}, "labels": [asset]}}}).encode()
        events.append(jira_webhook(body, sign(SECRET, body), SECRET))
    events.append(service_alert({"alert_id": "A-900", "asset": "CAM-001", "station": "S001", "kind": "camera",
                                 "severity": "minor", "raised_at": "2026-10-12T07:05:00Z", "issue_key": "SECOPS-3"}))
    events.append(service_alert({"alert_id": "A-901", "asset": "KIOSK-9", "station": "S009", "kind": "kiosk",
                                 "severity": "minor", "raised_at": "2026-10-12T07:06:00Z", "issue_key": "BILL-101"}))
    return events


class ProcessDied(BaseException):
    """The consumer process stops: nothing after this line runs, nothing is acknowledged."""


class FlakyJira(MockJira):
    """Times out on the first `failures` searches, as an overloaded SaaS API does."""

    failures = 0
    searches = 0

    def search(self, caller, **kw):
        FlakyJira.searches += 1
        if FlakyJira.failures > 0:
            FlakyJira.failures -= 1
            raise ConnectionError("Jira timed out")
        return super().search(caller, **kw)


class LostFirstResponse(CommentStore):
    dropped = False

    def add(self, *a, **k):
        c = super().add(*a, **k)
        if not self.dropped:
            self.dropped = True
            raise ConnectionError("connection reset after the server committed")
        return c


class NoMarkerCommentTool(CommentTool):
    """08 before this module: a post that ignores the approval's request id."""

    def post(self, issue_key, body, caller, approved_by, request_id=None):
        return super().post(issue_key, body, caller, approved_by)


def night(work: Path, dedupe: bool = True, key_on: str = "incident", marker: bool = True) -> dict:
    set_tracer(Tracer([InMemoryExporter()]))
    jira, users = FlakyJira.from_fixture(HERE / "fixtures" / "transit_jira.json")
    FlakyJira.failures, FlakyJira.searches = 2, 0
    store = LostFirstResponse()
    triage = Triage(jira, users, store, work / "approvals.db", TODAY, key_on=key_on)
    if not marker:
        import integration.triage as t
        t.CommentTool = NoMarkerCommentTool
    broker, dead = Broker(Clock()), DeadLetters(work / "dead.db")
    runs = {"n": 0}

    def counted(fn):
        def run(e):
            runs["n"] += 1
            return fn(e)
        return run

    handlers = {t: counted(fn) for t, fn in triage.handlers().items()}
    consumer = Consumer(broker, handlers, ProcessedStore(work / "processed.db") if dedupe else None, dead)

    workorders, rejected = parse_workorders(nightly())
    for r in rejected:
        dead.add_rejected(r)
    messages = workorders + outage_events()
    rng = random.Random(42)
    redelivered = rng.sample(messages, k=len(messages) // 4)
    for e in messages + redelivered:
        broker.publish(e)

    # The process dies once, right after handling the first outage and before acknowledging it.
    handle = consumer.handlers["alert.raised"]
    crashed = []

    def crash_once(e):
        handle(e)
        if not crashed:
            crashed.append(e.key)
            raise ProcessDied()

    consumer.handlers["alert.raised"] = crash_once
    try:
        consumer.run_until_idle()
    except ProcessDied:
        broker.crash()               # what was in flight is redelivered, as after a lapsed visibility timeout
    stats = consumer.run_until_idle()

    posted_answers = []
    for thread in list(triage.requested):
        try:
            posted_answers.append(triage.decide(thread, True, "maintenance_lead"))
        except ConnectionError:
            app = triage.graph(__import__("agent_tools.model_router", fromlist=["Decision"]).Decision("none"), comments=True)
            app.invoke(None, {"configurable": {"thread_id": thread}})     # the service retries the run
    if not marker:
        import integration.triage as t
        t.CommentTool = CommentTool
    set_tracer(None)
    return {"dedupe": dedupe, "key_on": key_on, "marker": marker,
            "published": broker.published, "messages": len(messages), "redelivered": len(redelivered),
            "handler_runs": runs["n"], "jira_searches": FlakyJira.searches, "work_orders_loaded": len(triage.workorders.orders),
            "crashed_after": crashed, "approval_requests": len(triage.requested), "comments_posted": len(store.comments),
            "duplicates_skipped": stats.duplicates_skipped, "retries": stats.retries, "backoff_waits": consumer.delays,
            "dead_letters": [d["reason"] for d in dead.items()]}


def main() -> int:
    runs = {}
    for name, kw in (("design", {}), ("no idempotency store", {"dedupe": False}),
                     ("keyed on the event, not the incident", {"key_on": "event"}),
                     ("no request id marker on the post", {"marker": False})):
        with tempfile.TemporaryDirectory() as tmp:
            runs[name] = night(Path(tmp), **kw)
    d = runs["design"]
    print(f"Messages: {d['messages']} (38 valid work orders of 40, 8 events for 4 outages, 2 bad alerts),"
          f" plus {d['redelivered']} redelivered: {d['published']} published")
    print(f"\n{'':40} {'approval requests':>18} {'comments posted':>16} {'handler runs':>13} {'Jira queries':>13}")
    for name, r in runs.items():
        print(f"{name:40} {r['approval_requests']:>18} {r['comments_posted']:>16} {r['handler_runs']:>13} {r['jira_searches']:>13}")
    print(f"\nDesign: {d['duplicates_skipped']} duplicates skipped, {d['retries']} retries (waits {d['backoff_waits']}s),"
          f" {d['work_orders_loaded']} work orders loaded")
    print("Dead letters:")
    for reason in d["dead_letters"]:
        print(f"  {reason}")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "integration_check.json").write_text(json.dumps(runs, indent=2) + "\n")
    print("\nWrote results/integration_check.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
