"""Classification (s6): rules first, a model only where rules measurably fall short.

The rules score each type in Quality's taxonomy from the signals a document carries: a
number in its header matching the type's pattern, the line that says what the document
is, a title phrase, the file format, and (weakly) the source's own type field. The best
score is the confidence. Below the taxonomy's threshold, the document goes to intake
review: nothing is filed on a guess.

`classify_with_model` is the same decision made by a model with a constrained output
schema. It is built and tested as plumbing only: a reply outside the schema, an unknown
type or a low confidence goes to intake review. Its quality is not claimed here; that
needs a held-out set and a live run (ADR 0013).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .parse import Parsed

TAXONOMY = Path(__file__).resolve().parents[1] / "taxonomy.yaml"


@lru_cache(maxsize=1)
def taxonomy(path: Path = TAXONOMY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Classification:
    doc_type: str | None
    confidence: float
    signals: tuple[str, ...]
    by: str                         # "rules" or "model"
    review: bool
    reason: str = ""


def header_values(doc: Parsed) -> list[str]:
    return [x.text.split(":", 1)[1].strip() for x in doc.header if ":" in x.text]


def classify(doc: Parsed, type_hint: str = "") -> Classification:
    tax = taxonomy()
    w, threshold = tax["classification"]["weights"], tax["classification"]["threshold"]
    values = header_values(doc)
    scores = {}
    for name, t in tax["types"].items():
        score, signals = 0.0, []
        if t.get("number") and any(re.match(t["number"], v) for v in values):
            score += w["number"]
            signals.append("number")
        if t.get("kind_line") and doc.kind_line.upper() == t["kind_line"]:
            score += w["kind_line"]
            signals.append("kind_line")
        if any(p in doc.title.lower() for p in t.get("titles", [])):
            score += w["title"]
            signals.append("title")
        if t.get("format") and doc.fmt == t["format"]:
            score += w["format"]
            signals.append("format")
        if type_hint == name:
            score += w["metadata"]
            signals.append("metadata")
        scores[name] = (round(min(score, 1.0), 3), tuple(signals))
    best = max(scores, key=lambda k: (scores[k][0], k))
    conf, signals = scores[best]
    if conf < threshold:
        return Classification(None, conf, signals, "rules", True, f"best score {conf} for {best}, below {threshold}")
    return Classification(best, conf, signals, "rules", False)


PROMPT = """Classify this quality document into exactly one type: {types}.
Reply with JSON only: {{"doc_type": "<type>", "confidence": <0 to 1>, "rationale": "<one sentence>"}}.

Title: {title}
First lines:
{lines}"""


def classify_with_model(doc: Parsed, llm) -> Classification:
    """The model's classification, accepted only if it fits the schema and clears the threshold."""
    from llm_provider import parse_json

    tax = taxonomy()
    first = "\n".join(x.text for x in doc.all_lines()[:12])
    msg = llm.invoke(PROMPT.format(types=", ".join(tax["types"]), title=doc.title, lines=first))
    try:
        out = parse_json(msg.content)
        doc_type, conf = out["doc_type"], float(out["confidence"])
    except (ValueError, KeyError, TypeError) as e:
        return Classification(None, 0.0, (), "model", True, f"reply outside the schema: {type(e).__name__}")
    if doc_type not in tax["types"]:
        return Classification(None, 0.0, (), "model", True, f"unknown type {doc_type!r}")
    if not 0.0 <= conf <= 1.0:
        return Classification(None, 0.0, (), "model", True, f"confidence {conf} outside 0 to 1")
    if conf < tax["classification"]["threshold"]:
        return Classification(None, conf, (), "model", True, f"confidence {conf} below threshold")
    return Classification(doc_type, conf, (), "model", False)
