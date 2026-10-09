"""CUAD loader.

CUAD (Hendrycks et al., NeurIPS 2021, CC BY 4.0) ships in SQuAD format: one
contract per entry, 41 clause-category questions per contract, and lawyer-
annotated answer spans as character offsets. Those offsets are the ground
truth for every retrieval and citation metric in this module.

Download: clone https://github.com/TheAtticusProject/cuad and unzip data.zip.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

_CATEGORY = re.compile(r'related to "([^"]+)"')
_DETAILS = re.compile(r"Details:\s*(.+)$", re.DOTALL)


@dataclass(frozen=True)
class Contract:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class Query:
    id: str
    contract_id: str
    category: str
    details: str
    gold_spans: tuple[tuple[int, int], ...] = field(default_factory=tuple)

    @property
    def answerable(self) -> bool:
        return len(self.gold_spans) > 0

    @property
    def text(self) -> str:
        """Retrieval query: the clause category plus its definition.

        The raw CUAD question is a template ("Highlight the parts (if any) of
        this contract related to ..."); the boilerplate adds noise to lexical
        retrieval, so only the category and its definition are used.
        """
        return f"{self.category}: {self.details}"


def _parse_question(question: str) -> tuple[str, str]:
    cat = _CATEGORY.search(question)
    det = _DETAILS.search(question)
    category = cat.group(1) if cat else question.strip()
    details = det.group(1).strip() if det else ""
    return category, details


def load_cuad(
    path: str | Path,
    n_contracts: int | None = None,
    seed: int = 7,
    categories: list[str] | None = None,
) -> tuple[list[Contract], list[Query]]:
    """Load contracts and queries from a CUAD SQuAD-format JSON file.

    n_contracts samples a fixed, seeded subset so runs are comparable.
    categories, when given, keeps only those clause categories.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["data"]
    if n_contracts is not None and n_contracts < len(raw):
        raw = random.Random(seed).sample(raw, n_contracts)

    contracts: list[Contract] = []
    queries: list[Query] = []
    for i, entry in enumerate(raw):
        for j, para in enumerate(entry["paragraphs"]):
            cid = f"c{i:03d}" if len(entry["paragraphs"]) == 1 else f"c{i:03d}p{j}"
            text = para["context"]
            contracts.append(Contract(id=cid, title=entry["title"], text=text))
            for qa in para["qas"]:
                category, details = _parse_question(qa["question"])
                if categories and category not in categories:
                    continue
                spans = tuple(
                    sorted(
                        {(a["answer_start"], a["answer_start"] + len(a["text"])) for a in qa["answers"]}
                    )
                )
                queries.append(
                    Query(
                        id=f"{cid}::{category}",
                        contract_id=cid,
                        category=category,
                        details=details,
                        gold_spans=spans,
                    )
                )
    return contracts, queries
