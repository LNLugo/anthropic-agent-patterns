import time
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI


# ---------------------------------------------------------
# Shared State
# ---------------------------------------------------------

class State(TypedDict):
    text: str

    technical_analysis: str
    business_analysis: str
    risk_analysis: str

    technical_duration: float
    business_duration: float
    risk_duration: float

    synthesis: str


# ---------------------------------------------------------
# LLM
# ---------------------------------------------------------

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)


# ---------------------------------------------------------
# Parallel Branch 1: Technical Analysis
# ---------------------------------------------------------

def technical_analysis_node(state: State):

    start_time = time.perf_counter()

    prompt = PromptTemplate.from_template(
        """Analyze the following AI architecture from a technical
perspective.

Focus on:
1. Architecture
2. Scalability
3. Integration
4. Technology considerations

Text:
{text}

Technical Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    duration = time.perf_counter() - start_time

    return {
        "technical_analysis": response.content.strip(),
        "technical_duration": duration
    }


# ---------------------------------------------------------
# Parallel Branch 2: Business Analysis
# ---------------------------------------------------------

def business_analysis_node(state: State):

    start_time = time.perf_counter()

    prompt = PromptTemplate.from_template(
        """Analyze the following AI architecture from a business
perspective.

Focus on:
1. Business value
2. User impact
3. Operational benefits
4. Strategic considerations

Text:
{text}

Business Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    duration = time.perf_counter() - start_time

    return {
        "business_analysis": response.content.strip(),
        "business_duration": duration
    }


# ---------------------------------------------------------
# Parallel Branch 3: Risk Analysis
# ---------------------------------------------------------

def risk_analysis_node(state: State):

    start_time = time.perf_counter()

    prompt = PromptTemplate.from_template(
        """Analyze the following AI architecture from a risk
and governance perspective.

Focus on:
1. Security
2. Compliance
3. Reliability
4. AI governance
5. Operational risk

Text:
{text}

Risk Analysis:"""
    )

    response = (prompt | llm).invoke({
        "text": state["text"]
    })

    duration = time.perf_counter() - start_time

    return {
        "risk_analysis": response.content.strip(),
        "risk_duration": duration
    }


# ---------------------------------------------------------
# Synthesis Node
# ---------------------------------------------------------

def synthesis_node(state: State):

    prompt = PromptTemplate.from_template(
        """You are an enterprise AI architect.

Synthesize the following three independent analyses into
one concise architectural assessment.

TECHNICAL ANALYSIS:
{technical_analysis}

BUSINESS ANALYSIS:
{business_analysis}

RISK ANALYSIS:
{risk_analysis}

Provide:
1. Overall assessment
2. Key strengths
3. Key concerns
4. Recommended next step

Architectural Assessment:"""
    )

    response = (prompt | llm).invoke({
        "technical_analysis": state["technical_analysis"],
        "business_analysis": state["business_analysis"],
        "risk_analysis": state["risk_analysis"]
    })

    return {
        "synthesis": response.content.strip()
    }


# ---------------------------------------------------------
# Build Workflow
# ---------------------------------------------------------

workflow = StateGraph(State)


# ---------------------------------------------------------
# Add Nodes
# ---------------------------------------------------------

workflow.add_node(
    "technical_analysis",
    technical_analysis_node
)

workflow.add_node(
    "business_analysis",
    business_analysis_node
)

workflow.add_node(
    "risk_analysis",
    risk_analysis_node
)

workflow.add_node(
    "synthesis",
    synthesis_node
)


# ---------------------------------------------------------
# Parallel Execution
# ---------------------------------------------------------

workflow.add_edge(
    "__start__",
    "technical_analysis"
)

workflow.add_edge(
    "__start__",
    "business_analysis"
)

workflow.add_edge(
    "__start__",
    "risk_analysis"
)


# ---------------------------------------------------------
# Convergence
# ---------------------------------------------------------

workflow.add_edge(
    "technical_analysis",
    "synthesis"
)

workflow.add_edge(
    "business_analysis",
    "synthesis"
)

workflow.add_edge(
    "risk_analysis",
    "synthesis"
)


# ---------------------------------------------------------
# End
# ---------------------------------------------------------

workflow.add_edge(
    "synthesis",
    END
)


# ---------------------------------------------------------
# Compile
# ---------------------------------------------------------

app = workflow.compile()