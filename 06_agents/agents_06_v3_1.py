"""Agents v3.1: the model chooses each turn; every turn ends in one typed outcome.

    agent --RunAgain-----> tool_executor --> agent
          --FinalOutput--> final_answer --> END        status "completed"
          --Handoff------> handoff ------> END         status "handed_off"
          --Interruption-> final_answer or stop -> END status "max_turns" or "invalid_decision"

The model's reply is parsed into exactly one TurnOutcome. A reply that names an
unknown tool, an unknown handoff target, or no valid decision is an
Interruption, never a guess. The turn limit is an outcome too: at the limit the
agent does not call the model at all, and the run ends with status
"max_turns", answered from what was gathered so far and labelled as such.
Every run says why it ended.

The outcome types follow the turn results in openai/openai-agents-python
(final output, run again, handoff, interruption); only the contract is borrowed.

run() makes each run one trace (10_observability): every decision and the answer
are generation spans, every tool a tool span, and a handoff a handoff span.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, TypedDict, Union

from dotenv import load_dotenv

load_dotenv()

from langchain_core.prompts import PromptTemplate  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "10_observability"))
from llm_provider import get_chat_model, parse_json  # noqa: E402  LLM_PROVIDER picks the model
from tracing import get_tracer, model_name, usage  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402


# ---------------------------------------------------------
# TURN OUTCOMES
# ---------------------------------------------------------

@dataclass(frozen=True)
class FinalOutput:
    """The model has enough to answer."""


@dataclass(frozen=True)
class RunAgain:
    """Run this tool, then give the model another turn."""

    tool: str


@dataclass(frozen=True)
class Handoff:
    """Pass the problem to another agent; this run ends."""

    to: str


@dataclass(frozen=True)
class Interruption:
    """The run stopped for a reason other than the model choosing to finish."""

    reason: Literal["max_turns", "invalid_decision"]
    detail: str = ""


TurnOutcome = Union[FinalOutput, RunAgain, Handoff, Interruption]

TOOLS = ("architecture_analysis", "risk_analysis")
HANDOFF_TARGETS = ("risk_specialist",)
MAX_TURNS = 3


def parse_outcome(reply) -> TurnOutcome:
    """Map the model's JSON reply onto one outcome. Anything else is an Interruption."""
    try:
        data = parse_json(reply)
    except (ValueError, TypeError) as e:
        return Interruption("invalid_decision", f"reply is not JSON: {e}")
    if not isinstance(data, dict):
        return Interruption("invalid_decision", "reply is not a JSON object")
    decision = data.get("decision")
    if decision == "finish":
        return FinalOutput()
    if decision == "act":
        tool = data.get("tool")
        if tool in TOOLS:
            return RunAgain(tool)
        return Interruption("invalid_decision", f"unknown tool {tool!r}")
    if decision == "handoff":
        to = data.get("to")
        if to in HANDOFF_TARGETS:
            return Handoff(to)
        return Interruption("invalid_decision", f"unknown handoff target {to!r}")
    return Interruption("invalid_decision", f"unknown decision {decision!r}")


# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------

class State(TypedDict, total=False):
    question: str
    actions: List[str]
    observations: List[str]
    iteration: int          # tool turns taken
    max_turns: int          # optional; MAX_TURNS when absent
    outcome: TurnOutcome    # the latest turn's outcome
    status: str             # why the run ended
    handoff_to: str
    final_answer: str


# ---------------------------------------------------------
# LLM
# ---------------------------------------------------------

llm = get_chat_model()


def _usage(response) -> dict:
    return usage(response)


def _model_name() -> str:
    return model_name(llm)


# ---------------------------------------------------------
# TOOLS
# ---------------------------------------------------------

def architecture_analysis(question: str) -> str:

    return (
        "Architecture analysis completed.\n"
        "Key areas identified: platform architecture, "
        "integration, scalability, governance, and cost."
    )


def risk_analysis(question: str) -> str:

    return (
        "Risk analysis completed.\n"
        "Key risk areas identified: security, compliance, "
        "operational complexity, model risk, and financial risk."
    )


TOOL_FUNCTIONS = {"architecture_analysis": architecture_analysis, "risk_analysis": risk_analysis}


# ---------------------------------------------------------
# AGENT
# ---------------------------------------------------------

AGENT_PROMPT = PromptTemplate.from_template(
    """You are an autonomous enterprise AI agent.

Your job is to determine what should happen next
to solve the user's problem.

User problem:

{question}

Available tools:

1. architecture_analysis
   Analyze architecture, platform design, integration,
   scalability, governance, and cost.

2. risk_analysis
   Analyze security, compliance, operational complexity,
   model risk, and financial risk.

You may also hand the problem to another agent:

- risk_specialist
  For problems that are mainly regulatory or model risk questions.

Previous actions:

{actions}

Previous observations:

{observations}

Turns used: {iteration} of {max_turns}

Return ONLY valid JSON, one of:

{{"decision": "act", "tool": "architecture_analysis"}}
{{"decision": "act", "tool": "risk_analysis"}}
{{"decision": "handoff", "to": "risk_specialist"}}
{{"decision": "finish"}}
"""
)


