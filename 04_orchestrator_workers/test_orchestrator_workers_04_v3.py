from orchestrator_workers_04_v3 import app


architecture_problem = """
An enterprise wants to build an AI platform for 10,000 employees.
The platform will provide RAG-based knowledge search, AI assistants,
multiple foundation models, integration with enterprise applications,
and must meet security, compliance, scalability, and cost requirements.
"""


result = app.invoke({
    "text": architecture_problem,
    "tasks": [],
    "worker_results": [],
    "synthesis": ""
})


print("\n" + "=" * 70)
print("DYNAMICALLY GENERATED WORKSTREAMS")
print("=" * 70)

for i, task in enumerate(result["tasks"], 1):
    print(f"{i}. {task}")


print("\n" + "=" * 70)
print("WORKER RESULTS")
print("=" * 70)

for i, worker_result in enumerate(result["worker_results"], 1):
    print(f"\n--- WORKER {i} ---")
    print(worker_result)


print("\n" + "=" * 70)
print("FINAL SYNTHESIS")
print("=" * 70)
print(result["synthesis"])


print("\n" + "=" * 70)
print("SCENARIO COMPLETE")
print("=" * 70)