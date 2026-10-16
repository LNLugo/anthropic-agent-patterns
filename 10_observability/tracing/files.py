"""Reading trace files back: what the audit report and the cost report both start from."""

from __future__ import annotations

import json
from pathlib import Path


def load_traces(folder: str | Path) -> tuple[dict, list[dict]]:
    """Every traces-*.jsonl under folder, as ({trace_id: trace record}, [span records]).
    A trace is written when it starts and again when it ends; the last record wins."""
    traces, spans = {}, []
    for path in sorted(Path(folder).rglob("traces-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["type"] == "trace":
                traces[r["trace_id"]] = r
            else:
                spans.append(r)
    return traces, spans


def identity(trace: dict, spans: list[dict] = ()) -> str:
    """Who a run was for: the caller, the approver deciding, or an MCP token's subject
    (which is in the token check, not the trace metadata)."""
    for s in spans:
        if s["kind"] == "guardrail" and s["name"] == "token_scope" and s["data"].get("subject"):
            return s["data"]["subject"]
    m = trace.get("metadata") or {}
    return m.get("caller") or m.get("approver") or m.get("subject") or f"({trace['workflow']})"
