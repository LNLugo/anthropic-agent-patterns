"""The scripted model's contract: replay in order, record calls, fail loudly."""

import threading
from typing import Literal

import pytest
from langchain_core.messages import AIMessage
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, ValidationError

from llm_provider import (
    InvalidModelStep,
    Step,
    UnconsumedModelSteps,
    UnexpectedModelCall,
    get_chat_model,
    parse_json,
    scripted,
    scripted_model,
)


class Route(BaseModel):
    route: Literal["news", "other"]


def test_provider_setting_returns_the_shared_instance():
    assert get_chat_model() is scripted_model()
    assert get_chat_model("scripted") is scripted_model()


def test_replays_in_order_and_records_prompts():
    llm = scripted_model()
    with scripted(["first", {"tasks": ["a"]}]) as model:
        assert llm.invoke("one").content == "first"
        reply = (PromptTemplate.from_template("two {x}") | llm).invoke({"x": 2})
        assert parse_json(reply.content) == {"tasks": ["a"]}
    assert [c.prompt for c in model.calls] == ["one", "two 2"]


def test_structured_output_validates_into_the_schema():
    router = scripted_model().with_structured_output(Route)
    with scripted([{"route": "news"}, '{"route": "other"}']) as model:
        assert router.invoke("a") == Route(route="news")
        assert router.invoke("b") == Route(route="other")
    assert [c.schema for c in model.calls] == ["Route", "Route"]


def test_structured_output_rejects_a_value_outside_the_schema():
    router = scripted_model().with_structured_output(Route)
    with pytest.raises(ValidationError), scripted([{"route": "finance"}]):
        router.invoke("a")


def test_a_call_with_no_step_left_raises():
    with pytest.raises(UnexpectedModelCall) as err, scripted([]):
        scripted_model().invoke("hello")
    assert err.value.call_index == 0 and err.value.call.prompt == "hello"


def test_unused_steps_raise_at_the_end_of_the_block():
    with pytest.raises(UnconsumedModelSteps) as err, scripted(["used", "unused"]):
        scripted_model().invoke("x")
    assert err.value.remaining_steps == 1


def test_an_error_in_the_block_is_not_masked_by_unused_steps():
    with pytest.raises(KeyError), scripted(["unused"]):
        raise KeyError("the real failure")
    assert scripted_model().remaining_steps == 0


def test_invalid_steps_are_refused_before_anything_runs():
    with pytest.raises(InvalidModelStep) as err:
        scripted_model().load(["ok", 42])
    assert err.value.input_index == 1


def test_exception_steps_raise_and_responders_see_the_call():
    with scripted([TimeoutError("provider down"), Step(responder=lambda c: f"echo: {c.prompt}")]):
        with pytest.raises(TimeoutError):
            scripted_model().invoke("a")
        assert scripted_model().invoke("b").content == "echo: b"


def test_ai_message_steps_pass_through():
    with scripted([AIMessage(content="raw")]):
        assert scripted_model().invoke("a").content == "raw"


def test_match_steps_answer_parallel_calls_whatever_order_they_arrive_in():
    llm, out = scripted_model(), {}

    def call(topic):
        out[topic] = llm.invoke(f"analyze {topic}").content

    with scripted([Step("T", match="technical"), Step("B", match="business"), Step("R", match="risk")]):
        threads = [threading.Thread(target=call, args=(t,)) for t in ("risk", "business", "technical")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert out == {"technical": "T", "business": "B", "risk": "R"}


def test_a_match_that_never_fires_is_an_unexpected_call():
    with pytest.raises(UnexpectedModelCall), scripted([Step("x", match="never")]):
        scripted_model().invoke("something else")


def test_tools_can_be_bound_and_tool_calls_scripted():
    from langchain_core.messages import ToolMessage

    call = AIMessage(content="", tool_calls=[{"name": "count", "args": {"project": "BILL"}, "id": "c1"}])
    with scripted([call, Step(responder=lambda c: f"saw: {c.messages[-1].content}")]) as model:
        llm = scripted_model().bind_tools([{"type": "function", "function": {"name": "count", "parameters": {}}}])
        assert model.bound_tools == ("count",)
        first = llm.invoke("count BILL")
        second = llm.invoke(["count BILL", first, ToolMessage(content="42", tool_call_id="c1")])
    assert first.tool_calls[0]["args"] == {"project": "BILL"} and second.content == "saw: 42"
    assert model.calls[1].messages[-1].content == "42"
