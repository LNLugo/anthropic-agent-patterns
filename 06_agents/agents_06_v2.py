import json

from typing import TypedDict, List

from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI

from langchain_core.prompts import PromptTemplate


class State(TypedDict):
    question: str
    actions: List[str]
    observation: str
    decision: str
    iteration: int
    final_answer: str


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


# ---------------------------------------------------------
# TOOL 1 — ARCHITECTURE ANALYSIS
# ---------------------------------------------------------

def architecture_analysis(question: str) -> str:

    return (
        "Architecture analysis tool executed.\n"
        f"Problem received: {question}\n"
        "The architecture should be evaluated across "
        "platform design, integration, security, scalability, "
        "governance, and cost."
    )


# ---------------------------------------------------------
# TOOL 2 — RISK ANALYSIS
# ---------------------------------------------------------

def risk_analysis(question: str) -> str:

    return (
        "Risk analysis tool executed.\n"
        f"Problem received: {question}\n"
        "Key risk categories include security, compliance, "
        "operational complexity, scalability, model risk, "
        "and cost."
    )
    
# ---------------------------------------------------------
# TOOL EXECUTOR
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
   Analyze security, compliance, operational,
   model, and financial risks.

Previous actions:

{actions}

Latest observation:

{observation}

Current iteration:

{iteration}

Decide whether you need another tool or whether
you have enough information to provide the final answer.

Return ONLY valid JSON.

If another tool is needed:

{{
    "decision": "act",
    "tool": "architecture_analysis"
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
        "observation": state["observation"],
        "iteration": state["iteration"]
    })

    data = json.loads(response.content.strip())

    decision = data["decision"]
    tool = data["tool"]

    actions = state["actions"]

    if decision == "act":
        actions = actions + [tool]

    return {
        "decision": decision,
        "actions": actions
    }
# ---------------------------------------------------------
# AGENT — DECIDES WHICH TOOL TO USE
# ---------------------------------------------------------

def agent_node(state: State):

    question = state["question"]

    # Simple decision logic for V2.
    # The agent will eventually make this decision dynamically
    # using the LLM.

    if "risk" in question.lower():
        action = "risk_analysis"
    else:
        action = "architecture_analysis"

    return {
        "actions": state["actions"] + [action]
    }
    
# ---------------------------------------------------------
# TOOL EXECUTOR
# ---------------------------------------------------------

# ---------------------------------------------------------
# AGENT — LLM DECIDES WHICH TOOL TO USE
# ---------------------------------------------------------

def agent_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an autonomous enterprise AI agent.

You must decide which tool should be used to analyze
the user's problem.

User problem:

{question}

Available tools:

1. architecture_analysis
   Use this tool to analyze architecture, platform design,
   integration, scalability, governance, and cost.

2. risk_analysis
   Use this tool to analyze security, compliance,
   operational, model, and financial risks.

Previous actions:

{actions}

Latest observation:

{observation}

Select the tool that would provide the most useful
next piece of information.

Return ONLY valid JSON.

Use exactly this format:

{{
    "tool": "architecture_analysis"
}}

The tool must be either:

architecture_analysis

or

risk_analysis
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "actions": state["actions"],
        "observation": state["observation"]
    })

    data = json.loads(response.content.strip())

    selected_tool = data["tool"]

    return {
        "actions": state["actions"] + [selected_tool]
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
        "observation": observation,
        "iteration": state["iteration"] + 1
    }
# ---------------------------------------------------------
# AGENT DECISION — CONTINUE OR FINISH
# ---------------------------------------------------------

# ---------------------------------------------------------
# V2 ROUTING
# ---------------------------------------------------------

def should_continue(state: State):

    # V2 executes one selected tool and then finishes.
    return "finish"
# ---------------------------------------------------------
# GRAPH
# ---------------------------------------------------------

from langgraph.graph import StateGraph, END


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

workflow.add_edge(
    "agent",
    "tool_executor"
)

workflow.add_conditional_edges(
    "tool_executor",
    should_continue,
    {
        "continue": "agent",
        "finish": END
    }
)

app = workflow.compile()