"""Token counts from a LangChain chat message, in the shape generation spans record.

    span.set(**usage(msg))

input_tokens includes cached input; cached_input_tokens says how much of it was
cached, which bills at a lower rate. output_tokens includes reasoning tokens;
reasoning_tokens says how many. Missing counts are 0, never guessed.
"""

from __future__ import annotations


def usage(msg) -> dict:
    u = getattr(msg, "usage_metadata", None) or {}
    inp, out = u.get("input_token_details") or {}, u.get("output_token_details") or {}
    return {
        "input_tokens": int(u.get("input_tokens", 0) or 0),
        "output_tokens": int(u.get("output_tokens", 0) or 0),
        "cached_input_tokens": int(inp.get("cache_read", 0) or 0),
        "reasoning_tokens": int(out.get("reasoning", 0) or 0),
    }


def model_name(llm) -> str:
    """The configured model a chat model calls, as a price list names it."""
    return getattr(llm, "model_name", None) or getattr(llm, "model", None) or type(llm).__name__
