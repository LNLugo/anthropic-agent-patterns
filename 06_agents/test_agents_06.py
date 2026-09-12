from agents_06 import app


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
    "observation": "",
    "decision": "",
    "iteration": 0,
    "final_answer": ""
})


print("\n" + "=" * 70)
print("AGENT EXECUTION")
print("=" * 70)

print("\nACTIONS:")

for i, action in enumerate(result["actions"], start=1):
    print(f"\n{i}. {action}")


print("\n" + "-" * 70)

print(f"\nITERATIONS: {result['iteration']}")

print("\nFINAL ANSWER:")
print(result["final_answer"])

print("\n" + "=" * 70)