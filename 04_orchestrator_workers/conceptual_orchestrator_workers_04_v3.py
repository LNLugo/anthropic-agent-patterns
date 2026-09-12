diagram = """
flowchart TD

    START([START])
    ORCH[ORCHESTRATOR<br/>LLM dynamically decomposes problem]
    SEND{{Send()<br/>Dynamic Fan-Out}}

    W1[Worker 1]
    W2[Worker 2]
    W3[Worker 3]
    W4[Worker 4]
    W5[Worker N]

    SYNTH[SYNTHESIS]
    END([END])

    START --> ORCH
    ORCH --> SEND

    SEND --> W1
    SEND --> W2
    SEND --> W3
    SEND --> W4
    SEND --> W5

    W1 --> SYNTH
    W2 --> SYNTH
    W3 --> SYNTH
    W4 --> SYNTH
    W5 --> SYNTH

    SYNTH --> END
"""

print(diagram)