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

## 5a decisions (agreed 2026-11-26)

- **Adapted sources:** privacy-fix commits on public GitHub ("don't log email", "redact
  PII", ...). The reversed fix is a positive case; the fix itself is a clean case. Search
  with `gh` (read-only; the user signs in).
- **Licenses:** MIT, Apache-2.0, BSD-2/3-Clause, ISC only. Notices in
  `benchmark/cases/THIRD_PARTY_NOTICES.md`. The repo itself is MIT (`LICENSE`).
- **Adapting:** keep structure, paths and noise; replace every real-looking value with
  policy fakes; trim unrelated files only above 600 changed lines; every change logged in
  the case's NOTES.md line; `stack` is the real stack.
- **Labels:** optional `difficulty` (easy | hard) and `patterns` (fixed vocabulary in
  `benchmark/cases.py`). All 30 existing cases are `easy`; their patterns need the user's OK.
- **Synthetic cases:** generated in the scratchpad from before/after files, as in Step 3;
  hand-edited only after review.
- **Batches:** 1-3 = 30 synthetic; 4 = 5 synthetic + 5 adapted; 5 = 10 adapted. If fewer
  than 15 adapted commits qualify, say so before batch 4 and fill with synthetic.
- **Test split:** once, at the end of Step 5, after the dev re-runs: v1 x pro, best x pro,
  best x flash. Ask first.
- **pos-erasure-003:** removed hard-delete line (old 18) added as an optional issue.

## 5a stopped at 40 cases (2026-11-26)

The user stopped 5a after synthetic batch 1: "too many cases", focus on the tool. The
benchmark is 40 reviewed cases (30 easy + 10 hard; 24 dev / 16 test). Unreviewed batch 2 was
removed before commit. Adapted real-world cases are dropped for now. Remaining Step 5 work:
re-run the configs on the 24-case dev split, then one test-split run (ask first), then
Step 6.
