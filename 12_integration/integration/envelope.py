"""One envelope for every source, so a consumer never parses a vendor's format.

The attributes are CloudEvents 1.0's required and common ones (specversion, id,
source, type, subject, time, datacontenttype, data). Only the shape is used; no
CloudEvents library is needed.

    id      unique per source: a redelivered event keeps its id, which is what
            makes duplicates detectable
    source  where it came from: "legacy/eam-nightly", "saas/jira", "bus/service-alerts"
    type    what happened: "workorder.upserted", "alert.raised", "incident.created"
    subject what it is about: an asset, an alert, an issue key
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


class InvalidEvent(ValueError):
    """The message cannot become an event. Retrying will not help: it goes to the dead-letter queue."""


@dataclass(frozen=True)
class Event:
    id: str
    source: str
    type: str
    subject: str
    time: str                       # RFC 3339
    data: dict = field(default_factory=dict)
    specversion: str = "1.0"
    datacontenttype: str = "application/json"

    def __post_init__(self):
        for name in ("id", "source", "type", "subject", "time"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise InvalidEvent(f"event {name} is required")
        try:
            datetime.fromisoformat(self.time.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidEvent(f"event time {self.time!r} is not RFC 3339") from None

    @property
    def key(self) -> str:
        """Deduplication key: an id is unique within its source, not across sources."""
        return f"{self.source}:{self.id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        try:
            return cls(**{k: d[k] for k in ("id", "source", "type", "subject", "time")}, data=dict(d.get("data") or {}))
        except KeyError as e:
            raise InvalidEvent(f"event {e.args[0]} is required") from None
