from prompt_chaining_01 import app


# Get the compiled graph
graph = app.get_graph()

# Render graph as PNG
png = graph.draw_mermaid_png()

# Save PNG
with open("prompt_chaining_01_graph.png", "wb") as f:
    f.write(png)

print("Graph saved to prompt_chaining_01_graph.png")