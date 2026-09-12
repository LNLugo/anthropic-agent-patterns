from evaluator_optimizer_05 import (
    evaluator_node,
    optimizer_node,
)


question = """
Why does an enterprise need an AI governance framework?
"""


# ------------------------------------------------
# Start with an intentionally weak draft
# ------------------------------------------------

state = {
    "question": question,
    "draft": "AI governance is important because AI is important. Companies should use AI responsibly.",
    "score": 0,
    "feedback": "",
    "iteration": 1,
}


# ------------------------------------------------
# Step 1: Evaluate weak draft
# ------------------------------------------------

state.update(
    evaluator_node(state)
)


print("\n" + "=" * 70)
print("ITERATION 1 — INITIAL EVALUATION")
print("=" * 70)

print(f"Score: {state['score']}")
print(f"Feedback: {state['feedback']}")


# ------------------------------------------------
# Step 2: Optimize
# ------------------------------------------------

state.update(
    optimizer_node(state)
)


print("\n" + "=" * 70)
print("ITERATION 2 — OPTIMIZED DRAFT")
print("=" * 70)

print(state["draft"])


# ------------------------------------------------
# Step 3: Evaluate improved draft
# ------------------------------------------------

state.update(
    evaluator_node(state)
)


print("\n" + "=" * 70)
print("ITERATION 2 — EVALUATION")
print("=" * 70)

print(f"Score: {state['score']}")
print(f"Feedback: {state['feedback']}")


print("\n" + "=" * 70)
print("SCENARIO COMPLETE")
print("=" * 70)