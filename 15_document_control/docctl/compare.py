"""Version against version (s10): a deterministic diff finds every change; something else
classifies it.

    align     sections matched by heading, so a renumbered or re-paginated revision still lines up
    diff      text sections compared sentence by sentence after normalising whitespace and
              line wrapping, so re-wrapped paragraphs and moved page breaks are not changes;
              tables compared row by row, keyed on the first cell
    classify  each change typed (editorial, procedural, specification_or_limit, safety_relevant)
              and given a severity, by one of:
                rules   the non-model baseline
                model   one call per change, with both sides quoted
    direct    the alternative the headline result measures: the model reads both revisions
              and reports the changes itself, with no diff

A change found by the diff cannot be missed by the classifier: the diff guarantees coverage,
and classification only decides what kind of change it is. That is the design's claim (s10,
"missed change: deterministic diff guarantees coverage, LLM only classifies"), and the
headline result measures it against asking the model to do both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .parse import Parsed

TYPES = ("editorial", "procedural", "specification_or_limit", "safety_relevant")
SEVERITIES = ("low", "medium", "high")
SAFETY = re.compile(r"\b(glove|gloves|glasses|goggles|ventilation|exhaust|flammable|ignition|hazard|respirator|"
                    r"ppe|burn|toxic)\b", re.I)
SENTENCE = re.compile(r"(?<=\.)\s+(?=\S)")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class Change:
    section: str
    before: str
    after: str
    cite_a: str              # where it is in the older revision ("P01_a#5 Procedure"), or "" if added
    cite_b: str              # where it is in the newer revision, or "" if removed
    row: str | None = None   # the table row's key, for a table change

    @property
    def kind(self) -> str:
        return "added" if not self.before else "removed" if not self.after else "modified"


@dataclass
class Finding:
    change: Change
    type: str | None
    severity: str | None
    explanation: str = ""
    confidence: float | None = None
    by: str = "rules"
    citations: list[str] = field(default_factory=list)


def _units(doc: Parsed) -> dict[str, tuple[str, list[str], dict[str, list[str]]]]:
    """Per section heading: its path, its sentences, and its table rows by first cell."""
    out = {}
    for s in doc.sections:
        text = norm(" ".join(x.text for x in s.lines))
        sentences = [norm(x) for x in SENTENCE.split(text) if norm(x)] if text else []
        rows = {}
        for t in s.tables:
            for r in t.rows:
                rows[r[0]] = [norm(c) for c in r]
        out[s.heading.lower()] = (s.path, sentences, rows, [t.header for t in s.tables])
    return out


def _pair(olds: list[str], news: list[str]) -> list[tuple[str, str]]:
    """Inside a changed block, pair each old sentence with the most similar new one (if at
    least half the same); the rest are removals and additions. Pairing by position would turn
    "step 2 removed, step 3 reworded" into "step 2 became step 3"."""
    if len(olds) == 1 and len(news) == 1:          # one sentence replaced in place: a replacement
        return [(olds[0], news[0])]
    pairs, used = [], set()
    for o in olds:
        best, score = None, 0.5
        for j, n in enumerate(news):
            r = SequenceMatcher(None, o, n).ratio()
            if j not in used and r >= score:
                best, score = j, r
        if best is None:
            pairs.append((o, ""))
        else:
            used.add(best)
            pairs.append((o, news[best]))
    pairs += [("", n) for j, n in enumerate(news) if j not in used]
    return pairs


def diff(a: Parsed, b: Parsed, name_a: str = "a", name_b: str = "b") -> list[Change]:
    ua, ub = _units(a), _units(b)
    changes: list[Change] = []
    for key in list(ua) + [k for k in ub if k not in ua]:
        pa, sa, ra, _ = ua.get(key, ("", [], {}, []))
        pb, sb, rb, _ = ub.get(key, ("", [], {}, []))
        heading = (pa or pb).split(" ", 1)[1] if (pa or pb) else key
        ca, cb = f"{name_a}#{pa}" if pa else "", f"{name_b}#{pb}" if pb else ""
        for op, i1, i2, j1, j2 in SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            for o, n in _pair(sa[i1:i2], sb[j1:j2]):
                changes.append(Change(heading, o, n, ca if o else "", cb if n else ""))
        for row in list(ra) + [r for r in rb if r not in ra]:
            o, n = ra.get(row), rb.get(row)
            if o != n:
                changes.append(Change(heading, " | ".join(o) if o else "", " | ".join(n) if n else "",
                                      ca if o else "", cb if n else "", row))
    return changes


# ---------------------------------------------------------------- the rules baseline

def _range(cell: str) -> tuple[float, float] | None:
    m = re.match(r"\s*([-\d.]+)\s*-\s*([-\d.]+)\s*$", cell)
    return (float(m.group(1)), float(m.group(2))) if m else None


def classify_rules(c: Change) -> Finding:
    """The non-model baseline. Table rows: a widened limit or a changed unit is high, a tightened
    limit or changed method medium. Text: anything touching protective or hazard words is
    safety-relevant; a near-identical sentence with no number changed is editorial; the rest
    is procedural."""
    if c.row is not None and c.before and c.after:
        o, n = c.before.split(" | "), c.after.split(" | ")
        if len(o) >= 3 and len(n) >= 3:
            if o[2] != n[2]:
                return Finding(c, "specification_or_limit", "high", "unit changed")
            ro, rn = _range(o[1]), _range(n[1])
            if ro and rn and ro != rn:
                widened = rn[0] < ro[0] or rn[1] > ro[1]
                return Finding(c, "specification_or_limit", "high" if widened else "medium",
                               "limit widened" if widened else "limit tightened")
            return Finding(c, "specification_or_limit", "medium", "method or other column changed")
    if c.row is not None:
        return Finding(c, "specification_or_limit", "high", "parameter added or removed")
    if SAFETY.search(c.before) or SAFETY.search(c.after):
        return Finding(c, "safety_relevant", "high", "protective or hazard instruction")
    if c.before and c.after:
        digits = re.findall(r"\d+", c.before) == re.findall(r"\d+", c.after)
        wa, wb = c.before.lower().split(), c.after.lower().split()
        changed = sum(1 for op in SequenceMatcher(None, wa, wb).get_opcodes() if op[0] != "equal")
        if digits and changed <= 1 and SequenceMatcher(None, c.before, c.after).ratio() >= 0.85:
            return Finding(c, "editorial", "low", "wording only")
    return Finding(c, "procedural", "medium", "step or responsibility changed")


# ---------------------------------------------------------------- the model, after the diff

CLASSIFY_PROMPT = """You review a change between two revisions of a quality document.
Section: {section}
Before: {before}
After: {after}

