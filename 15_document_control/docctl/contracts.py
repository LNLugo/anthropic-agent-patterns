"""Each source's data contract, checked on arrival, and the adapters that turn a source
record into one envelope (12's Event).

A record that breaks its source's contract never becomes an event. It comes back as a
rejection with the raw record and the reason, for 12's dead-letter queue: quarantined,
counted and replayable once fixed, never guessed at and never silently dropped. One bad
record never stops the others (12's rule, INT-04). This differs from 14, where a log line
that will not parse stops the rebuild: one bad document must not hold up thousands of
good ones, so here the failure is per document.

The contracts are the specification's "named owner and expected schema" per source (s5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from integration.adapters import Rejected
from integration.envelope import Event, InvalidEvent

SOURCE = {"qdms": "dms/qdms", "sharepoint": "dms/sharepoint", "sap": "dms/sap", "status": "dms/qdms-status"}


@dataclass(frozen=True)
class Contract:
    source: str
    owner: str
    required: tuple[str, ...]          # dotted paths that must be present and non-empty
    required_when: tuple[tuple[str, str, str], ...] = ()   # (path, if this path, equals this value)


CONTRACTS = {
    "qdms": Contract("qdms", "Quality systems",
                     ("DocumentNumber", "Revision", "Title", "DocType", "Site", "Readers", "File", "ContentType",
                      "Modified")),
    "sharepoint": Contract("sharepoint", "Collaboration platform team",
                           ("id", "name", "file.mimeType", "lastModifiedDateTime", "listItem.fields.DocNumber",
                            "listItem.fields.Rev", "listItem.fields.Title", "listItem.fields.AllowedGroups")),
    "sap": Contract("sap", "ERP document management",
                    ("DOKNR", "DOKVR", "DOKAR", "DKTXT", "ACL", "ORIGINAL.FILE", "ORIGINAL.MIME", "AEDAT"),
                    (("MATNR", "DOKAR", "QCA"),)),       # a certificate is always linked to a material
    "status": Contract("status", "Quality systems", ("id", "doc_number", "revision", "to", "at")),
}


def _get(record: dict, path: str):
    v = record
    for part in path.split("."):
        if not isinstance(v, dict) or part not in v:
            return None
        v = v[part]
    return v


def check(source: str, record: dict) -> list[str]:
    """The contract's failures for this record, empty when it conforms."""
    c = CONTRACTS[source]
    missing = [p for p in c.required if _get(record, p) in (None, "", [])]
    missing += [p for p, when, value in c.required_when
                if _get(record, when) == value and _get(record, p) in (None, "", [])]
    return [f"{p} missing" for p in missing]


def _where(source: str, record: dict) -> str:
    return {"qdms": lambda r: r.get("DocumentNumber", "?"), "sharepoint": lambda r: r.get("id", "?"),
            "sap": lambda r: f"{r.get('DOKNR', '?')}/{r.get('DOKVR', '?')}",
            "status": lambda r: r.get("id", "?")}[source](record)


def to_event(source: str, record: dict) -> Event | Rejected:
    """A source record as an envelope, or a rejection naming what broke the contract."""
    failures = check(source, record)
    if failures:
        return Rejected(SOURCE[source], _where(source, record), json.dumps(record, sort_keys=True),
                        "contract: " + ", ".join(failures))
    try:
        return _ADAPT[source](record)
    except InvalidEvent as e:
        return Rejected(SOURCE[source], _where(source, record), json.dumps(record, sort_keys=True), str(e))


def _qdms(r: dict) -> Event:
    key = f"{r['DocumentNumber']}/{r['Revision']}"
    return Event(f"{key}@{r['Modified']}", SOURCE["qdms"], "document.upserted", key, r["Modified"],
                 {"doc_number": r["DocumentNumber"], "revision": r["Revision"], "title": r["Title"],
                  "type_hint": r["DocType"], "site": r["Site"], "acl_groups": list(r["Readers"]),
                  "system": "qdms", "file": r["File"], "mime": r["ContentType"]})


def _sharepoint(r: dict) -> Event:
    f = r["listItem"]["fields"]
    key = f"{f['DocNumber']}/{f['Rev']}"
    return Event(r["id"], SOURCE["sharepoint"], "document.upserted", key, r["lastModifiedDateTime"],
                 {"doc_number": f["DocNumber"], "revision": f["Rev"], "title": f["Title"],
                  "type_hint": f.get("ContentType", ""), "site": f.get("SiteCode", ""),
                  "acl_groups": [g for g in f["AllowedGroups"].split(";") if g], "system": "sharepoint",
                  "file": r["name"], "mime": r["file"]["mimeType"]})


def _sap(r: dict) -> Event:
    key = f"{r['DOKNR']}/{r['DOKVR']}"
    return Event(key, SOURCE["sap"], "document.upserted", key, f"{r['AEDAT']}T00:00:00Z",
                 {"doc_number": r["DOKNR"], "revision": r["DOKVR"], "title": r["DKTXT"],
                  "type_hint": {"QCA": "COA", "QCO": "COR"}.get(r["DOKAR"], ""), "site": r.get("WERKS", ""),
                  "acl_groups": list(r["ACL"]), "system": "sap", "file": r["ORIGINAL"]["FILE"],
                  "mime": r["ORIGINAL"]["MIME"], "material_id": r["MATNR"], "supplier_id": r.get("LIFNR", "")})


def _status(r: dict) -> Event:
    key = f"{r['doc_number']}/{r['revision']}"
    return Event(r["id"], SOURCE["status"], "document.status_changed", key, r["at"],
                 {"doc_number": r["doc_number"], "revision": r["revision"], "from": r.get("from"), "to": r["to"]})


_ADAPT = {"qdms": _qdms, "sharepoint": _sharepoint, "sap": _sap, "status": _status}