def agent_node(state: State):
    turns, limit = state.get("iteration", 0), state.get("max_turns", MAX_TURNS)
    if turns >= limit:
        # The limit is decided here, before the model is asked, so no call is
        # spent on a decision that would be overruled.
        return {"outcome": Interruption("max_turns", f"{turns} of {limit} turns used")}

    with get_tracer().span("generation", "decide", model=_model_name(), turn=turns + 1) as span:
        response = (AGENT_PROMPT | llm).invoke({
            "question": state["question"],
            "actions": state.get("actions", []),
            "observations": state.get("observations", []),
            "iteration": turns,
            "max_turns": limit,
        })
        outcome = parse_outcome(response.content)
        span.set(**_usage(response), outcome=type(outcome).__name__)
        # The detail can quote the model's reply ("unknown tool ..."), so it is content.
        span.content(output=response.content, detail=getattr(outcome, "detail", None))
    update = {"outcome": outcome}
    if isinstance(outcome, RunAgain):
        update["actions"] = state.get("actions", []) + [outcome.tool]
    return update


# ---------------------------------------------------------
# TOOL EXECUTOR
# ---------------------------------------------------------

def tool_executor_node(state: State):
    tool = state["outcome"].tool
    with get_tracer().span("tool", tool, outcome="ok") as span:
        observation = TOOL_FUNCTIONS[tool](state["question"])
        span.content(output=observation)
    return {
        "observations": state.get("observations", []) + [observation],
        "iteration": state.get("iteration", 0) + 1,
    }


# ---------------------------------------------------------
# FINAL ANSWER
# ---------------------------------------------------------

def final_answer_node(state: State):
    outcome = state["outcome"]
    capped = isinstance(outcome, Interruption) and outcome.reason == "max_turns"

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Answer the user's original question using the
observations gathered by the agent.
{limit_note}
User question:

{question}

Observations gathered:

{observations}

Provide a concise but useful enterprise architecture
recommendation.

Return only the final answer.
"""
    )

    with get_tracer().span("generation", "final_answer", model=_model_name(), capped=capped) as span:
        response = (prompt | llm).invoke({
            "question": state["question"],
            "observations": state.get("observations", []),
            "limit_note": (
                "\nThe agent reached its turn limit before deciding it was done. "
                "Say plainly what remains unanalysed.\n" if capped else ""
            ),
        })
        span.set(**_usage(response))
        span.content(output=response.content)

    return {
        "final_answer": response.content.strip(),
        "status": "max_turns" if capped else "completed",
    }


# ---------------------------------------------------------
# HANDOFF AND STOP
# ---------------------------------------------------------

def handoff_node(state: State):
    get_tracer().event("handoff", "handoff", from_agent="agent", to_agent=state["outcome"].to)
    return {"status": "handed_off", "handoff_to": state["outcome"].to}


def stop_node(state: State):
    return {"status": state["outcome"].reason}


# ---------------------------------------------------------
# ROUTING
# ---------------------------------------------------------

def route_after_agent(state: State) -> str:
    """The route is named after the outcome, so the graph diagram labels each edge with it."""
    outcome = state["outcome"]
    if isinstance(outcome, RunAgain):
        return "RunAgain"
    if isinstance(outcome, FinalOutput):
        return "FinalOutput"
    if isinstance(outcome, Handoff):
        return "Handoff"
    if outcome.reason == "max_turns":
        return "Interruption: max_turns"     # answer from what was gathered, labelled as capped
    return "Interruption: invalid_decision"  # no answer rather than a guessed one


# ---------------------------------------------------------
# GRAPH
# ---------------------------------------------------------

workflow = StateGraph(State)

workflow.add_node("agent", agent_node)
workflow.add_node("tool_executor", tool_executor_node)
workflow.add_node("final_answer", final_answer_node)
workflow.add_node("handoff", handoff_node)
workflow.add_node("stop", stop_node)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    route_after_agent,
    {
        "RunAgain": "tool_executor",
        "FinalOutput": "final_answer",
        "Interruption: max_turns": "final_answer",
        "Handoff": "handoff",
        "Interruption: invalid_decision": "stop",
    },
)

workflow.add_edge("tool_executor", "agent")
workflow.add_edge("final_answer", END)
workflow.add_edge("handoff", END)
workflow.add_edge("stop", END)

app = workflow.compile()


def run(question: str, max_turns: int = MAX_TURNS) -> State:
    """One run as one trace. The agent span records why the run ended."""
    tracer = get_tracer()
    with tracer.trace("agents_06", metadata={"max_turns": max_turns}):
        with tracer.span("agent", "agent", max_turns=max_turns) as span:
            span.content(input=question)
            state = app.invoke({"question": question, "actions": [], "observations": [], "iteration": 0,
                                "max_turns": max_turns, "final_answer": ""})
            span.set(status=state.get("status"), turns=state.get("iteration", 0))
            span.content(output=state.get("final_answer"))
    return state
