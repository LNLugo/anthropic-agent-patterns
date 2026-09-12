from orchestrator_workers_04 import app


sample_text = """
An enterprise wants to build an AI platform for 10,000 employees.
The platform will provide RAG-based knowledge search, AI assistants,
multiple foundation models, integration with enterprise applications,
and must meet security, compliance, scalability, and cost requirements.
"""


print("=" * 70)
print("ORCHESTRATOR-WORKERS TEST")
print("=" * 70)

print("\nINPUT:")
print(sample_text)


result = app.invoke({
    "text": sample_text
})


print("\n" + "=" * 70)
print("DYNAMICALLY GENERATED WORKSTREAMS")
print("=" * 70)

for i, task in enumerate(result["tasks"], 1):
    print(f"\n{i}. {task}")


print("\n" + "=" * 70)
print("WORKER RESULTS")
print("=" * 70)

for result_item in result["worker_results"]:
    print("\n" + result_item)


print("\n" + "=" * 70)
print("FINAL SYNTHESIS")
print("=" * 70)

print(result["synthesis"])


print("\n" + "=" * 70)
print("ORCHESTRATOR-WORKERS SCENARIO COMPLETE")
print("=" * 70)