Classify it. Types: editorial (wording only, nobody does anything differently), procedural (a step
or responsibility changed), specification_or_limit (a limit, unit or test method changed),
safety_relevant (a protective or hazard instruction changed). Severity: low, medium or high
(a widened limit, a changed unit or a weakened safety instruction is high).
Reply with JSON only: {{"type": "...", "severity": "...", "explanation": "<one sentence>", "confidence": <0 to 1>}}"""


def classify_model(c: Change, llm) -> Finding:
    from llm_provider import parse_json

    msg = llm.invoke(CLASSIFY_PROMPT.format(section=c.section, before=c.before or "(none: added)",
                                            after=c.after or "(none: removed)"))
    try:
        out = parse_json(msg.content)
        t, s = out.get("type"), out.get("severity")
        conf = float(out.get("confidence", 0))
    except (ValueError, TypeError, AttributeError):
        return Finding(c, None, None, "reply outside the schema", None, "model")
    if t not in TYPES or s not in SEVERITIES:
        return Finding(c, None, None, f"not an allowed type or severity: {t!r}, {s!r}", conf, "model")
    return Finding(c, t, s, str(out.get("explanation", "")), conf, "model", [x for x in (c.cite_a, c.cite_b) if x])


# ---------------------------------------------------------------- the model, alone

DIRECT_PROMPT = """You compare two revisions of a quality document and list every change that matters.
Ignore formatting: line wrapping, spacing and page breaks are not changes.

REVISION A (older):
{a}

REVISION B (newer):
{b}

Reply with JSON only:
{{"findings": [{{"section": "<heading>", "before": "<exact text from A, or empty if added>",
   "after": "<exact text from B, or empty if removed>", "type": "editorial|procedural|specification_or_limit|safety_relevant",
   "severity": "low|medium|high", "explanation": "<one sentence>"}}]}}"""


def _text(doc: Parsed) -> str:
    out = []
    for s in doc.sections:
        out.append(f"## {s.heading}")
        out += [x.text for x in s.lines]
        for t in s.tables:
            out += [" | ".join(t.header)] + [" | ".join(r) for r in t.rows]
    return "\n".join(out)


def compare_direct(a: Parsed, b: Parsed, llm) -> list[Finding]:
    from llm_provider import parse_json

    msg = llm.invoke(DIRECT_PROMPT.format(a=_text(a), b=_text(b)))
    try:
        items = parse_json(msg.content).get("findings", [])
    except (ValueError, TypeError, AttributeError):
        return []
    out = []
    for f in items if isinstance(items, list) else []:
        if not isinstance(f, dict):
            continue
        c = Change(str(f.get("section", "")), norm(str(f.get("before", ""))), norm(str(f.get("after", ""))), "", "")
        t, s = f.get("type"), f.get("severity")
        out.append(Finding(c, t if t in TYPES else None, s if s in SEVERITIES else None,
                           str(f.get("explanation", "")), None, "model-direct"))
    return out


# ---------------------------------------------------------------- scoring against the planted changes

def _similar(x: str, y: str) -> float:
    x, y = norm(x).lower(), norm(y).lower()
    if not x and not y:
        return 1.0
    return SequenceMatcher(None, x, y).ratio()


def match(findings: list[Finding], planted: list[dict]) -> tuple[list[tuple[dict, Finding]], list[Finding], list[dict]]:
    """One-to-one: a finding matches a planted change in the same section whose before and after
    texts it reproduces (0.8 similarity, so a model quoting with a small slip still counts)."""
    pairs, used = [], set()
    for p in planted:
        best, score = None, 0.0
        for i, f in enumerate(findings):
            if i in used or f.change.section.lower() != p["section"].lower():
                continue
            s = min(_similar(f.change.before, p["before"]), _similar(f.change.after, p["after"]))
            if s > score:
                best, score = i, s
        if best is not None and score >= 0.8:
            used.add(best)
            pairs.append((p, findings[best]))
    extra = [f for i, f in enumerate(findings) if i not in used]
    missed = [p for p in planted if not any(p is q for q, _ in pairs)]
    return pairs, extra, missed


def score(findings: list[Finding], planted: list[dict]) -> dict:
    pairs, extra, missed = match(findings, planted)
    return {"planted": len(planted), "found": len(pairs), "false_findings": len(extra),
            "type_correct": sum(1 for p, f in pairs if f.type == p["type"]),
            "severity_correct": sum(1 for p, f in pairs if f.severity == p["severity"]),
            "missed": [{"section": p["section"], "type": p["type"]} for p in missed]}
