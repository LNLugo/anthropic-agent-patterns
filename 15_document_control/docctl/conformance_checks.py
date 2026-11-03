"""The other two comparison modes (s10).

**Certificate against specification, by rules.** A supplier's certificate of analysis is
checked against the specification in force on the day the lot was made: every result
against its limit, every specified test present. A model does not decide pass or fail; the
numbers do. Findings cite both documents (the certificate's results and the specification's
limits, each by document, revision and section).

**Document against standard.** Each clause of a standard is checked: the section it asks
for must exist and say something. A clause is covered, missing, or uncertain (the section
is there but empty or a placeholder). Uncertain is never passed. On a scan with lines OCR read
with low confidence, an apparent absence is uncertain, not missing: the heading may be there and
misread, so the check cannot claim it is absent. A model may be asked as
well, but a "covered" verdict without a quote that is really in the document is downgraded
to uncertain: false assurance is the expensive failure here (s10).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parse import Parsed

PLACEHOLDERS = {"tbd", "tba", "to be defined", "n/a", "-", "pending"}


@dataclass
class CoaFinding:
    kind: str                      # out_of_specification, test_missing, test_not_in_specification, no_specification
    parameter: str | None
    value: str | None
    limit: list[str] | None
    coa: str
    spec: str | None
    citations: list[str] = field(default_factory=list)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def specification_for(material: str, made: str, records: dict) -> tuple[str, object] | None:
    """The specification revision in force when the lot was made: the latest one effective on
    or before that date."""
    best = None
    for key, r in records.items():
        if r.doc_type != "SPEC" or "material_id" not in r.fields or r.fields["material_id"].value != material:
            continue
        eff = r.fields.get("effective_date")
        if eff is None or str(eff.value) > made:
            continue
        if best is None or str(eff.value) > str(best[1].fields["effective_date"].value):
            best = (key, r)
    return best


def check_coa(key: str, coa, records: dict) -> list[CoaFinding]:
    f = coa.fields
    material, made = f.get("material_id"), f.get("manufactured")
    if material is None or made is None or "results" not in f:
        return [CoaFinding("no_specification", None, None, None, key, None)]
    spec = specification_for(str(material.value), str(made.value), records)
    if spec is None:
        return [CoaFinding("no_specification", None, None, None, key, None)]
    skey, s = spec
    limits, results = s.fields["limits"].value, f["results"].value
    cite = [c.chunk_id for c in coa.chunks if "Results" in c.section_path] + \
           [c.chunk_id for c in s.chunks if "Limits" in c.section_path]
    out = []
    for p, (lo, hi) in limits.items():
        if p not in results:
            out.append(CoaFinding("test_missing", p, None, [lo, hi], key, skey, cite))
            continue
        v, a, b = _f(results[p]), _f(lo), _f(hi)
        if v is None or a is None or b is None or not a <= v <= b:
            out.append(CoaFinding("out_of_specification", p, results[p], [lo, hi], key, skey, cite))
    for p in results:
        if p not in limits:
            out.append(CoaFinding("test_not_in_specification", p, results[p], None, key, skey, cite))
    return out


# ---------------------------------------------------------------- against a standard

@dataclass(frozen=True)
class ClauseResult:
    clause: str
    verdict: str                   # covered, missing, uncertain
    evidence: str                  # the section or header line that covers it, or why not
    by: str = "rules"


OCR_DOUBT = 0.90          # below this, a line may have been misread


def check_standard(doc: Parsed, clauses: list[dict]) -> list[ClauseResult]:
    shaky = sum(1 for x in doc.all_lines() if x.confidence is not None and x.confidence < OCR_DOUBT)
    return [ClauseResult(r.clause, "uncertain", f"{r.evidence}; {shaky} lines read with low OCR confidence", r.by)
            if r.verdict == "missing" and shaky else r for r in _check(doc, clauses)]


def _check(doc: Parsed, clauses: list[dict]) -> list[ClauseResult]:
    sections = {s.heading.lower(): s for s in doc.sections}
    header = {x.text.split(":", 1)[0].strip().lower(): x.text for x in doc.header if ":" in x.text}
    out = []
    for c in clauses:
        if "header" in c:
            line = header.get(c["header"].lower())
            value = line.split(":", 1)[1].strip() if line else ""
            if not line:
                out.append(ClauseResult(c["id"], "missing", f"no '{c['header']}' line"))
            elif value.lower() in PLACEHOLDERS:
                out.append(ClauseResult(c["id"], "uncertain", f"'{line}' is a placeholder"))
            else:
                out.append(ClauseResult(c["id"], "covered", line))
            continue
        s = sections.get(c["heading"].lower())
        if s is None:
            out.append(ClauseResult(c["id"], "missing", f"no '{c['heading']}' section"))
            continue
        text = " ".join(x.text for x in s.lines).strip()
        if not (text or s.tables) or text.lower().strip(". ") in PLACEHOLDERS:
            out.append(ClauseResult(c["id"], "uncertain", f"section {s.path} is empty or a placeholder"))
        elif any(w not in text.lower() for w in c.get("words", [])):
            out.append(ClauseResult(c["id"], "uncertain", f"section {s.path} does not say {c['words']}"))
        else:
            out.append(ClauseResult(c["id"], "covered", f"section {s.path}"))
    return out


def accept_model_verdict(verdict: str, quote: str, doc: Parsed) -> str:
    """A model's 'covered' stands only with a quote that is really in the document; otherwise it
    is uncertain. 'missing' and 'uncertain' are accepted as they are: they go to a person."""
    if verdict not in ("covered", "missing", "uncertain"):
        return "uncertain"
    if verdict == "covered":
        q = " ".join(quote.split()).lower()
        return "covered" if len(q) >= 12 and q in " ".join(doc.text().split()).lower() else "uncertain"
    return verdict
