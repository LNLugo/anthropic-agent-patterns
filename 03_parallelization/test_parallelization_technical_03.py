import time

from parallelization_03 import app


sample_text = """
An enterprise is designing an AI platform using Azure Kubernetes Service,
a model gateway, vector search, an enterprise data lake, and API-based
integration with existing business applications. The platform must support
high availability, horizontal scaling, and multiple AI models.
"""


print("=" * 70)
print("PARALLELIZATION TEST — TECHNICAL")
print("=" * 70)

print("\nINPUT:")
print(sample_text)

# ---------------------------------------------------------
# Execute the complete parallel workflow
# ---------------------------------------------------------

start_time = time.perf_counter()

result = app.invoke({
    "text": sample_text
})

total_duration = time.perf_counter() - start_time


# ---------------------------------------------------------
# Display results
# ---------------------------------------------------------

print("\nTECHNICAL ANALYSIS:")
print(result["technical_analysis"])

print("\nBUSINESS ANALYSIS:")
print(result["business_analysis"])

print("\nRISK ANALYSIS:")
print(result["risk_analysis"])

print("\nFINAL SYNTHESIS:")
print(result["synthesis"])


# ---------------------------------------------------------
# Timing
# ---------------------------------------------------------

technical_time = result["technical_duration"]
business_time = result["business_duration"]
risk_time = result["risk_duration"]

branch_sum = (
    technical_time
    + business_time
    + risk_time
)

print("\n" + "=" * 70)
print("EXECUTION TIMING")
print("=" * 70)

print(f"\nTechnical branch: {technical_time:.2f} seconds")
print(f"Business branch:  {business_time:.2f} seconds")
print(f"Risk branch:      {risk_time:.2f} seconds")

print(f"\nSum of branch times: {branch_sum:.2f} seconds")
print(f"Total workflow time: {total_duration:.2f} seconds")

print("\n" + "=" * 70)

if total_duration < branch_sum:
    print(
        "PASS — Total workflow time is less than "
        "the sum of independent branch times."
    )
else:
    print(
        "OBSERVATION — Timing did not demonstrate "
        "a parallel execution advantage in this run."
    )

print("=" * 70)

print("\nTECHNICAL SCENARIO COMPLETE")
print("=" * 70)