"""Prompt chaining: a fixed sequence of three calls, every input taking the same path."""

from llm_provider import scripted
from prompt_chaining_01 import app

TEXT = "Anthropic's MCP is an open standard that lets applications reach APIs and tools."


def test_runs_the_fixed_chain_in_order(run_graph):
    with scripted(["Blog", "Anthropic, MCP", "MCP connects apps to tools."]) as model:
        path, state = run_graph(app, {"text": TEXT})

    assert path == ["classification_node", "entity_extraction", "summarization"]
    assert state["classification"] == "Blog"
    assert state["entities"] == ["Anthropic", "MCP"]
    assert state["summary"] == "MCP connects apps to tools."
    prompts = [c.prompt for c in model.calls]
    assert prompts[0].startswith("Classify") and TEXT in prompts[0]
    assert prompts[1].startswith("Extract all the entities")
    assert prompts[2].startswith("Summarize")


def test_the_path_does_not_depend_on_what_the_model_says(run_graph):
    """A chain has no branches: an unexpected classification still runs every step."""
    with scripted(["something unexpected", "none", "short"]):
        path, _ = run_graph(app, {"text": TEXT})
    assert path == ["classification_node", "entity_extraction", "summarization"]
