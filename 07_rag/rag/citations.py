"""Citation validation: every claim must resolve to text the model was shown.

A citation is a (chunk_id, quote) pair. It is valid only when:
  1. chunk_id is one of the chunks actually injected into the prompt, and
  2. quote appears in that chunk (whitespace and case normalized).

Valid citations are mapped back to contract character offsets, so they can be
scored against CUAD gold spans (citation precision) rather than trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .chunking import Chunk

_WS = re.compile(r"\s+")
MIN_QUOTE_CHARS = 12


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    quote: str


@dataclass(frozen=True)
class CheckedCitation:
    citation: Citation
    status: str  # valid | unknown_chunk | quote_not_found | quote_too_short
    span: tuple[int, int] | None = None


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace and lowercase, keeping a map back to original offsets."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space and out:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def locate(quote: str, chunk: Chunk) -> tuple[int, int] | None:
    """Return the contract-level (start, end) of quote inside chunk, or None."""
    needle = _WS.sub(" ", quote).strip().lower()
    if not needle:
        return None
    hay, idx = _normalize_with_map(chunk.text)
    pos = hay.find(needle)
    if pos < 0:
        return None
    start = chunk.start + idx[pos]
    end = chunk.start + idx[pos + len(needle) - 1] + 1
    return start, end


def validate(citations: list[Citation], shown: list[Chunk]) -> list[CheckedCitation]:
    by_id = {c.id: c for c in shown}
    checked: list[CheckedCitation] = []
    for cit in citations:
        chunk = by_id.get(cit.chunk_id)
        if chunk is None:
            checked.append(CheckedCitation(cit, "unknown_chunk"))
            continue
        if len(_WS.sub(" ", cit.quote).strip()) < MIN_QUOTE_CHARS:
            checked.append(CheckedCitation(cit, "quote_too_short"))
            continue
        span = locate(cit.quote, chunk)
        checked.append(CheckedCitation(cit, "valid" if span else "quote_not_found", span))
    return checked


def citation_precision(checked: list[CheckedCitation], gold: tuple[tuple[int, int], ...]) -> float | None:
    """Share of valid citations that overlap a gold span. None if nothing valid."""
    valid = [c for c in checked if c.status == "valid" and c.span]
    if not valid:
        return None
    hits = sum(1 for c in valid if any(c.span[0] < e and s < c.span[1] for s, e in gold))
    return hits / len(valid)
