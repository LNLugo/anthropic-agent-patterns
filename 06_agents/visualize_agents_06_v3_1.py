from pathlib import Path
from agents_06_v3_1 import app


graph = app.get_graph()

png_bytes = graph.draw_mermaid_png()

output_path = Path(__file__).resolve().parent / "agents_06_v3_1_graph.png"

with open(
    output_path,
    "wb"
) as f:
    f.write(png_bytes)

print(
    f"Graph saved as {output_path}"
)