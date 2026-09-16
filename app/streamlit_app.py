import sys
import importlib.util
from pathlib import Path

import streamlit as st

from demo_scenarios import ROUTING_SCENARIOS


# --------------------------------------------------
# Project path
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# --------------------------------------------------
# Page configuration
# --------------------------------------------------

st.set_page_config(
    page_title="Anthropic Agent Pattern Explorer",
    page_icon="🤖",
    layout="wide"
)


# --------------------------------------------------
# Header
# --------------------------------------------------

st.title("🤖 Anthropic Agent Pattern Explorer")

st.markdown(
    """
Explore the six workflow and agent patterns described in
Anthropic's **Building effective agents**.

The goal is not to maximize autonomy.

> **Choose the simplest architecture that reliably solves the problem.**
"""
)


# --------------------------------------------------
# Pattern definitions
# --------------------------------------------------

patterns = {
    "Prompt Chaining": "Fixed sequence of LLM calls",
    "Routing": "Conditional selection of a specialized path",
    "Routing V2": "LLM-driven structured routing with constrained execution",
    "Parallelization": "Independent work executed in parallel",
    "Orchestrator-Workers": "Dynamic decomposition into worker tasks",
    "Evaluator-Optimizer": "Iterative generation, evaluation, and refinement",
    "Agents": "Dynamic decision-making and tool use",
}


selected_pattern = st.selectbox(
    "Select an Agent Pattern",
    list(patterns.keys())
)


st.divider()


# --------------------------------------------------
# Pattern information
# --------------------------------------------------

col1, col2 = st.columns([1, 2])


with col1:

    st.subheader("Pattern")

    st.markdown(
        f"### {selected_pattern}"
    )

    st.write(
        patterns[selected_pattern]
    )


with col2:

    st.subheader("Architecture")

    if selected_pattern == "Prompt Chaining":

        graph_path = (
            PROJECT_ROOT
            / "01_prompt_chaining"
            / "prompt_chaining_01_graph.png"
        )

        st.image(
            str(graph_path),
            width=400
        )

    elif selected_pattern in ["Routing", "Routing V2"]:

        graph_path = (
            PROJECT_ROOT
            / "02_routing"
            / "routing_02_graph.png"
        )

        st.image(
            str(graph_path),
            width=400
        )

        if selected_pattern == "Routing V2":

            st.caption(
                "LLM Router → Structured RouteDecision → "
                "LangGraph Conditional Edge → Specialized Path"
            )

    else:

        st.info(
            "This pattern will be connected next."
        )


st.divider()


# --------------------------------------------------
# Demonstration scenario
# --------------------------------------------------

st.subheader("Demonstration Scenario")


if selected_pattern in ["Routing", "Routing V2"]:

    scenario_name = st.selectbox(
        "Select a Routing Scenario",
        list(ROUTING_SCENARIOS.keys())
    )

    question = st.text_area(
        "Prompt",
        value=ROUTING_SCENARIOS[scenario_name]["prompt"],
        height=120
    )

else:

    question = st.text_area(
        "Enter a question or problem",
        value=(
            "An enterprise wants to build an AI platform "
            "for 10,000 employees."
        ),
        height=120
    )


# --------------------------------------------------
# Run selected pattern
# --------------------------------------------------

if st.button(
    "Run Pattern",
    type="primary"
):

    if not question.strip():

        st.warning(
            "Please enter a question or problem."
        )


    # ==================================================
    # PROMPT CHAINING
    # ==================================================

    elif selected_pattern == "Prompt Chaining":

        module_path = (
            PROJECT_ROOT
            / "01_prompt_chaining"
            / "prompt_chaining_01.py"
        )

        spec = importlib.util.spec_from_file_location(
            "prompt_chaining_01",
            module_path
        )

        module = importlib.util.module_from_spec(
            spec
        )

        spec.loader.exec_module(
            module
        )

        app = module.app

        with st.spinner(
            "Running Prompt Chaining..."
        ):

            result = app.invoke(
                {
                    "text": question,
                    "classification": "",
                    "entities": [],
                    "summary": ""
                }
            )

        st.success(
            "Prompt Chaining completed."
        )

        st.subheader(
            "Execution Result"
        )

        result_col1, result_col2 = st.columns(2)

        with result_col1:

            st.markdown(
                "### Classification"
            )

            st.write(
                result["classification"]
            )

            st.markdown(
                "### Entities"
            )

            for entity in result["entities"]:

                st.write(
                    f"- {entity}"
                )

        with result_col2:

            st.markdown(
                "### Summary"
            )

            st.write(
                result["summary"]
            )


    # ==================================================
    # ROUTING V1
    # ==================================================

    elif selected_pattern == "Routing":

        module_path = (
            PROJECT_ROOT
            / "02_routing"
            / "routing_02.py"
        )

        spec = importlib.util.spec_from_file_location(
            "routing_02",
            module_path
        )

        module = importlib.util.module_from_spec(
            spec
        )

        spec.loader.exec_module(
            module
        )

        app = module.app

        with st.spinner(
            "Running Routing..."
        ):

            result = app.invoke(
                {
                    "text": question,
                    "classification": "",
                    "selected_path": "",
                    "response": ""
                }
            )

        st.success(
            "Routing completed."
        )

        st.subheader(
            "Execution Result"
        )

        result_col1, result_col2 = st.columns(2)

        with result_col1:

            st.markdown(
                "### Classification"
            )

            st.write(
                result["classification"]
            )

            st.markdown(
                "### Selected Path"
            )

            st.write(
                f"**{result['selected_path']}**"
            )

        with result_col2:

            st.markdown(
                "### Response"
            )

            st.write(
                result["response"]
            )


    # ==================================================
    # ROUTING V2
    # ==================================================

    elif selected_pattern == "Routing V2":

        module_path = (
            PROJECT_ROOT
            / "02_routing"
            / "routing_02_v2.py"
        )

        spec = importlib.util.spec_from_file_location(
            "routing_02_v2",
            module_path
        )

        module = importlib.util.module_from_spec(
            spec
        )

        spec.loader.exec_module(
            module
        )

        app = module.app

        with st.spinner(
            "Running Routing V2 — LLM Router..."
        ):

            result = app.invoke(
                {
                    "text": question,
                    "route": "",
                    "selected_path": "",
                    "response": ""
                }
            )

        st.success(
            "Routing V2 completed."
        )

        st.subheader(
            "Execution Result"
        )

        result_col1, result_col2 = st.columns(2)

        with result_col1:

            st.markdown(
                "### LLM Route"
            )

            st.write(
                f"**{result['route']}**"
            )

            st.markdown(
                "### LangGraph Selected Path"
            )

            st.write(
                f"**{result['selected_path']}**"
            )

        with result_col2:

            st.markdown(
                "### Response"
            )

            st.write(
                result["response"]
            )


    # ==================================================
    # FUTURE PATTERNS
    # ==================================================

    else:

        st.info(
            f"{selected_pattern} will be connected next."
        )