import json
from typing import TypedDict, List

from dotenv import load_dotenv

load_dotenv()

from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from langgraph.graph import StateGraph, END


# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------

class State(TypedDict):
    question: str
    actions: List[str]
    observations: List[str]
    decision: str
    iteration: int
    final_answer: str


# ---------------------------------------------------------
# LLM
# ---------------------------------------------------------

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


# ---------------------------------------------------------
# TOOL 1 — ARCHITECTURE ANALYSIS
# ---------------------------------------------------------

def architecture_analysis(question: str) -> str:

    return (
        "Architecture analysis completed.\n"
        "Key areas identified: platform architecture, "
        "integration, scalability, governance, and cost."
    )


# ---------------------------------------------------------
# TOOL 2 — RISK ANALYSIS
# ---------------------------------------------------------

def risk_analysis(question: str) -> str:

    return (
        "Risk analysis completed.\n"
        "Key risk areas identified: security, compliance, "
        "operational complexity, model risk, and financial risk."
    )

# ---------------------------------------------------------
# AGENT — DECIDE WHAT TO DO NEXT
# ---------------------------------------------------------

def agent_node(state: State):

    prompt = PromptTemplate.from_template(
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

Previous actions:

{actions}

Previous observations:

{observations}

Current iteration:

{iteration}

Decide whether:

1. You need another tool to gather useful information.
2. You have enough information to provide a final answer.

Return ONLY valid JSON.

If another tool is needed:

{{
    "decision": "act",
    "tool": "architecture_analysis"
}}

If another tool is needed:

{{
    "decision": "act",
    "tool": "risk_analysis"
}}

If you have enough information:

{{
    "decision": "finish",
    "tool": ""
}}

The decision must be either:

act

or

finish

If decision is "act", the tool must be either:

architecture_analysis

or

risk_analysis
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "actions": state["actions"],
        "observations": state["observations"],
        "iteration": state["iteration"]
    })

    data = json.loads(response.content.strip())

    decision = data["decision"]
    tool = data["tool"]

    return {
        "decision": decision,
        "actions": (
            state["actions"] + [tool]
            if decision == "act"
            else state["actions"]
        )
    }
    
# ---------------------------------------------------------
# TOOL EXECUTOR
# ---------------------------------------------------------

def tool_executor_node(state: State):

    selected_tool = state["actions"][-1]

    if selected_tool == "architecture_analysis":

        observation = architecture_analysis(
            state["question"]
        )

    elif selected_tool == "risk_analysis":

        observation = risk_analysis(
            state["question"]
        )

    else:

        observation = (
            f"Unknown tool selected: {selected_tool}"
        )

    return {
        "observations": (
            state["observations"] + [observation]
        ),
        "iteration": state["iteration"] + 1
    }
    
# ---------------------------------------------------------
# ROUTING
# ---------------------------------------------------------

def route_after_agent(state: State):

    # Safety limit to prevent an infinite agent loop
    if state["iteration"] >= 3:
        return "finish"

    if state["decision"] == "finish":
        return "finish"

    return "act"


# ---------------------------------------------------------
# GRAPH
# ---------------------------------------------------------

workflow = StateGraph(State)

workflow.add_node(
    "agent",
    agent_node
)

workflow.add_node(
    "tool_executor",
    tool_executor_node
)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    route_after_agent,
    {
        "act": "tool_executor",
        "finish": END
    }
)

workflow.add_edge(
    "tool_executor",
    "agent"
)

app = workflow.compile()