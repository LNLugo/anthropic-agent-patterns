from routing_02 import app


# ---------------------------------------------------------
# Test Cases
# ---------------------------------------------------------

test_cases = [
    {
        "name": "TEST 1 — NEWS",
        "expected_path": "news",
        "text": """
        Microsoft announced a new enterprise AI platform today,
        expanding its cloud services for organizations building
        production AI applications.
        """
    },
    {
        "name": "TEST 2 — RESEARCH",
        "expected_path": "research",
        "text": """
        Researchers at Stanford University published a new study
        examining how structured inference techniques can improve
        the reasoning performance of large language models.
        """
    },
    {
        "name": "TEST 3 — BLOG",
        "expected_path": "blog",
        "text": """
        In this article, we explore five practical lessons for
        enterprise architects building reliable AI systems and
        explain why architecture matters more than simply
        selecting a powerful language model.
        """
    }
]


# ---------------------------------------------------------
# Execute Tests
# ---------------------------------------------------------

print("=" * 70)
print("ROUTING — THREE PATH VALIDATION TESTS")
print("=" * 70)


for test_case in test_cases:

    result = app.invoke({
        "text": test_case["text"]
    })

    actual_path = result["selected_path"]

    print("\n" + "-" * 70)
    print(test_case["name"])
    print("-" * 70)

    print("\nINPUT")
    print(test_case["text"].strip())

    print("\nCLASSIFIER DECISION")
    print(f"  {result['classification']}")

    print("\nROUTER DECISION")
    print(f"  Selected path: {actual_path}")

    print("\nEXPECTED PATH")
    print(f"  {test_case['expected_path']}")

    print("\nROUTING VALIDATION")

    if actual_path == test_case["expected_path"]:
        print("  PASS — Correct path selected")
    else:
        print("  FAIL — Incorrect path selected")

    print("\nEXECUTED NODE")
    print(f"  {actual_path}")


# ---------------------------------------------------------
# Complete
# ---------------------------------------------------------

print("\n" + "=" * 70)
print("THREE-PATH ROUTING VALIDATION COMPLETE")
print("=" * 70)