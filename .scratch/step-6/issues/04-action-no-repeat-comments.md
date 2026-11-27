# 04: Action: no repeat comments

**What to build:** When the Action runs again on the same PR, after a new push or a
reopen, it does not comment again on a finding it already posted. If no new findings are
left, it posts no review.

**Blocked by:** 02 (Action: post one review)

**Status:** ready-for-agent

- [ ] Before posting, the Action lists the PR's existing review comments and collects the
      fingerprints from the hidden markers in its own earlier comments.
- [ ] A finding whose fingerprint is already on the PR is not posted inline and not listed
      in the body again.
- [ ] The comment cap and the confidence ordering apply to the new findings only.
- [ ] No new findings left: no review, and the job summary says how many were already
      posted.
- [ ] Markers on comments the Action did not write are ignored: only comments carrying the
      reviewer's own marker prefix count.
- [ ] Tests use fakes only. A second run with the same findings posts nothing. A run where
      a finding's line moved but its content did not posts nothing. A run with one new
      finding posts only that one. pytest and ruff pass.
