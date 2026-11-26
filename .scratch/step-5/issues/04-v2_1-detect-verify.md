# v2_1: LangGraph detect -> verify
Status: ready-for-agent · Blocked by: 03

`pipeline: detect_verify` in the config. LangGraph graph with detect and verify nodes; both
call the LLM seam, record every call, reuse the retry rules. Verify drops or re-scores each
candidate. Fake-adapter tests for keep, drop, re-score, verify failure.
Done when: tests pass; paid dev run after commit.
