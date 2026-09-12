import os
from typing import TypedDict, List

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# --------------------------------------------------
# 1. Define the agent state
# --------------------------------------------------

class State(TypedDict):
    text: str
    classification: str
    entities: List[str]
    summary: str


# --------------------------------------------------
# 2. Create the language model
# --------------------------------------------------

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


# --------------------------------------------------
# 3. Classification node
# --------------------------------------------------

def classification_node(state: State):
    """
    Classify the text into News, Blog, Research, or Other.
    """

    prompt = PromptTemplate(
        input_variables=["text"],
        template=(
            "Classify the following text into one of the categories: "
            "News, Blog, Research, or Other.\n\n"
            "Text: {text}\n\n"
            "Category:"
        )
    )

    message = HumanMessage(
        content=prompt.format(text=state["text"])
    )

    classification = llm.invoke([message]).content.strip()

    return {
        "classification": classification
    }


# --------------------------------------------------
# 4. Entity extraction node
# --------------------------------------------------

def entity_extraction_node(state: State):
    """
    Extract people, organizations, and locations.
    """

    prompt = PromptTemplate(
        input_variables=["text"],
        template=(
            "Extract all the entities (Person, Organization, Location) "
            "from the following text. "
            "Provide the result as a comma-separated list.\n\n"
            "Text: {text}\n\n"
            "Entities:"
        )
    )

    message = HumanMessage(
        content=prompt.format(text=state["text"])
    )

    entities = (
        llm.invoke([message])
        .content
        .strip()
        .split(", ")
    )

    return {
        "entities": entities
    }


# --------------------------------------------------
# 5. Summarization node
# --------------------------------------------------

def summarize_node(state: State):
    """
    Summarize the input text in one short sentence.
    """

    summarization_prompt = PromptTemplate.from_template(
        """Summarize the following text in one short sentence.

Text: {text}

Summary:"""
    )

    chain = summarization_prompt | llm

    response = chain.invoke(
        {"text": state["text"]}
    )

    return {
        "summary": response.content
    }


# --------------------------------------------------
# 6. Build the LangGraph workflow
# --------------------------------------------------

workflow = StateGraph(State)


# Add nodes
workflow.add_node(
    "classification_node",
    classification_node
)

workflow.add_node(
    "entity_extraction",
    entity_extraction_node
)

workflow.add_node(
    "summarization",
    summarize_node
)


# Define workflow sequence
workflow.set_entry_point("classification_node")

workflow.add_edge(
    "classification_node",
    "entity_extraction"
)

workflow.add_edge(
    "entity_extraction",
    "summarization"
)

workflow.add_edge(
    "summarization",
    END
)


# --------------------------------------------------
# 7. Compile the graph
# --------------------------------------------------

app = workflow.compile()
