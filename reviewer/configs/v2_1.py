"""v2_1: v2 detect, then a verify call per finding that keeps or drops it (LangGraph)."""

from reviewer.configs import ReviewConfig

CONFIG = ReviewConfig(
    name="v2_1",
    prompt="v2",
    temperature=0.0,
    max_retries=2,
    pipeline="detect_verify",
    verify_prompt="verify_v1",
)
