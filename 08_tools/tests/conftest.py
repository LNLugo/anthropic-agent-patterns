"""Every 08 test runs with an in-memory tracer, so no test writes a trace file.
Tests that check traces read `traces` (the exporter) or `tracer`."""

import pytest

from agent_tools.observe import get_tracer  # noqa: F401  puts 10_observability on the path
from tracing import InMemoryExporter, Tracer, set_tracer


@pytest.fixture(autouse=True)
def traces():
    exporter = InMemoryExporter()
    previous = set_tracer(Tracer([exporter]))
    yield exporter
    set_tracer(previous)
