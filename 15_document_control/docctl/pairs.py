"""Comparison fixtures (s10): revision pairs with planted, labelled changes, and a standard.

Each pair is an older and a newer revision of one procedure or specification, written as
native PDFs, with between two and five planted changes. Every change is labelled with its
section, its before and after text, its type and its severity, so recall, type accuracy and
false findings can be counted:

    editorial               wording that does not change what anyone does      low
    procedural              a step added, removed or reassigned                 medium
    specification_or_limit  a limit, unit or method in a specification table    medium if a limit
                                                                                tightens, high if it
                                                                                widens or a unit changes
    safety_relevant         a protective or hazard instruction changed          high

Each newer revision also carries formatting noise that is not a change: a paragraph re-wrapped
across two lines, extra spaces, and a page break in a different place. A finding there is a
false finding.

The standard is an invented internal document-control standard with six clauses. Three
extra procedures each break it in a known way.

Everything is invented. Written by corpus.generate (seed 15), so the fixture test covers it.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml

from .pdfwrite import write_pdf

EDITORIAL = [("must be", "shall be"), ("recieve", "receive"), ("in accordance to", "in accordance with"),
             ("e-mail", "email"), ("ensure that", "ensure")]
SAFETY_LINES = ["Wear nitrile gloves and safety glasses when handling the material.",
                "Work under local exhaust ventilation; the vapour is flammable.",
                "Do not open the container near an ignition source."]
ROLES = ["Quality", "Production", "the area supervisor", "the shift lead"]
TOPICS = ["Raw material receipt", "Bulk transfer", "Line sanitisation", "Weigh room cleaning", "Sample labelling",
          "Hot fill setup", "Batch reconciliation", "Tank cleaning"]
PARAMS = [("pH", "-", 5.0, 7.5, 1, "Potentiometric"), ("Viscosity", "mPa.s", 1200, 1600, 0, "Brookfield"),
          ("Water", "%", 0.0, 0.5, 2, "Karl Fischer"), ("Assay", "%", 98.0, 101.0, 1, "HPLC"),
          ("Microbial count", "cfu/g", 0, 100, 0, "Plate count")]

STANDARD_CLAUSES = [
    {"id": "STD-1", "requirement": "The procedure states its purpose.", "heading": "Purpose"},
    {"id": "STD-2", "requirement": "The procedure defines its scope.", "heading": "Scope"},
    {"id": "STD-3", "requirement": "The procedure names who is responsible.", "heading": "Responsibilities"},
    {"id": "STD-4", "requirement": "The procedure states how long records are kept.", "heading": "Records",
     "words": ["kept for"]},
    {"id": "STD-5", "requirement": "The procedure carries a revision history.", "heading": "Revision history"},
    {"id": "STD-6", "requirement": "The procedure names who approved it.", "header": "Approved by"},
]


def _wrap(line: str) -> list[str]:
    """Noise: one paragraph re-wrapped across two lines. Not a change."""
    cut = line.rfind(" ", 0, len(line) // 2 + 8)
    return [line[:cut], line[cut + 1:]] if cut > 0 else [line]


def _lines(title, header, sections, tables, noise=False):
    out = ["STANDARD OPERATING PROCEDURE" if "Procedure" in [h for h, _ in sections] else "MATERIAL SPECIFICATION",
           title] + [f"{k}: {v}" for k, v in header] + [""]
    for n, (h, paras) in enumerate(sections, 1):
        out.append(f"{n}. {h.upper()}")
        for p in paras:
            if noise and len(p) > 60 and p is paras[0]:
                out += _wrap(p.replace(", ", ",  "))          # re-wrapped, and double spaces
            else:
                out.append(p)
        if h in tables:
            cols, rows = tables[h]
            widths = [max(len(str(x)) for x in [c] + [r[j] for r in rows]) + 3 for j, c in enumerate(cols)]
            out.append("".join(str(c).ljust(w) for c, w in zip(cols, widths)).rstrip())
            out += ["".join(str(x).ljust(w) for x, w in zip(r, widths)).rstrip() for r in rows]
        out.append("")
        if noise and n == 2:
            out.append("\f")                               # a page break somewhere new
    return out


def _sop(rng, i):
    topic = TOPICS[i % len(TOPICS)]
    owner = ROLES[i % 2]
    steps = [f"Step {k}: " + s for k, s in enumerate(rng.sample([
        "Check the area is clear of the previous product and its labels.",
        "Confirm the equipment status label shows clean and in date.",
        "Record the batch number in the logbook.",
        "The operator must be trained on this procedure before starting.",
        "Verify the result in accordance to the acceptance criterion.",
        "Send the completed record to Quality by e-mail.",
        "Ensure that the second-person check is signed."], 6), 1)]
    return {"title": topic, "sections": [
        ["Purpose", [f"This procedure describes {topic.lower()} at the Northfield site."]],
        ["Scope", ["Applies to production and warehouse staff."]],
        ["Responsibilities", [f"{owner} owns this procedure; Quality approves it."]],
        ["Safety", [SAFETY_LINES[i % len(SAFETY_LINES)]]],
        ["Procedure", steps],
        ["Records", ["Records are kept for five years after batch expiry."]]], "tables": {}}


def _spec(rng, i):
    params = rng.sample(PARAMS, 4)
    rows = [[p, f"{lo:.{dg}f} - {hi:.{dg}f}", u, m] for p, u, lo, hi, dg, m in params]
    return {"title": f"Specification: raw material RM-2{i:04d}", "sections": [
        ["Scope", ["Acceptance specification for this raw material at all sites."]],
        ["Limits", []],
        ["Sampling", ["Sample every lot before release and test against the limits below."]]],
        "tables": {"Limits": (["Parameter", "Limit", "Unit", "Method"], rows)}, "params": params}


def _change_sop(rng, doc, changes):
    secs = {h: p for h, p in doc["sections"]}
    kinds = rng.sample(["editorial", "procedural_add", "procedural_remove", "procedural_role", "safety"],
                       rng.randint(2, 4))
    for kind in kinds:
        if kind == "editorial":
            for k, line in enumerate(secs["Procedure"]):
                hit = next(((a, b) for a, b in EDITORIAL if a in line), None)
                if hit:
                    new = line.replace(*hit)
                    secs["Procedure"][k] = new
                    changes.append({"section": "Procedure", "type": "editorial", "severity": "low",
                                    "before": line, "after": new})
                    break
        elif kind == "procedural_add":
            n = len(secs["Procedure"]) + 1
            new = f"Step {n}: Return unused material to the warehouse with its status label."
            secs["Procedure"].append(new)
            changes.append({"section": "Procedure", "type": "procedural", "severity": "medium", "before": "",
                            "after": new})
        elif kind == "procedural_remove":
            gone = secs["Procedure"].pop(1)
            changes.append({"section": "Procedure", "type": "procedural", "severity": "medium", "before": gone,
                            "after": ""})
        elif kind == "procedural_role":
            old = secs["Responsibilities"][0]
            new = old.replace("Quality approves it", "the area supervisor approves it")
            secs["Responsibilities"][0] = new
            changes.append({"section": "Responsibilities", "type": "procedural", "severity": "medium",
                            "before": old, "after": new})
        elif kind == "safety":
            old = secs["Safety"][0]
            new = old.split(";")[0].replace("nitrile gloves and safety glasses", "gloves") \
                if "gloves" in old else "Keep the container closed when not in use."
            secs["Safety"][0] = new
            changes.append({"section": "Safety", "type": "safety_relevant", "severity": "high", "before": old,
                            "after": new})


def _change_spec(rng, doc, changes):
    cols, rows = doc["tables"]["Limits"]
    picks = rng.sample(range(len(rows)), rng.randint(2, 3))
    for j, r in enumerate(picks):
        p, u, lo, hi, dg, m = doc["params"][r]
        row = rows[r]
        before = " | ".join(row)
        kind = ["tighten", "widen", "unit", "method"][(j + r) % 4]
        if kind == "tighten":
            row[1] = f"{lo:.{dg}f} - {hi - (hi - lo) * 0.2:.{dg}f}"
            sev = "medium"
        elif kind == "widen":
            row[1] = f"{lo:.{dg}f} - {hi + (hi - lo) * 0.3:.{dg}f}"
            sev = "high"
        elif kind == "unit":
            row[2] = {"%": "% w/w", "-": "pH units", "mPa.s": "cP", "cfu/g": "cfu/mL"}[u]
            sev = "high"
        else:
            row[3] = m + " (in-house)"
            sev = "medium"
        changes.append({"section": "Limits", "type": "specification_or_limit", "severity": sev, "before": before,
                        "after": " | ".join(row), "row": p})


def generate_pairs(folder: Path, seed: int = 15) -> dict:
    rng = random.Random(seed + 7)
    out = {"pairs": [], "standard": {"id": "STD-QA-001", "title": "Document control standard (invented)",
                                     "clauses": STANDARD_CLAUSES}, "standard_checks": []}
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(16):
        is_spec = i % 3 == 2
        a = _spec(rng, i) if is_spec else _sop(rng, i)
        b = {**a, "sections": [[h, list(p)] for h, p in a["sections"]],
             "tables": {k: (c, [list(r) for r in rows]) for k, (c, rows) in a["tables"].items()}}
        changes: list[dict] = []
        (_change_spec if is_spec else _change_sop)(rng, b, changes)
        pid = f"P{i + 1:02d}"
        number = f"{'SPEC-RM' if is_spec else 'SOP-QA'}-{8000 + i}"
        ha = [("Document No.", number), ("Revision", "01"), ("Effective date", "2025-01-06"),
              ("Approved by", "Grace Okafor")]
        hb = [("Document No.", number), ("Revision", "02"), ("Effective date", "2026-02-02"),
              ("Approved by", "Grace Okafor")]
        (folder / f"{pid}_a.pdf").write_bytes(write_pdf(_lines(a["title"], ha, a["sections"], a["tables"])))
        (folder / f"{pid}_b.pdf").write_bytes(write_pdf(_lines(b["title"], hb, b["sections"], b["tables"],
                                                               noise=True)))
        out["pairs"].append({"id": pid, "doc_number": number, "kind": "SPEC" if is_spec else "SOP",
                             "a": f"{pid}_a.pdf", "b": f"{pid}_b.pdf", "changes": changes})
    # three procedures that each break the standard in a known way
    breaks = [("S01", "no Records section", lambda s, h: ([x for x in s if x[0] != "Records"], h), "STD-4", "missing"),
              ("S02", "no approver named", lambda s, h: (s, [x for x in h if x[0] != "Approved by"]), "STD-6",
               "missing"),
              ("S03", "responsibilities left as TBD",
               lambda s, h: ([[x[0], ["TBD"]] if x[0] == "Responsibilities" else x for x in s], h), "STD-3",
               "uncertain")]
    for sid, what, edit, clause, verdict in breaks:
        d = _sop(rng, 30 + len(out["standard_checks"]))
        d["sections"].append(["Revision history", ["01 first issue."]])
        hdr = [("Document No.", f"SOP-QA-90{sid[1:]}"), ("Revision", "01"), ("Approved by", "Grace Okafor")]
        secs, hdr = edit(d["sections"], hdr)
        (folder / f"{sid}.pdf").write_bytes(write_pdf(_lines(d["title"], hdr, secs, {})))
        out["standard_checks"].append({"id": sid, "file": f"{sid}.pdf", "breaks": clause, "expected": verdict,
                                       "what": what})
    (folder / "pairs.yaml").write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out
