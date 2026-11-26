"""v1_1: v1 with a threshold. Same prompt and pipeline, so it's scored from v1's results.

Threshold picked by the rule in benchmark/report.py on v1's dev results (commit 6e5320b):
the highest threshold whose recall is within 5 points of threshold 0.0.
"""

from reviewer.configs import ReviewConfig

CONFIG = ReviewConfig(name="v1_1", prompt="v1", temperature=0.0, max_retries=2, threshold=0.7)
