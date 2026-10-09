"""Chunking with exact character offsets.

Every chunk keeps (start, end) into its source contract. Offsets are what make
the evaluation honest: a chunk "hits" only if it overlaps a lawyer-annotated
span, and a citation is valid only if its quote maps back into the contract.

Two levels are produced:
  child chunks  small windows used for matching (precise retrieval)
  parent chunks larger windows returned for generation (enough context for a
                clause to be read with its defined terms and section header)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    id: str
    contract_id: str
    text: str
    start: int
    end: int
    parent_id: str | None = None

    def overlaps(self, span: tuple[int, int]) -> bool:
        s, e = span
        return self.start < e and s < self.end


def _snap_back(text: str, pos: int, floor: int) -> int:
    """Move pos back to the nearest whitespace so words are not split."""
    if pos >= len(text):
        return len(text)
    k = pos
    while k > floor and not text[k].isspace():
        k -= 1
    return k if k > floor else pos


def fixed_size(text: str, contract_id: str, size: int, overlap: int, prefix: str = "ch") -> list[Chunk]:
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    chunks: list[Chunk] = []
    start = 0
    n = 0
    while start < len(text):
        end = _snap_back(text, min(start + size, len(text)), start + size // 2)
        piece = text[start:end]
        if piece.strip():
            chunks.append(Chunk(f"{contract_id}:{prefix}{n:04d}", contract_id, piece, start, end))
            n += 1
        if end >= len(text):
            break
        nxt = _snap_back(text, end - overlap, start + 1) if overlap else end
        start = max(nxt, start + 1)
    return chunks


def chunk_contract(
    text: str,
    contract_id: str,
    size: int = 1000,
    overlap: int = 200,
    parent_size: int | None = 4000,
) -> tuple[list[Chunk], dict[str, Chunk]]:
    """Return child chunks (with parent_id set) and a parent lookup."""
    children = fixed_size(text, contract_id, size, overlap, prefix="ch")
    if not parent_size:
        return children, {}
    parents = fixed_size(text, contract_id, parent_size, 0, prefix="pa")
    lookup = {p.id: p for p in parents}

    def parent_for(c: Chunk) -> str:
        for p in parents:
            if p.start <= c.start < p.end:
                return p.id
        return parents[-1].id

    children = [
        Chunk(c.id, c.contract_id, c.text, c.start, c.end, parent_for(c)) for c in children
    ]
    return children, lookup
