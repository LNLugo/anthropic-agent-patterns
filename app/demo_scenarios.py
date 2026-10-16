# --------------------------------------------------
# Demo scenarios for Anthropic Agent Pattern Explorer
# --------------------------------------------------

ROUTING_SCENARIOS = {
    "News": {
        "prompt": (
            "Microsoft announced a new enterprise AI platform today "
            "that will allow organizations to deploy AI assistants "
            "across their workforce."
        )
    },

    "Research": {
        "prompt": (
            "A new research study demonstrates that "
            "retrieval-augmented generation can improve the accuracy "
            "of enterprise AI assistants."
        )
    },

    "Blog": {
        "prompt": (
            "In this article, I discuss five lessons I learned "
            "while designing an enterprise AI architecture."
        )
    },

    "Other": {
        "prompt": (
            "An enterprise wants to build an AI platform "
            "for 10,000 employees."
        )
    }
}

# --------------------------------------------------
# Agents scenarios: one per way a run can end
# --------------------------------------------------
#
# Live scenarios are prompts: gpt-6-luna decides each turn, so "expected" is
# what the scenario is designed to produce, and the page reports what actually
# happened. A well-behaved live model rarely breaks the decision contract, so
# the two abstentions on a bad decision are replays: the model's replies are
# scripted, the run is real, and the outcome is the same every time.

AGENT_SCENARIOS = {
    "Completes: an architecture choice": {
        "prompt": (
            "We are choosing between a central AI gateway and letting each "
            "business unit call model APIs directly. Which should we pick "
            "for 10,000 employees?"
        ),
        "max_turns": 3,
        "expected": "completed",
        "about": "Within the agent's tools. It should gather analysis, then decide it has enough and answer.",
    },
    "Hands off: a model risk question": {
        "prompt": (
            "Our bank's credit-decision model now uses an LLM. Does it meet "
            "SR 11-7 model risk management expectations and the EU AI Act's "
            "high-risk requirements?"
        ),
        "max_turns": 3,
        "expected": "handed_off",
        "about": "A regulatory and model risk question. The agent should pass it to the risk specialist rather than answer it itself.",
    },
    "Turn limit: too broad for one turn": {
        "prompt": (
            "Assess architecture, security, compliance, cost and operating "
            "model for rolling out AI assistants to 10,000 employees across "
            "three regions."
        ),
        "max_turns": 1,
        "expected": "max_turns",
        "about": "Needs both tools, with a limit of one turn. The answer should say plainly what was not analysed.",
    },
    "Abstains: asks for a tool it doesn't have": {
        "prompt": "Forecast our AI platform's cloud spend for each of the next three years.",
        "max_turns": 3,
        "expected": "invalid_decision",
        "about": (
            "The agent has no forecasting tool. It asks for one that doesn't exist; "
            "the run stops with no answer instead of inventing a forecast."
        ),
        "replay": [
            {"decision": "act", "tool": "architecture_analysis"},
            {"decision": "act", "tool": "cost_forecast"},
        ],
    },
    "Abstains: answers instead of deciding": {
        "prompt": "Which cloud should host our AI platform?",
        "max_turns": 3,
        "expected": "invalid_decision",
        "about": (
            "The model skips the decision contract and replies with a confident "
            "recommendation in prose. That reply is not shown to the user: no tool "
            "ran, so nothing backs it."
        ),
        "replay": [
            "Go with Azure. It has the best enterprise AI services and your "
            "team already knows it.",
        ],
    },
    "Your own question": {
        "prompt": "",
        "max_turns": 3,
        "about": "Live. Type any question and see how the run ends.",
    },
}
