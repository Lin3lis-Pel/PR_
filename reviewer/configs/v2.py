"""v2: v1 plus one prompt rule: one finding per risk (fields added together are one)."""

from reviewer.configs import ReviewConfig

CONFIG = ReviewConfig(name="v2", prompt="v2", temperature=0.0, max_retries=2)
