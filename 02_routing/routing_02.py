import os
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage


# ---------------------------------------------------------
# Shared State
# ---------------------------------------------------------

class State(TypedDict):
    text: str
    classification: str
    selected_path: str
    response: str


# ---------------------------------------------------------
# LLM
# ---------------------------------------------------------

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


# ---------------------------------------------------------
# Node 1: Classification
# ---------------------------------------------------------

def classification_node(state: State):
    prompt = PromptTemplate(
        input_variables=["text"],
        template=(
            "Classify the following text into exactly one of these "
            "categories: News, Research, Blog, or Other.\n\n"
            "Return ONLY the category name.\n\n"
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


# ---------------------------------------------------------
# Routing Decision
# ---------------------------------------------------------

def route_by_classification(state: State):
    classification = state["classification"].lower()

    if "news" in classification:
        return "news"

    elif "research" in classification:
        return "research"

    elif "blog" in classification:
        return "blog"

    else:
        return "other"


# ---------------------------------------------------------
# News Processing
# ---------------------------------------------------------

def news_node(state: State):
    prompt = PromptTemplate.from_template(
        """Analyze this news content.

Identify:
1. The main event
2. The organizations or people involved
3. Why the event matters

Text:
{text}

Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    return {
        "selected_path": "news",
        "response": response.content.strip()
    }


# ---------------------------------------------------------
# Research Processing
# ---------------------------------------------------------

def research_node(state: State):
    prompt = PromptTemplate.from_template(
        """Analyze this research content.

Identify:
1. The research topic
2. The main finding or contribution
3. The potential significance

Text:
{text}

Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    return {
        "selected_path": "research",
        "response": response.content.strip()
    }


# ---------------------------------------------------------
# Blog Processing
# ---------------------------------------------------------

def blog_node(state: State):
    prompt = PromptTemplate.from_template(
        """Analyze this blog content.

Identify:
1. The main idea
2. The author's perspective
3. The key takeaway

Text:
{text}

Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    return {
        "selected_path": "blog",
        "response": response.content.strip()
    }


# ---------------------------------------------------------
# Other Processing
# ---------------------------------------------------------

def other_node(state: State):
    prompt = PromptTemplate.from_template(
        """Provide a concise summary of this content.

Text:
{text}

Summary:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    return {
        "selected_path": "other",
        "response": response.content.strip()
    }


# ---------------------------------------------------------
# Build Routing Workflow
# ---------------------------------------------------------

workflow = StateGraph(State)

workflow.add_node(
    "classification_node",
    classification_node
)

workflow.add_node(
    "news",
    news_node
)

workflow.add_node(
    "research",
    research_node
)

workflow.add_node(
    "blog",
    blog_node
)

workflow.add_node(
    "other",
    other_node
)


# ---------------------------------------------------------
# Entry Point
# ---------------------------------------------------------

workflow.set_entry_point(
    "classification_node"
)


# ---------------------------------------------------------
# Dynamic Routing
# ---------------------------------------------------------

workflow.add_conditional_edges(
    "classification_node",
    route_by_classification,
    {
        "news": "news",
        "research": "research",
        "blog": "blog",
        "other": "other"
    }
)


# ---------------------------------------------------------
# End Points
# ---------------------------------------------------------

workflow.add_edge("news", END)
workflow.add_edge("research", END)
workflow.add_edge("blog", END)
workflow.add_edge("other", END)


# ---------------------------------------------------------
# Compile Application
# ---------------------------------------------------------

app = workflow.compile()