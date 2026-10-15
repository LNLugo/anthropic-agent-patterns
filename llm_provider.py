"""One place to choose the model for the Phase 1 patterns (01 to 06).

The patterns are model agnostic: the provider is a run setting, not a code
change. This matches the convention in 07_rag/rag/graph.py.

    LLM_PROVIDER   openai (default), anthropic, or scripted (offline tests)
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

LLM_PROVIDER=scripted returns ScriptedChatModel, a deterministic stand-in for
offline tests. It replays steps a test loads, records every call, raises
UnexpectedModelCall when a call has no step left to answer it, and raises
UnconsumedModelSteps when a test ends with steps unused. The contract follows
ScriptedModel in openai/openai-agents-python (src/agents/testing/model.py):
the same error names, `calls`, `remaining_steps` and `assert_complete()`.
One addition, `Step(match=...)`, lets parallel branches be scripted without
depending on which thread calls first.
"""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

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
    if provider == "scripted":
        return scripted_model()
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}; choose openai, anthropic or scripted")


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


# ---------------------------------------------------------------- scripted model for offline tests

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from pydantic import BaseModel, PrivateAttr  # noqa: E402


class ModelScriptError(Exception):
    """Base error for an invalid or incompletely consumed model script."""


class InvalidModelStep(ModelScriptError):
    """A step cannot be replayed (an unsupported reply type)."""

    def __init__(self, message: str, *, input_index: int) -> None:
        super().__init__(message)
        self.input_index = input_index


class UnexpectedModelCall(ModelScriptError):
    """The model was called and no remaining step could answer the call."""

    def __init__(self, message: str, *, call: "ScriptedCall", call_index: int) -> None:
        super().__init__(message)
        self.call, self.call_index = call, call_index


class UnconsumedModelSteps(ModelScriptError):
    """A test finished before every scripted step was used."""

    def __init__(self, message: str, *, remaining_steps: int) -> None:
        super().__init__(message)
        self.remaining_steps = remaining_steps


@dataclass(frozen=True)
class ScriptedCall:
    """One recorded call: the prompt text, the messages, and the structured output schema if any."""

    prompt: str
    messages: tuple[BaseMessage, ...]
    schema: str | None = None


@dataclass(frozen=True)
class Step:
    """One scripted reply.

    reply      str, dict or list (sent as JSON text), a pydantic model, an AIMessage,
               or an Exception to raise. For with_structured_output calls, a dict or
               JSON string is validated into the requested schema.
    match      if set, the step answers only a call whose prompt contains this text.
               Steps are tried in order, so unmatched steps behave as a plain queue.
    responder  builds the reply from the ScriptedCall instead of a fixed value.
    """

    reply: Any = None
    match: str | None = None
    responder: Callable[[ScriptedCall], Any] | None = None


_REPLY_TYPES = (str, dict, list, BaseModel, AIMessage, Exception)


def _coerce(item: Any, index: int) -> Step:
    step = item if isinstance(item, Step) else Step(reply=item)
    if step.responder is None and not isinstance(step.reply, _REPLY_TYPES):
        raise InvalidModelStep(
            f"step {index}: reply must be one of str, dict, list, pydantic model, AIMessage or "
            f"Exception, or the step needs a responder; got {type(step.reply).__name__}",
            input_index=index,
        )
    return step


def _as_text(reply: Any) -> str:
    if isinstance(reply, str):
        return reply
    if isinstance(reply, BaseModel):
        return reply.model_dump_json()
    return json.dumps(reply)


