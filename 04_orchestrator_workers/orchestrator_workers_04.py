import json
from typing import TypedDict, List

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI


class State(TypedDict):
    text: str
    tasks: List[str]
    worker_results: List[str]
    synthesis: str


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


def orchestrator_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Analyze the following architecture problem.

Dynamically identify the major independent workstreams
that should be analyzed by specialized workers.

Return between 3 and 5 workstreams.

IMPORTANT:
Return ONLY valid JSON.
Do not include markdown.
Do not include explanations.
Do not include numbering outside the JSON.

Use exactly this format:

{{
  "tasks": [
    "task 1",
    "task 2",
    "task 3"
  ]
}}

Each task should be a short, specific workstream.

Architecture problem:

{text}
"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    content = response.content.strip()

    # Remove accidental markdown code fences
    content = content.replace("```json", "")
    content = content.replace("```", "")
    content = content.strip()

    data = json.loads(content)

    tasks = data["tasks"]

    return {
        "tasks": tasks
    }


def worker_node(state: State):

    results = []

    for task in state["tasks"]:

        prompt = PromptTemplate.from_template(
            """You are a specialized enterprise AI architecture worker.

Analyze the following architecture problem.

Your assigned workstream is:

{task}

Architecture problem:

{text}

Provide a concise architectural assessment.

Identify:
1. Important considerations
2. Key risks
3. Recommendations
"""
        )

        response = (prompt | llm).invoke({
            "task": task,
            "text": state["text"]
        })

        results.append(
            f"WORKSTREAM: {task}\n"
            f"ASSESSMENT:\n{response.content.strip()}"
        )

    return {
        "worker_results": results
    }


def synthesis_node(state: State):

    worker_output = "\n\n".join(
        state["worker_results"]
    )

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Synthesize the following dynamically generated
workstream assessments into one architectural recommendation.

Architecture problem:

{text}

Worker assessments:

{worker_results}

Provide:

1. Overall architectural assessment
2. Key findings
3. Major risks
4. Recommended next steps
"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"],
        "worker_results": worker_output
    })

    return {
        "synthesis": response.content.strip()
    }


workflow = StateGraph(State)

workflow.add_node(
    "orchestrator",
    orchestrator_node
)

workflow.add_node(
    "workers",
    worker_node
)

workflow.add_node(
    "synthesis",
    synthesis_node
)

workflow.set_entry_point("orchestrator")

workflow.add_edge(
    "orchestrator",
    "workers"
)

workflow.add_edge(
    "workers",
    "synthesis"
)

workflow.add_edge(
    "synthesis",
    END
)

app = workflow.compile()