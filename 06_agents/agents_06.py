import json
from typing import TypedDict, List

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI


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
# AGENT
# ---------------------------------------------------------

def agent_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an autonomous enterprise AI agent.

You must decide what to do next to solve the user's problem.

User problem:

{question}

Previous actions:

{actions}

Latest observation:

{observation}

Current iteration:

{iteration}

You have two possible decisions:

1. "act" - perform another analysis action
2. "finish" - provide the final answer

IMPORTANT:

Return ONLY valid JSON.

Use exactly this format:

{{
    "decision": "act",
    "action": "Analyze the key architectural risks."
}}

If the problem has been sufficiently analyzed, return:

{{
    "decision": "finish",
    "action": "Provide the final recommendation."
}}
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "actions": state["actions"],
        "observation": state["observation"],
        "iteration": state["iteration"]
    })

    data = json.loads(response.content.strip())

    return {
        "decision": data["decision"],
        "actions": state["actions"] + [data["action"]]
    }


# ---------------------------------------------------------
# ACTION
# ---------------------------------------------------------

def action_node(state: State):

    latest_action = state["actions"][-1]

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architecture analyst.

The agent has decided to perform the following action:

{action}

User problem:

{question}

Perform the requested analysis.

Provide a concise observation that the agent can use
to decide what to do next.
"""
    )

    response = (prompt | llm).invoke({
        "action": latest_action,
        "question": state["question"]
    })

    return {
        "observation": response.content.strip(),
        "iteration": state["iteration"] + 1
    }


# ---------------------------------------------------------
# FINAL ANSWER
# ---------------------------------------------------------

def final_answer_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Provide the final answer to the user's problem.

User problem:

{question}

Actions performed:

{actions}

Latest observations:

{observation}

Synthesize the analysis into a clear executive recommendation.
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "actions": state["actions"],
        "observation": state["observation"]
    })

    return {
        "final_answer": response.content.strip()
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
    "action",
    action_node
)

workflow.add_node(
    "final_answer",
    final_answer_node
)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    route_after_agent,
    {
        "act": "action",
        "finish": "final_answer"
    }
)

workflow.add_edge(
    "action",
    "agent"
)

workflow.add_edge(
    "final_answer",
    END
)

app = workflow.compile()