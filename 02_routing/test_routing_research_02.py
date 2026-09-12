from routing_02 import app


sample_text = """
Researchers at Stanford University published a new study
examining how structured inference techniques can improve
the reasoning performance of large language models.
"""


result = app.invoke({
    "text": sample_text
})


print("=" * 60)
print("ROUTING TEST — RESEARCH")
print("=" * 60)

print("\nINPUT:")
print(sample_text.strip())

print("\nCLASSIFIER DECISION:")
print(result["classification"])

print("\nSELECTED PATH:")
print(result["selected_path"])

print("\nEXPECTED PATH:")
print("research")

print("\nVALIDATION:")

if result["selected_path"] == "research":
    print("PASS — Research path correctly selected")
else:
    print("FAIL — Incorrect path selected")

print("\nEXECUTED NODE:")
print(result["selected_path"])

print("=" * 60)