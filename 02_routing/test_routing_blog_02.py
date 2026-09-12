from routing_02 import app


sample_text = """
In this article, we explore five practical lessons for
enterprise architects building reliable AI systems and
explain why architecture matters more than simply
selecting a powerful language model.
"""


result = app.invoke({
    "text": sample_text
})


print("=" * 60)
print("ROUTING TEST — BLOG")
print("=" * 60)

print("\nINPUT:")
print(sample_text.strip())

print("\nCLASSIFIER DECISION:")
print(result["classification"])

print("\nSELECTED PATH:")
print(result["selected_path"])

print("\nEXPECTED PATH:")
print("blog")

print("\nVALIDATION:")

if result["selected_path"] == "blog":
    print("PASS — Blog path correctly selected")
else:
    print("FAIL — Incorrect path selected")

print("\nEXECUTED NODE:")
print(result["selected_path"])

print("=" * 60)