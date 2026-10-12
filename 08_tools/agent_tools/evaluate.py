"""Router and extraction accuracy on a labeled question set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .router import NeedsClarification, classify, extract

SPEC_FIELDS = ("project", "issue_type", "priority", "status", "group_by", "created_from", "created_before")


@dataclass(frozen=True)
class RouterReport:
    total: int
    routed_correctly: int
    misroutes: tuple[tuple[str, str, str], ...]          # (question, expected, got)
    extraction_total: int
    extracted_correctly: int
    extraction_misses: tuple[tuple[str, str], ...]      # (question, what differed)

    @property
    def router_accuracy(self) -> float:
        return self.routed_correctly / self.total

    @property
    def extraction_accuracy(self) -> float:
        return self.extracted_correctly / self.extraction_total if self.extraction_total else 0.0


def evaluate(path: str | Path) -> RouterReport:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    today = date.fromisoformat(raw["today"])
    misroutes, misses = [], []
    ok_route = ok_extract = n_extract = 0
    for item in raw["questions"]:
        got = classify(item["q"])
        if got == item["route"]:
            ok_route += 1
        else:
            misroutes.append((item["q"], item["route"], got))
        expected = item.get("spec")
        if expected is None:
            continue
        n_extract += 1
        try:
            spec = extract(item["q"], today).spec
        except NeedsClarification as e:
            misses.append((item["q"], f"asked to clarify: {e}"))
            continue
        diffs = []
        for f in SPEC_FIELDS:
            want = expected.get(f)
            have = getattr(spec, f)
            have = have.isoformat() if isinstance(have, date) else have
            if want != have:
                diffs.append(f"{f}: expected {want!r}, got {have!r}")
        if diffs:
            misses.append((item["q"], "; ".join(diffs)))
        else:
            ok_extract += 1
    return RouterReport(len(raw["questions"]), ok_route, tuple(misroutes), n_extract, ok_extract, tuple(misses))
