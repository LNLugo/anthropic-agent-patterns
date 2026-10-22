"""Validation (s6): rules over the extracted values, against Quality's taxonomy and the
material and supplier master data (the stand-in for SAP data in Delta tables).

A failure never changes a value. It is recorded with the rule, the field and why, and
the document goes to review. "Nothing is silently corrected" is tested: every value in a
validated record is exactly what extraction read.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .classify import taxonomy
from .extract import Field

MASTER = Path(__file__).resolve().parents[1] / "fixtures" / "master"


@dataclass(frozen=True)
class Failure:
    rule: str
    field: str
    detail: str


@lru_cache(maxsize=1)
def master(folder: Path = MASTER) -> dict:
    with open(folder / "materials.csv", encoding="utf-8") as f:
        materials = {r["material_id"] for r in csv.DictReader(f)}
    with open(folder / "suppliers.csv", encoding="utf-8") as f:
        suppliers = {r["supplier_id"]: r["approved"] == "True" for r in csv.DictReader(f)}
    return {"materials": materials, "suppliers": suppliers}


def _number(s) -> float | None:
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def validate(doc_type: str | None, fields: dict[str, Field], metadata: dict) -> list[Failure]:
    tax, m = taxonomy(), master()
    out: list[Failure] = []
    if doc_type is None:
        return out
    t = tax["types"][doc_type]
    for name in t["required"]:
        if name not in fields or fields[name].value in ("", None, [], {}):
            out.append(Failure("required_field_missing", name, f"{name} is required for {doc_type}"))
    if "revision" in fields and not re.match(tax["formats"]["revision"], str(fields["revision"].value)):
        out.append(Failure("bad_revision_format", "revision", f"{fields['revision'].value!r} is not two digits"))
    for name in tax["formats"]["dates"]:
        if name in fields:
            try:
                date.fromisoformat(str(fields[name].value))
            except ValueError:
                out.append(Failure("bad_date", name, f"{fields[name].value!r} is not a valid date"))
    if "site" in fields and fields["site"].value not in tax["allowed"]["site"]:
        out.append(Failure("site_not_allowed", "site", f"{fields['site'].value!r}"))
    if "material_id" in fields and fields["material_id"].value not in m["materials"]:
        out.append(Failure("unknown_material", "material_id", f"{fields['material_id'].value} not in the material master"))
    if "supplier_id" in fields:
        sid = fields["supplier_id"].value
        if sid not in m["suppliers"]:
            out.append(Failure("unknown_supplier", "supplier_id", f"{sid} not in the supplier master"))
        elif not m["suppliers"][sid]:
            out.append(Failure("supplier_not_approved", "supplier_id", f"{sid} is not an approved supplier"))
    ranges = tax["parameter_ranges"]
    values = []
    if "limits" in fields:
        values += [(p, v) for p, lohi in fields["limits"].value.items() for v in lohi]
    if "results" in fields:
        values += list(fields["results"].value.items())
    for p, v in values:
        x = _number(v)
        if p not in ranges:
            out.append(Failure("unknown_parameter", p, f"{p} is not in the taxonomy"))
        elif x is None:
            out.append(Failure("value_not_numeric", p, f"{v!r}"))
        elif not ranges[p][0] <= x <= ranges[p][1]:
            out.append(Failure("value_out_of_allowed_range", p, f"{v} outside {ranges[p]}"))
    floor = tax["ocr_confidence_floor"]
    for f in fields.values():
        if f.confidence is not None and f.confidence < floor:
            out.append(Failure("ocr_confidence_below_floor", f.name, f"read at {f.confidence}, floor {floor}"))
    # the document against the source's own metadata
    for name, meta in (("doc_number", "doc_number"), ("revision", "revision")):
        if name in fields and fields[name].source == "content" and metadata.get(meta) \
                and str(fields[name].value) != str(metadata[meta]):
            out.append(Failure("disagrees_with_metadata", name,
                               f"document says {fields[name].value!r}, the source says {metadata[meta]!r}"))
    hint = metadata.get("type_hint")
    if hint and hint in tax["types"] and hint != doc_type:
        out.append(Failure("type_disagrees_with_metadata", "doc_type", f"classified {doc_type}, the source says {hint}"))
    return out
