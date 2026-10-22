"""Structure-aware chunking and chunk-level dedup (s5).

- One chunk per section, split on headings, because SOPs and specifications are written by
  section. The header block is a chunk of its own.
- A section over the target is split by lines with 15% overlap; a short one is never
  padded or merged.
- A table is kept whole. If it is too large, it is split by rows with the header repeated
  on every part.
- Every chunk carries its section path, page range, the lowest OCR confidence of its lines,
  and a SHA-256 of its normalised text. An unchanged section in a new revision has the same
  hash, so its embedding is reused, not computed again.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identity import content_hash
from .parse import Line, Parsed, Table

MAX_TOKENS = 800
OVERLAP = 0.15


def tokens(text: str) -> int:
    """A word-based estimate (about 1.3 tokens a word), so chunk sizes need no tokenizer."""
    return round(len(text.split()) * 1.3)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    key: str
    section_path: str
    text: str
    pages: tuple[int, int]
    token_count: int
    chunk_hash: str
    ocr_confidence: float | None
    is_table: bool = False


def _table_text(t: Table, rows: list[list[str]]) -> str:
    return "\n".join([" | ".join(t.header)] + [" | ".join(r) for r in rows])


def _make(key, path, n, text, lines, pages, is_table=False) -> Chunk:
    confs = [x.confidence for x in lines if x.confidence is not None]
    return Chunk(f"{key}#{path}#{n}", key, path, text, pages, tokens(text), content_hash(path + "\n" + text),
                 min(confs) if confs else None, is_table)


def _split_lines(lines: list[Line], max_tokens: int) -> list[list[Line]]:
    parts, cur = [], []
    for ln in lines:
        if cur and tokens(" ".join(x.text for x in cur + [ln])) > max_tokens:
            parts.append(cur)
            keep = max(1, round(len(cur) * OVERLAP))
            cur = cur[-keep:]
        cur.append(ln)
    if cur:
        parts.append(cur)
    return parts


def _split_table(t: Table, heading_tokens: int, max_tokens: int) -> list[list[list[str]]]:
    parts, cur = [], []
    for r in t.rows:
        if cur and tokens(_table_text(t, cur + [r])) + heading_tokens > max_tokens:
            parts.append(cur)
            cur = []
        cur.append(r)
    if cur or not parts:
        parts.append(cur)
    return parts


def chunk(key: str, doc: Parsed, max_tokens: int = MAX_TOKENS) -> list[Chunk]:
    out = []
    head = [doc.kind_line, doc.title] + [x.text for x in doc.header]
    pages = [x.page for x in doc.header] or [1]
    out.append(_make(key, "0 Header", 1, "\n".join(x for x in head if x), doc.header, (min(pages), max(pages))))
    for s in doc.sections:
        n = 0
        if s.lines or not s.tables:
            for part in _split_lines(s.lines, max_tokens) or [[]]:
                n += 1
                p = [x.page for x in part] or [1]
                out.append(_make(key, s.path, n, "\n".join([s.heading] + [x.text for x in part]), part,
                                 (min(p), max(p))))
        for t in s.tables:
            for rows in _split_table(t, tokens(s.heading), max_tokens):
                n += 1
                out.append(_make(key, s.path, n, s.heading + "\n" + _table_text(t, rows), [], (t.page, t.page),
                                 is_table=True))
    return out


class EmbeddingCache:
    """Vectors keyed by chunk hash and embedding model: an unchanged chunk is never embedded twice."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.model_id = getattr(embedder, "model", "unknown")
        self._vectors: dict[tuple[str, str], list[float]] = {}
        self.computed = 0
        self.reused = 0

    def vectors(self, chunks: list[Chunk]) -> list[list[float]]:
        todo = [c for c in chunks if (c.chunk_hash, self.model_id) not in self._vectors]
        if todo:
            for c, v in zip(todo, self.embedder.embed_documents([c.text for c in todo])):
                self._vectors[(c.chunk_hash, self.model_id)] = v
        self.computed += len({c.chunk_hash for c in todo})
        self.reused += len(chunks) - len(todo)
        return [self._vectors[(c.chunk_hash, self.model_id)] for c in chunks]
