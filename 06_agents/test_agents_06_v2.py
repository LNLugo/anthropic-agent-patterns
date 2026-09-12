from agents_06_v2 import app


question = """
An enterprise wants to build an AI platform for 10,000 employees.

The platform will provide RAG-based knowledge search,
AI assistants, multiple foundation models,
integration with enterprise applications,
and must meet security, compliance,
scalability, and cost requirements.
"""


result = app.invoke({
    "question": question,
    "actions": [],
    "observation": "",
    "iteration": 0,
    "final_answer": ""
})


print("\n" + "=" * 70)
print("AGENT + TOOL EXECUTION")
print("=" * 70)

print("\nSELECTED TOOL:")

for action in result["actions"]:
    print(f"  {action}")

print("\nOBSERVATION:")
print(result["observation"])

print("\nITERATIONS:")
print(result["iteration"])

print("\n" + "=" * 70)