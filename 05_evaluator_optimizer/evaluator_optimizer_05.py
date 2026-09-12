from langgraph.graph import StateGraph, END

from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

load_dotenv()


class State(TypedDict):
    question: str
    draft: str
    score: int
    feedback: str
    iteration: int


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


def generator_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Answer the following question for an executive audience.

Question:

{question}

Provide a clear, concise, and actionable response.
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"]
    })

    return {
        "draft": response.content.strip(),
        "iteration": 1
    }


def evaluator_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an expert evaluator of enterprise AI architecture responses.

Evaluate the following answer to the question.

Question:

{question}

Answer:

{draft}

Evaluate the answer based on:

1. Accuracy
2. Completeness
3. Executive clarity
4. Actionability

Return ONLY valid JSON.

Use exactly this format:

{{
    "score": 7,
    "feedback": "Specific improvements needed."
}}

The score must be an integer from 1 to 10.
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "draft": state["draft"]
    })

    import json

    data = json.loads(response.content.strip())

    return {
        "score": data["score"],
        "feedback": data["feedback"]
    }

def optimizer_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an expert enterprise AI architect.

Improve the following draft based on the evaluator's feedback.

Question:

{question}

Current draft:

{draft}

Evaluator feedback:

{feedback}

Rewrite the answer to address the evaluator's concerns.

Requirements:

1. Preserve accurate information.
2. Address every important issue identified by the evaluator.
3. Make the response clear for an executive audience.
4. Make the recommendations actionable.
5. Return ONLY the improved answer.
"""
    )

    response = (prompt | llm).invoke({
        "question": state["question"],
        "draft": state["draft"],
        "feedback": state["feedback"]
    })

    return {
        "draft": response.content.strip(),
        "iteration": state["iteration"] + 1
    }
    
def route_after_evaluation(state: State):

    if state["score"] >= 8:
        return "end"

    if state["iteration"] >= 3:
        return "end"

    return "optimize"
workflow = StateGraph(State)


workflow.add_node(
    "generator",
    generator_node
)

workflow.add_node(
    "evaluator",
    evaluator_node
)

workflow.add_node(
    "optimizer",
    optimizer_node
)


workflow.set_entry_point("generator")


workflow.add_edge(
    "generator",
    "evaluator"
)


workflow.add_conditional_edges(
    "evaluator",
    route_after_evaluation,
    {
        "optimize": "optimizer",
        "end": END
    }
)


workflow.add_edge(
    "optimizer",
    "evaluator"
)


app = workflow.compile()