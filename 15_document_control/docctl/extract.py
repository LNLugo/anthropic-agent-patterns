"""Field extraction (s6): every value with the page, region and OCR confidence it came from,
so a reviewer sees it in place.

Values are read from the header block's "Label: value" lines, using the label variants
the sources use, and from the tables the taxonomy names. A value is extracted exactly as
written. Nothing is normalised or corrected here: validation decides what is wrong, and a
person decides what it should be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import Line, Parsed

LABELS = {
    "doc_number": ("Document No.", "Change No.", "Deviation No.", "Complaint No.", "Certificate No.", "Cert. ref"),
    "revision": ("Revision",),
    "effective_date": ("Effective date",),
    "site": ("Site",),
    "approved_by": ("Approved by",),
    "material_id": ("Material", "Item", "Product material"),
    "supplier_id": ("Supplier", "Vendor"),
    "lot": ("Lot", "Batch"),
    "manufactured": ("Manufactured", "Date of manufacture"),
    "opened": ("Opened",),
    "received": ("Received",),
}
SUPPLIER_ID = re.compile(r"\((SUP-[^)]+)\)\s*$")
FROM_DOMAIN = re.compile(r"@(sup-\d{4})\.", re.I)


@dataclass(frozen=True)
class Field:
    name: str
    value: object
    page: int
    region: tuple[float, ...] | None
    confidence: float | None
    source: str = "content"          # "content"; "metadata" when the document cannot state it; "derived"
                                     # when worked out from something it does state (an email's sender)


def _labelled(doc: Parsed) -> dict[str, Line]:
    out = {}
    for line in doc.header:
        if ":" in line.text:
            label = line.text.split(":", 1)[0].strip()
            out.setdefault(label, line)
    return out


def _value(line: Line) -> str:
    return line.text.split(":", 1)[1].strip()


def extract(doc: Parsed, doc_type: str | None, metadata: dict) -> dict[str, Field]:
    labelled = _labelled(doc)
    out: dict[str, Field] = {}
    for name, labels in LABELS.items():
        line = next((labelled[x] for x in labels if x in labelled), None)
        if line is None:
            continue
        value = _value(line)
        if name == "supplier_id":
            m = SUPPLIER_ID.search(value)
            value = m.group(1) if m else value
        out[name] = Field(name, value, line.page, line.region, line.confidence)
    out["title"] = Field("title", doc.title, 1, None, None)
    sec = {s.heading: s for s in doc.sections}
    if doc_type == "CC" and "Affected documents" in sec:
        s = sec["Affected documents"]
        out["affected_documents"] = Field("affected_documents", [x.text for x in s.lines],
                                          s.lines[0].page if s.lines else 1, None, None)
    if doc_type == "SPEC":
        t = next((t for s in doc.sections for t in s.tables if t.header[:2] == ["Parameter", "Limit"]), None)
        if t is not None:
            limits = {}
            for r in t.rows:
                lo, _, hi = r[1].partition(" - ")
                limits[r[0]] = [lo.strip(), hi.strip()]
            out["limits"] = Field("limits", limits, t.page, None, None)
    if doc_type == "COA":
        t = next((t for s in doc.sections for t in s.tables if t.header[:2] == ["Test", "Result"]), None)
        if t is not None:
            out["results"] = Field("results", {r[0]: r[1] for r in t.rows}, t.page, None, None)
    if doc_type == "COR":
        frm = next((x for x in doc.header if x.text.startswith("From:")), None)
        m = FROM_DOMAIN.search(frm.text) if frm else None
        if m:
            out["supplier_id"] = Field("supplier_id", m.group(1).upper(), 1, None, None, "derived")
    if "doc_number" not in out and metadata.get("doc_number"):     # email has no number of its own
        out["doc_number"] = Field("doc_number", metadata["doc_number"], 1, None, None, "metadata")
    return out
