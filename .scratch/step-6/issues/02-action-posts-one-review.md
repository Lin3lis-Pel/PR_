# 02: Action: post one review

**What to build:** A composite GitHub Action, run by one stdlib-only Python script. On a
pull request it fetches the full PR diff, sends it to the service and posts the result as
one review: inline comments for postable findings, everything else in the review body. A PR
with no findings gets no review, only a line in the job summary.

**Blocked by:** 01 (Service: POST /review end to end), for the response format

**Status:** done (2026-11-27)

- [x] A composite action with inputs `service-url`, `service-token`, `github-token`
      (default: the workflow token) and `max-comments` (default 10). It runs on the
      runner's Python and installs no dependencies.
- [x] Two seams, each with a fake for tests: a GitHub client (get PR, get diff, list review
      comments, create review) and a reviewer client (POST /review). No test calls GitHub
      or the service.
- [x] One review with `event: COMMENT`, never `REQUEST_CHANGES`.
- [x] Inline comments go to postable findings, highest confidence first, at most
      `max-comments`. Old-side findings go on `side: LEFT` and new-side ones on
      `side: RIGHT`. Line numbers are used as the service returns them, with no position
      mapping.
- [x] Inline comment text: category, article, explanation, suggested fix, confidence, then
      the footer "Automated check, not legal advice." Each comment also carries the
      finding's fingerprint in a hidden marker, which ticket 04 uses.
- [x] Review body: a short header and the counts by category. Then the findings that are
      not postable or are over the cap (file:line, category, explanation), and the line
      "Reviewed by config <c> x model <m>. The diff was sent to OpenRouter and the upstream
      provider." It ends with the footer.
- [x] No findings: no review, and one job-summary line with the counts and cost. Cost
      always goes to the job summary, never to the review.
- [x] Findings never fail the job.
- [x] Tests cover ordering, findings over the cap moving to the body, LEFT/RIGHT,
      non-postable findings in the body, and the clean-PR path. pytest and ruff pass.
