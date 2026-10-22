"""Canonical identity (s4): one record per document number and revision, with every place it
is held.

The business key is doc_number + revision. The content hash (SHA-256 of normalised text)
says whether two copies are the same document. Three cases:

    same key, same hash        one record, two locations
    same key, different hash   a conflict: the system of record's copy is canonical, the
                               other copy is quarantined and reported, never merged
    different key, same hash   likely misfiled: both records kept, both flagged

The view is computed from every registration, so the order copies arrive in does not
change which copy is canonical or what is flagged.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SYSTEM_OF_RECORD = "qdms"
PRIORITY = {"qdms": 0, "sap": 1, "sharepoint": 2}


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Location:
    system: str
    uri: str
    content_hash: str


@dataclass
class Canonical:
    key: str
    content_hash: str
    locations: list[Location] = field(default_factory=list)
    conflicts: list[Location] = field(default_factory=list)
    same_content_as: list[str] = field(default_factory=list)


class Registry:
    def __init__(self):
        self._seen: dict[str, list[Location]] = {}

    def register(self, key: str, system: str, uri: str, chash: str) -> None:
        loc = Location(system, uri, chash)
        locs = self._seen.setdefault(key, [])
        if loc not in locs:
            locs.append(loc)

    def records(self) -> dict[str, Canonical]:
        out = {}
        for key, locs in self._seen.items():
            ordered = sorted(locs, key=lambda x: (PRIORITY.get(x.system, 9), x.uri))
            canon = ordered[0].content_hash
            out[key] = Canonical(key, canon, [x for x in ordered if x.content_hash == canon],
                                 [x for x in ordered if x.content_hash != canon])
        by_hash: dict[str, list[str]] = {}
        for key, rec in out.items():
            by_hash.setdefault(rec.content_hash, []).append(key)
        for keys in by_hash.values():
            if len(keys) > 1:
                for k in keys:
                    out[k].same_content_as = sorted(x for x in keys if x != k)
        return out

    def is_canonical(self, key: str, chash: str) -> bool:
        rec = self.records().get(key)
        return rec is not None and rec.content_hash == chash
