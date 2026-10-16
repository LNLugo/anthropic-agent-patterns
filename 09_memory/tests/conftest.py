"""Every 09 test runs with an in-memory tracer, so no test writes a trace file."""

import pytest

from tracing import InMemoryExporter, Tracer, set_tracer


@pytest.fixture(autouse=True)
def traces():
    exporter = InMemoryExporter()
    previous = set_tracer(Tracer([exporter]))
    yield exporter
    set_tracer(previous)
