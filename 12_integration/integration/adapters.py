"""Three sources, three integration styles, one envelope.

  legacy      a nightly fixed-width file from the asset management system: batch,
              positional fields, a header and a trailer with control totals
  SaaS        Jira's webhook: pushed over HTTPS, signed with a shared secret
  cloud-native service alerts on an event bus: JSON messages, already event shaped

Each adapter turns its input into Events, or raises InvalidEvent with the reason.
A bad record never stops the rest of a batch: it comes back as a rejection, to be
dead-lettered with the raw input, so nothing is lost and nothing is guessed.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .envelope import Event, InvalidEvent

# ---------------------------------------------------------------- legacy: fixed-width work orders

# Field layout of a detail record ("D"), by column. The header ("H") carries the run
# date; the trailer ("T") carries the record count, which must match.
LAYOUT = (
    ("record", 0, 1),
    ("work_order", 1, 11),
    ("asset", 11, 23),
    ("station", 23, 27),
    ("status", 27, 29),        # OP open, IP in progress, CL closed
    ("opened", 29, 37),        # YYYYMMDD
    ("description", 37, 77),
)
STATUSES = {"OP": "open", "IP": "in_progress", "CL": "closed"}
SOURCE_LEGACY = "legacy/eam-nightly"


@dataclass(frozen=True)
class Rejected:
    """An input that could not become an event, kept with the raw text for the dead-letter queue."""

    source: str
    where: str
    raw: str
    reason: str


def _fields(line: str) -> dict:
    return {name: line[a:b].strip() for name, a, b in LAYOUT}


def detail_record(work_order: str, asset: str, station: str, status: str, opened: date, description: str) -> str:
    """One detail line in the layout above (for fixtures and tests)."""
    return (f"D{work_order:<10}{asset:<12}{station:<4}{status:<2}{opened:%Y%m%d}{description[:40]:<40}")


def parse_workorders(text: str) -> tuple[list[Event], list[Rejected]]:
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("H") or not lines[-1].startswith("T"):
        return [], [Rejected(SOURCE_LEGACY, "file", text[:200], "missing header or trailer: the file is incomplete")]
    run = lines[0][1:9]
    details = lines[1:-1]
    try:
        expected = int(lines[-1][1:10])
    except ValueError:
        return [], [Rejected(SOURCE_LEGACY, "trailer", lines[-1], "trailer count is not a number")]
    if expected != len(details):
        # A short file is a truncated transfer, not a smaller night: reject all of it.
        return [], [Rejected(SOURCE_LEGACY, "file", lines[-1],
                             f"trailer says {expected} records, file has {len(details)}: truncated or padded")]
    events, rejected = [], []
    for n, line in enumerate(details, start=2):
        f = _fields(line)
        try:
            if f["record"] != "D" or len(line) < 37:
                raise InvalidEvent("not a detail record")
            if f["status"] not in STATUSES:
                raise InvalidEvent(f"unknown status {f['status']!r}")
            try:
                opened = datetime.strptime(f["opened"], "%Y%m%d").date()
            except ValueError:
                raise InvalidEvent(f"opened date {f['opened']!r} is not a real date") from None
            if not f["work_order"] or not f["asset"]:
                raise InvalidEvent("work order and asset are required")
            events.append(Event(
                id=f"{f['work_order']}@{run}", source=SOURCE_LEGACY, type="workorder.upserted", subject=f["asset"],
                time=f"{run[:4]}-{run[4:6]}-{run[6:8]}T02:00:00Z",
                data={"work_order": f["work_order"], "asset": f["asset"], "station": f["station"],
                      "status": STATUSES[f["status"]], "opened": opened.isoformat(), "description": f["description"]}))
        except (InvalidEvent, ValueError) as e:
            rejected.append(Rejected(SOURCE_LEGACY, f"line {n}", line, str(e)))
    return events, rejected


# ---------------------------------------------------------------- SaaS: Jira webhook, signed

SOURCE_JIRA = "saas/jira"


def sign(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 of the raw body with the shared secret, compared in constant time.
    The header name and format vary by vendor; check your vendor's webhook docs."""
    return bool(header) and hmac.compare_digest(sign(secret, body), header)


def jira_webhook(body: bytes, signature: str | None, secret: bytes) -> Event:
    """An incident created in Jira, as an event. Unsigned or forged requests are refused
    before the body is even parsed."""
    import json

    if not verify_signature(secret, body, signature):
        raise InvalidEvent("webhook signature missing or wrong")
    try:
        p = json.loads(body)
        issue = p["issue"]
        return Event(id=str(p["timestamp"]) + ":" + issue["key"], source=SOURCE_JIRA, type="incident.created",
                     subject=issue["key"], time=datetime.fromtimestamp(p["timestamp"] / 1000, timezone.utc).isoformat(),
                     data={"issue_key": issue["key"], "project": issue["fields"]["project"]["key"],
                           "asset": (issue["fields"].get("labels") or [None])[0], "event": p.get("webhookEvent")})
    except (KeyError, TypeError, ValueError) as e:
        raise InvalidEvent(f"not a Jira issue webhook: {e}") from None


# ---------------------------------------------------------------- cloud-native: service alerts on a bus

SOURCE_ALERTS = "bus/service-alerts"
SEVERITIES = ("info", "minor", "major", "critical")


def service_alert(message: dict) -> Event:
    try:
        sev = message["severity"]
        if sev not in SEVERITIES:
            raise InvalidEvent(f"unknown severity {sev!r}")
        return Event(id=message["alert_id"], source=SOURCE_ALERTS, type="alert.raised", subject=message["asset"],
                     time=message["raised_at"],
                     data={"alert_id": message["alert_id"], "asset": message["asset"], "station": message["station"],
                           "kind": message["kind"], "severity": sev, "issue_key": message.get("issue_key")})
    except KeyError as e:
        raise InvalidEvent(f"alert {e.args[0]} is required") from None
