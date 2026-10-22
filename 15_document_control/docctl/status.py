"""The build status table, rendered from status.yaml for the README and the app."""

from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
STATUS = HERE / "status.yaml"
BEGIN, END = "<!-- status:begin (rendered from status.yaml) -->", "<!-- status:end -->"


def load(path: Path = STATUS) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _proof(items) -> str:
    out = []
    for p in items or []:
        name = p.split("::")[-1] if "::" in p else p
        out.append(f"`{name}`")
    return ", ".join(out)


def table(data: dict | None = None) -> str:
    data = data or load()
    rows = ["| § | Section | Status | PR | Proof | Note |", "|---|---|---|---|---|---|"]
    for s in data["sections"]:
        rows.append(f"| {s['section']} | {s['title']} | {s['status']} | {s['pr']} | {_proof(s.get('proof'))} | "
                    f"{s.get('note', '')} |")
    return "\n".join(rows)


def stand_ins(data: dict | None = None) -> str:
    data = data or load()
    rows = ["| Managed service | Stand-in here |", "|---|---|"]
    rows += [f"| {x['service']} | {x['stand_in']} |" for x in data["stand_ins"]]
    return "\n".join(rows)


def block() -> str:
    return f"{BEGIN}\n{table()}\n\n**Stand-ins (ADR 0015).**\n\n{stand_ins()}\n{END}"
