"""Phase 1 offline test setup.

Every pattern module builds its model at import time with get_chat_model(), so
the provider is set to "scripted" here, before any test module imports a
pattern. No test can reach a live model: an unscripted call raises
UnexpectedModelCall.
"""

import os

os.environ["LLM_PROVIDER"] = "scripted"  # before any pattern module is imported

import pytest  # noqa: E402

from llm_provider import scripted_model  # noqa: E402


@pytest.fixture(autouse=True)
def traces():
    """Every test runs with an in-memory tracer, so no test writes a trace file."""
    from tracing import InMemoryExporter, Tracer, set_tracer

    exporter = InMemoryExporter()
    previous = set_tracer(Tracer([exporter]))
    yield exporter
    set_tracer(previous)


@pytest.fixture(autouse=True)
def _clean_script():
    """No script or recorded call leaks from one test into the next."""
    scripted_model().reset()
    yield
    scripted_model().reset()


def graph_path(app, inputs):
    """Run a compiled graph and return (nodes in the order they finished, final state)."""
    path, state = [], None
    for mode, chunk in app.stream(inputs, stream_mode=["updates", "values"]):
        if mode == "updates":
            path.extend(chunk)
        else:
            state = chunk
    return path, state


@pytest.fixture
def run_graph():
    return graph_path
