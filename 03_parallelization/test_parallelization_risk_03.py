from parallelization_03 import app


sample_text = """
A healthcare organization is considering an enterprise generative AI
platform that will process sensitive patient information. The platform
will use retrieval-augmented generation to answer clinical and
operational questions and will integrate with existing healthcare
applications.
"""


result = app.invoke({
    "text": sample_text
})


print("=" * 70)
print("PARALLELIZATION TEST — RISK")
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
print("RISK SCENARIO COMPLETE")
print("=" * 70)