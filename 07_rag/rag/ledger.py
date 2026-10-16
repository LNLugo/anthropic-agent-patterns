"""Context ledger: an auditable record of what entered the model's context.

For every query the ledger records, per stage, which chunk ids were injected,
how many characters that was, the tokens the model reported, latency, and
model calls. Cost is computed only from prices you configure, never assumed.

This is the evidence layer the rest of the capability layer builds on:
observability reads it, FinOps prices it, and governance audits it.

Each stage is also a span in the run's trace (10_observability), of the kind
that matches what it does: retrieval is a tool, generation a generation, and
citation validation a guardrail on the model's output. Chunk ids, sizes and
tokens are metadata; chunk text, the prompt and the reply are content, and
stay out of the trace unless sensitive data is enabled.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    from tracing import get_tracer
except ImportError:  # the path setup goes away with the single pyproject
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "10_observability"))
    from tracing import get_tracer

STAGE_KINDS = {"retrieve": "tool", "generate": "generation", "validate": "guardrail"}


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
        with get_tracer().span(STAGE_KINDS.get(name, "custom"), name, query_id=self.query_id) as span:
            t0 = time.perf_counter()
            try:
                yield rec
            finally:
                rec.latency_ms = (time.perf_counter() - t0) * 1000
                self.stages.append(rec)
                span.set(chunk_ids=rec.chunk_ids, context_chars=rec.context_chars, input_tokens=rec.input_tokens,
                         output_tokens=rec.output_tokens, model_calls=rec.model_calls)

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
