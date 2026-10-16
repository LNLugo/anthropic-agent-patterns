from pathlib import Path

import pytest

from rag.data import load_cuad

FIXTURE = Path(__file__).parent / "fixtures" / "mini_cuad.json"


@pytest.fixture
def mini():
    return load_cuad(FIXTURE)


@pytest.fixture
def contract(mini):
    return mini[0][0]


@pytest.fixture
def queries(mini):
    return {q.category: q for q in mini[1]}


@pytest.fixture(autouse=True)
def traces():
    """Every test runs with an in-memory tracer, so no test writes a trace file."""
    from rag.ledger import get_tracer  # noqa: F401  puts 10_observability on the path
    from tracing import InMemoryExporter, Tracer, set_tracer

    exporter = InMemoryExporter()
    previous = set_tracer(Tracer([exporter]))
    yield exporter
    set_tracer(previous)
