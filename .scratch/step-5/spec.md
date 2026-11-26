# Step 5: measured improvements

Agreed 2026-11-26. Order: 5b (improvements on the current 30 cases) first, then 5a (50 harder
cases), then every config is re-run on the larger dev split; only those re-runs go in the
README.

## 5b: improvements on the current case set

v1 x mimo-v2.6-pro on dev: recall 100% (n=8), precision 96%, 0 false alarms on 11 clean PRs.
Detection can't improve on this set; 5b measures cost, latency, duplicates, confidence
calibration and run-to-run stability. Each row records the case-set hash.

1. **Threshold (v1_1).** Rule: the highest threshold whose dev recall is within 5 points of
   threshold 0.0. Stored as `threshold` in a new config; scored from the existing v1 results,
   no new run. Expected: 0.7.
2. **Flash row.** v1 x `xiaomi/mimo-v2.6-flash` pinned to `xiaomi`. The runner takes
   `--model` / `--provider` (default: `.env`).
3. **v2.** v1 plus one prompt rule: one finding per risk; fields added together, or one call
   sending several values, are one finding at the first line.
4. **v2_1.** LangGraph detect -> verify. Detect = v2 prompt. Verify = one call per candidate
   (policy + finding + that file's numbered diff): keep or drop, and a new confidence.
   LangGraph defines the graph only; calls go through the `LLM` seam.
5. **v3** (repo context) waits for 5a: no current case has a `context/` folder.

Dropped: RAG. The policy fits in the prompt; article text isn't scored; the data map is v3.

## 5a: harder cases (after 5b)

+50 cases (28 positive, 10 negative, 12 tricky) -> 80 total; ~15 adapted real-world commits
(MIT/Apache, url + license, all values replaced with fakes), ~35 harder synthetic. Patterns:
indirect names, data via helpers/serializers, whole-object logging, PII in exceptions and
error-tracker context, third parties via SDK clients, quietly weakened erasure, large noisy
PRs (2+ per category), risky-looking clean PRs. Optional label fields `difficulty`
(easy|hard) and `patterns`; report shows recall by both. Reviewed in 5 batches of 10.
Open questions for 5a's own design round: adapted-case rules, test-split timing.

## Alongside: Step 0

Short prior-art survey in `docs/prior-art.md`.
