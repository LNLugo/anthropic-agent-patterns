"""One place to choose the model for the Phase 1 patterns (01 to 06).

The patterns are model agnostic: the provider is a run setting, not a code
change. This matches the convention in 07_rag/rag/graph.py.

    LLM_PROVIDER   openai (default) or anthropic
    CLAUDE_MODEL   default claude-sonnet-5-5   (needs ANTHROPIC_API_KEY)
    OPENAI_MODEL   default gpt-6-luna          (needs OPENAI_API_KEY)
    OPENAI_REASONING_EFFORT  default none, for gpt-6 and later reasoning models

Claude Sonnet 5.5 and OpenAI's reasoning models (gpt-6-luna) take no
temperature setting, so `temperature` is sent only to the older GPT-4 series.
gpt-6-luna runs with reasoning effort "none" by default: on Chat Completions it
supports function calling only at that setting, and these patterns do their
reasoning in the graph, not inside one call.

Both providers return a LangChain chat model, so `llm.invoke(...)`,
`prompt | llm` and `llm.with_structured_output(...)` work unchanged.
"""

from __future__ import annotations

import json
import os
import re

DEFAULT_PROVIDER = "openai"

try:  # load the repo root .env, so every caller sees the keys the Phase 1 scripts see
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def get_chat_model(provider: str | None = None, model: str | None = None, temperature: float = 0):
    provider = (provider or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
            max_tokens=2048,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        name = model or os.environ.get("OPENAI_MODEL", "gpt-6-luna")
        return ChatOpenAI(model=name, **openai_settings(name, temperature))
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}; choose anthropic or openai")


def openai_settings(model: str, temperature: float = 0) -> dict:
    """GPT-4 series models take a temperature; gpt-6 and later reasoning models take a reasoning effort."""
    if model.startswith("gpt-4"):
        return {"temperature": temperature}
    return {"reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", "none")}


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json(content) -> dict:
    """Parse a model's JSON reply. Strips markdown fences and any prose around
    the outermost object, since providers differ in how strictly they follow
    'return only JSON'."""
    if isinstance(content, list):  # content blocks
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    text = _FENCE.sub("", content.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])