class ScriptedChatModel(BaseChatModel):
    """A LangChain chat model that replays scripted steps and records every call."""

    _steps: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)
    _tools: tuple = PrivateAttr(default=())
    _lock: Any = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    # ------------------------------------------------ script

    def load(self, steps: Iterable[Any]) -> None:
        """Replace the script and clear recorded calls and bound tools."""
        coerced = [_coerce(s, i) for i, s in enumerate(steps)]
        with self._lock:
            self._steps, self._calls, self._tools = coerced, [], ()

    def extend(self, steps: Iterable[Any]) -> None:
        start = self.remaining_steps
        coerced = [_coerce(s, start + i) for i, s in enumerate(steps)]
        with self._lock:
            self._steps.extend(coerced)

    def reset(self) -> None:
        """Clear the script and the recorded calls."""
        self.load(())

    def discard_steps(self) -> None:
        """Drop unused steps but keep the recorded calls for inspection."""
        with self._lock:
            self._steps = []

    @property
    def calls(self) -> tuple[ScriptedCall, ...]:
        with self._lock:
            return tuple(self._calls)

    @property
    def remaining_steps(self) -> int:
        with self._lock:
            return len(self._steps)

    def assert_complete(self) -> None:
        """Raise when scripted steps remain unused."""
        remaining = self.remaining_steps
        if remaining:
            raise UnconsumedModelSteps(
                f"{remaining} scripted model step(s) were not consumed.", remaining_steps=remaining
            )

    # ------------------------------------------------ calls

    def _next(self, messages: list[BaseMessage], schema: str | None) -> Any:
        prompt = "\n".join(str(m.content) for m in messages)
        call = ScriptedCall(prompt=prompt, messages=tuple(messages), schema=schema)
        with self._lock:
            self._calls.append(call)
            index = len(self._calls) - 1
            for i, step in enumerate(self._steps):
                if step.match is None or step.match in prompt:
                    del self._steps[i]
                    break
            else:
                head = prompt[:120].replace("\n", " ")
                raise UnexpectedModelCall(
                    f"Unexpected model call #{index + 1}: no scripted step answers it. Prompt starts: {head!r}",
                    call=call, call_index=index,
                )
        reply = step.responder(call) if step.responder is not None else step.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        reply = self._next(list(messages), None)
        message = reply if isinstance(reply, AIMessage) else AIMessage(content=_as_text(reply))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        """Record the tool names offered; replies come from the script. To script a
        tool call, use an AIMessage step with tool_calls."""
        names = []
        for t in tools:
            fn = t.get("function", t) if isinstance(t, dict) else None
            names.append(fn["name"] if fn else getattr(t, "name", str(t)))
        with self._lock:
            self._tools = tuple(names)
        return self

    @property
    def bound_tools(self) -> tuple[str, ...]:
        with self._lock:
            return self._tools

    def with_structured_output(self, schema, **kwargs):
        name = getattr(schema, "__name__", None) or str(schema)

        def run(model_input):
            messages = self._convert_input(model_input).to_messages()
            reply = self._next(messages, name)
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                if isinstance(reply, schema):
                    return reply
                if isinstance(reply, str):
                    return schema.model_validate_json(reply)
                if isinstance(reply, BaseModel):
                    return schema.model_validate(reply.model_dump())
                return schema.model_validate(reply)
            return json.loads(reply) if isinstance(reply, str) else reply

        return RunnableLambda(run, name=f"scripted_structured_{name}")


_SCRIPTED: ScriptedChatModel | None = None


def scripted_model() -> ScriptedChatModel:
    """The one ScriptedChatModel instance. Modules that build their model at import
    time (and derive structured-output runnables from it) all share it, so a test
    loads its script into this instance rather than patching module globals."""
    global _SCRIPTED
    if _SCRIPTED is None:
        _SCRIPTED = ScriptedChatModel()
    return _SCRIPTED


@contextmanager
def scripted(steps: Iterable[Any]) -> Iterator[ScriptedChatModel]:
    """Load a script for the duration of a block, and check it was fully used.

        with scripted(["News", "Analysis of the announcement."]) as model:
            app.invoke({"text": "..."})
        assert model.calls[0].prompt.startswith("Classify")

    On a clean exit, unused steps raise UnconsumedModelSteps. If the block itself
    raises, that error propagates unmasked. Unused steps are dropped either way;
    recorded calls stay readable until the next script is loaded."""
    model = scripted_model()
    model.load(steps)
    try:
        yield model
        model.assert_complete()
    finally:
        model.discard_steps()
