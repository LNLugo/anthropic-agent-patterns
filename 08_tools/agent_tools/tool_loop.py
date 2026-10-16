"""A bounded tool loop with guardrails: the model calls a tool, sees the result, answers.

    model --count_jira_issues--> input guardrails --allow--> Jira, as the caller --> output guardrails
      ^                               |                                                   |
      |                          reject_content                                      reject_content
      +---- tool message <------------+---------------------------------------------------+
            (the result, or what the guardrail told the model)

    raise_exception at either stage halts the run with a tripwire exception.

The model router in model_router.py makes one decision and stops; it is what the
evaluations measure. This loop is what a deployed assistant needs instead: when
a guardrail rejects a call, the model has to hear why and get another turn.
The tool's own bound (spec_from_arguments and validate) still runs on every
call that the guardrails allow, so a guardrail can narrow what reaches Jira but
never widen it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .guardrails import GuardrailRecord, ModelBudget, ToolCallData, ToolGuardrail, check_model_budget, run_guardrails
from .jira_mock import Caller
from .jql_tool import BoundedJiraTool, ToolRejected, ToolResult
from .observe import get_tracer, model_name, usage
from .model_router import ASK_CLARIFICATION, EVIDENCE_FIELD, SEARCH_WIKI, openai_style, system_prompt, with_evidence

LOOP_INSTRUCTIONS = """
After count_jira_issues returns, answer the user in one or two sentences using
the count, and say that it covers only issues they can see. If a tool reply
starts with "Not run", follow its instruction instead of retrying the same call.
"""


@dataclass
class LoopResult:
    status: Literal["answered", "clarify", "knowledge", "max_turns", "budget"]
    text: str                                   # the answer, the clarifying question, or the wiki query
    turns: int
    tool_results: list[ToolResult] = field(default_factory=list)
    records: list[GuardrailRecord] = field(default_factory=list)   # every guardrail verdict, in order


class ToolLoop:
    def __init__(self, llm, tool: BoundedJiraTool, guardrails: list[ToolGuardrail] = (), max_turns: int = 4,
                 budget: ModelBudget | None = None):
        """budget is asked before every turn's model call, so a loop that keeps calling the
        model is stopped mid-run, not only at the start."""
        self.tool, self.guardrails, self.max_turns, self.budget = tool, list(guardrails), max_turns, budget
        tools = [openai_style(with_evidence(tool.schema)), openai_style(SEARCH_WIKI), openai_style(ASK_CLARIFICATION)]
        self.llm = llm.bind_tools(tools)
        self.model = model_name(llm)

    def run(self, question: str, caller: Caller, today: date) -> LoopResult:
        tracer = get_tracer()
        with tracer.trace("tool_loop", metadata={"caller": caller.id}):
            with tracer.span("agent", "tool_loop", caller=caller.id, max_turns=self.max_turns) as span:
                span.content(input=question)
                result = self._run(question, caller, today)
                span.set(status=result.status, turns=result.turns)
                span.content(output=result.text)
        return result

    def _run(self, question: str, caller: Caller, today: date) -> LoopResult:
        messages = [SystemMessage(system_prompt(today) + LOOP_INSTRUCTIONS), HumanMessage(question)]
        result = LoopResult("max_turns", "", 0)
        tracer = get_tracer()
        for turn in range(1, self.max_turns + 1):
            result.turns = turn
            refused = check_model_budget(self.budget, caller, self.model, result.records)
            if refused is not None:
                result.status, result.text = "budget", refused
                return result
            with tracer.span("generation", "turn", model=self.model, turn=turn) as gen:
                msg = self.llm.invoke(messages)
                calls = getattr(msg, "tool_calls", None) or []
                gen.set(**usage(msg), tool_choice=calls[0]["name"] if calls else None)
                gen.content(output=[dict(c.get("args") or {}) for c in calls] or str(msg.content))
            if not calls:
                result.status, result.text = "answered", str(msg.content)
                return result
            call, extra = calls[0], calls[1:]
            name, args = call["name"], dict(call.get("args") or {})
            if name == ASK_CLARIFICATION["name"]:
                result.status, result.text = "clarify", args.get("question", "")
                return result
            if name == SEARCH_WIKI["name"]:
                result.status, result.text = "knowledge", args.get("query", "")
                return result

            reply = self._count(name, args, question, caller, result)
            messages += [msg, ToolMessage(content=reply, tool_call_id=call["id"])]
            messages += [ToolMessage(content="Not run: one tool call per turn.", tool_call_id=c["id"]) for c in extra]
        return result

    def _count(self, name: str, args: dict, question: str, caller: Caller, result: LoopResult) -> str:
        """Run one count call through input guardrails, the tool, and output guardrails.
        Returns what the model is shown: the result, or why it was not run."""
        data = ToolCallData(name, args, question, caller)
        rejected = run_guardrails(self.guardrails, "input", data, result.records)
        if rejected is not None:
            return rejected
        tracer = get_tracer()
        with tracer.span("tool", name, caller=caller.id) as span:
            span.content(input=args)
            try:
                spec = self.tool.spec_from_arguments({k: v for k, v in args.items() if k != EVIDENCE_FIELD})
                out = self.tool.run(spec, caller)
            except ToolRejected as e:
                span.set(outcome="rejected")
                with tracer.span("guardrail", "tool_bound", stage="input", tool=name, behavior="reject_content",
                                 triggered=True) as bound:
                    bound.content(message=str(e))  # the bound's message quotes the arguments
                result.records.append(GuardrailRecord("tool_bound", "input", name, "reject_content", {"error": str(e)}))
                return f"Not run: {e}."
            span.set(outcome="ok", truncated=out.truncated)
            span.content(output={"count": out.count, "keys": list(out.keys), "jql": out.jql})
        rejected = run_guardrails(self.guardrails, "output", replace(data, output=out), result.records)
        if rejected is not None:
            return rejected
        result.tool_results.append(out)
        return json.dumps({"count": out.count, "keys": list(out.keys), "truncated": out.truncated,
                           "groups": out.groups, "jql": out.jql})
