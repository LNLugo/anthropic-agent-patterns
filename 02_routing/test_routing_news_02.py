from routing_02 import app


sample_text = """
Microsoft announced a new enterprise AI platform today,
expanding its cloud services for organizations building
production AI applications.
"""


result = app.invoke({
    "text": sample_text
})


print("=" * 60)
print("ROUTING TEST — NEWS")
print("=" * 60)

print("\nINPUT:")
print(sample_text.strip())

print("\nCLASSIFIER DECISION:")
print(result["classification"])

print("\nSELECTED PATH:")
print(result["selected_path"])

print("\nEXPECTED PATH:")
print("news")

print("\nVALIDATION:")

if result["selected_path"] == "news":
    print("PASS — News path correctly selected")
else:
    print("FAIL — Incorrect path selected")

print("\nEXECUTED NODE:")
print(result["selected_path"])

print("=" * 60)