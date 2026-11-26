"""v1: the baseline. One LLM call per PR: policy summary, categories, numbered diff."""

from reviewer.configs import ReviewConfig

CONFIG = ReviewConfig(name="v1", prompt="v1", temperature=0.0, max_retries=2)
