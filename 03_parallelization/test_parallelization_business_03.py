from parallelization_03 import app


sample_text = """
A manufacturing company wants to introduce an enterprise AI assistant
to help employees find operational information, reduce support costs,
accelerate decision-making, and improve productivity across its plants.
The solution will initially serve 5,000 employees.
"""


result = app.invoke({
    "text": sample_text
})


print("=" * 70)
print("PARALLELIZATION TEST — BUSINESS")
print("=" * 70)

print("\nINPUT:")
print(sample_text.strip())

print("\nTECHNICAL ANALYSIS:")
print(result["technical_analysis"])

print("\nBUSINESS ANALYSIS:")
print(result["business_analysis"])

print("\nRISK ANALYSIS:")
print(result["risk_analysis"])

print("\nFINAL SYNTHESIS:")
print(result["synthesis"])

print("\n" + "=" * 70)
print("BUSINESS SCENARIO COMPLETE")
print("=" * 70)