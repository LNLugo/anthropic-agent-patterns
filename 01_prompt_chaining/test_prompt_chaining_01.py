from prompt_chaining_01 import app


# ---------------------------------------------------------
# Test Input
# ---------------------------------------------------------

sample_text = """
Anthropic's MCP (Model Context Protocol) is an open-source
standard that allows applications to interact with APIs,
tools, and external systems.
"""


# ---------------------------------------------------------
# Initial State
# ---------------------------------------------------------

state_input = {
    "text": sample_text
}


# ---------------------------------------------------------
# Execute Workflow
# ---------------------------------------------------------

result = app.invoke(state_input)


# ---------------------------------------------------------
# Display Results
# ---------------------------------------------------------

print("=" * 60)
print("PROMPT CHAINING — TEST RESULTS")
print("=" * 60)

print("\nInput:")
print(sample_text.strip())

print("\nClassification:")
print(result["classification"])

print("\nEntities:")
for entity in result["entities"]:
    print(f"  - {entity}")

print("\nSummary:")
print(result["summary"])

print("\n" + "=" * 60)