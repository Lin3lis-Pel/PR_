# Store raw findings, score later

Benchmark runs store every finding the reviewer returns, with its confidence and no threshold applied, plus cost, latency, model and upstream provider. Scores are always recomputed from these files. One paid run then gives the whole threshold curve, and fixing a scorer bug or adding a metric never means paying to rerun the LLM. The deployed reviewer still applies its own threshold before posting comments.

**Consequences:** the reviewer must return low-confidence findings to the benchmark instead of dropping them, and result files are larger than a table of scores would be.
