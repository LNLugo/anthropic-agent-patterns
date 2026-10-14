"""One place to choose the model for the Phase 1 patterns (01 to 06).

The patterns are model agnostic: the provider is a run setting, not a code
change. This matches the convention in 07_rag/rag/graph.py.

    LLM_PROVIDER   anthropic (default) or openai
    CLAUDE_MODEL   default claude-sonnet-5-5   (needs ANTHROPIC_API_KEY)
    OPENAI_MODEL   default gpt-4o-mini         (needs OPENAI_API_KEY)

Both providers return a LangChain chat model, so `llm.invoke(...)`,
`prompt | llm` and `llm.with_structured_output(...)` work unchanged.
"""

from __future__ import annotations

import json
import os
import re

DEFAULT_PROVIDER = "anthropic"


def get_chat_model(provider: str | None = None, model: str | None = None, temperature: float = 0):
    provider = (provider or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
            temperature=temperature,
            max_tokens=2048,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), temperature=temperature)
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}; choose anthropic or openai")


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
