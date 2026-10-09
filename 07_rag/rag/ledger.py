"""Context ledger: an auditable record of what entered the model's context.

For every query the ledger records, per stage, which chunk ids were injected,
how many characters that was, the tokens the model reported, latency, and
model calls. Cost is computed only from prices you configure, never assumed.

This is the evidence layer the rest of the capability layer builds on:
observability reads it, FinOps prices it, and governance audits it.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field


@dataclass
class StageRecord:
    stage: str
    chunk_ids: list[str] = field(default_factory=list)
    context_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    latency_ms: float = 0.0


@dataclass
class ContextLedger:
    query_id: str
    stages: list[StageRecord] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str):
        rec = StageRecord(name)
        t0 = time.perf_counter()
        try:
            yield rec
        finally:
            rec.latency_ms = (time.perf_counter() - t0) * 1000
            self.stages.append(rec)

    @property
    def input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages)

    @property
    def output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    @property
    def latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.stages)

    @property
    def model_calls(self) -> int:
        return sum(s.model_calls for s in self.stages)

    def cost(self, input_per_mtok: float | None, output_per_mtok: float | None) -> float | None:
        if input_per_mtok is None or output_per_mtok is None:
            return None
        return self.input_tokens / 1e6 * input_per_mtok + self.output_tokens / 1e6 * output_per_mtok

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "stages": [asdict(s) for s in self.stages],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "model_calls": self.model_calls,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
