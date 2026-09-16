from importlib.util import spec_from_file_location, module_from_spec


# ---------------------------------------------------------
# Load Routing V2
# ---------------------------------------------------------

module_path = "02_routing/routing_02_v2.py"

spec = spec_from_file_location(
    "routing_02_v2",
    module_path
)

module = module_from_spec(spec)
spec.loader.exec_module(module)

app = module.app


# ---------------------------------------------------------
# Test Scenarios
# ---------------------------------------------------------

scenarios = [
    {
        "name": "News",
        "text": """
        Microsoft announced a new enterprise AI platform today
        that will allow organizations to deploy AI assistants
        across their workforce.
        """
    },
    {
        "name": "Research",
        "text": """
        A new research study demonstrates that
        retrieval-augmented generation can improve the
        accuracy of enterprise AI assistants.
        """
    },
    {
        "name": "Blog",
        "text": """
        In this article, I discuss five lessons I learned
        while designing an enterprise AI architecture.
        """
    },
    {
        "name": "Other",
        "text": """
        An enterprise wants to build an AI platform
        for 10,000 employees.
        """
    }
]


# ---------------------------------------------------------
# Execute Tests
# ---------------------------------------------------------

print("\nROUTING V2 — FOUR PATH TEST")
print("=" * 60)

passed = 0

for scenario in scenarios:

    result = app.invoke({
        "text": scenario["text"],
        "route": "",
        "selected_path": "",
        "response": ""
    })

    expected = scenario["name"].lower()
    actual_route = result["route"]
    actual_path = result["selected_path"]

    print(f"\nSCENARIO: {scenario['name']}")
    print(f"LLM ROUTE:              {actual_route}")
    print(f"LANGGRAPH SELECTED:     {actual_path}")

    if actual_route == expected and actual_path == expected:
        print("STATUS: PASS")
        passed += 1
    else:
        print("STATUS: FAIL")


# ---------------------------------------------------------
# Summary
# ---------------------------------------------------------

print("\n" + "=" * 60)
print(f"RESULT: {passed}/{len(scenarios)} routes passed")

if passed == len(scenarios):
    print("PASS — LLM successfully selected all four approved routes.")
else:
    print("FAIL — One or more routing scenarios did not match.")