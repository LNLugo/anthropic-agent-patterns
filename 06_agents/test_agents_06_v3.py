from agents_06_v3 import app


question = """
An enterprise wants to build an AI platform for 10,000 employees.

The platform will provide RAG-based knowledge search,
AI assistants, multiple foundation models,
integration with enterprise applications,
and must meet security, compliance,
scalability, and cost requirements.

What should the enterprise consider when designing
this platform?
"""


result = app.invoke({
    "question": question,
    "actions": [],
    "observations": [],
    "decision": "",
    "iteration": 0,
    "final_answer": ""
})


print("\n" + "=" * 70)
print("AGENT V3 EXECUTION")
print("=" * 70)

print("\nAGENT TRAJECTORY:")

for i, action in enumerate(result["actions"], start=1):

    print(f"\nIteration {i}")
    print(f"  Action: {action}")

    if i <= len(result["observations"]):
        print(
            f"  Observation: "
            f"{result['observations'][i - 1]}"
        )


print("\n" + "-" * 70)

print(f"\nTOTAL ITERATIONS: {result['iteration']}")

print(f"\nFINAL DECISION: {result['decision']}")

print("\n" + "=" * 70)