# LangGraph nodes call our LLM seam, not ChatOpenAI

Multi-step pipelines (v2_1 detect -> verify and later) are LangGraph graphs, but every node makes its model calls through `reviewer/llm.py`'s `LLM` protocol, not through `langchain-openai`'s `ChatOpenAI`. The seam records the upstream provider and OpenRouter's reported cost on every call, the fake adapter keeps unit tests offline, and only `llm.py` imports an LLM SDK. LangGraph is used only to define the graph and move state between nodes. Retries and output validation are shared with the single-call pipeline (`_ask` in `reviewer/review.py`), so every pipeline counts failed attempts and their cost the same way.

**Considered options:** `ChatOpenAI` pointed at OpenRouter inside the nodes. Rejected because OpenRouter's `provider` and `usage.cost` fields are harder to reach through LangChain's response metadata, and it would add a second way of calling models with its own retry behaviour.
