# 06: This repo reviews its own PRs

**What to build:** Every PR opened from a branch of this repository gets a review. A
workflow starts the service inside the job and runs the local Action against it. The
benchmark's fake personal data is excluded on purpose, and the Action gets setup docs.

**Blocked by:** 03 (Action: skips, ignored_paths and errors), 04 (Action: no repeat comments)

**Status:** ready-for-agent

- [ ] A root `.gdpr-reviewer.yml` whose `ignored_paths` covers the benchmark cases, the
      benchmark results and the test fixtures.
- [ ] A workflow on `pull_request` (opened, synchronize, reopened, ready_for_review), never
      `pull_request_target`, with `pull-requests: write` and `contents: read`. It installs
      with uv and starts the service on localhost, with a random token per job and the
      flash model pinned to `xiaomi`. It waits for `/healthz`, then runs the local Action.
- [ ] The workflow uses the repo secret `OPENROUTER_API_KEY`. Fork PRs are skipped by the
      Action's own skip.
- [ ] Action setup docs cover:
      - the inputs, the required permissions and the trigger to use;
      - the data-transfer note: diffs go to the service, to OpenRouter and to the upstream
        provider;
      - the advice to set a credit limit on the OpenRouter key.
- [ ] The main README says why the benchmark paths are excluded: they hold fake personal
      data by design.
- [ ] Update the CLAUDE.md status line for Step 6.
- [ ] Done when a PR in this repo gets a review from the in-job service, or, when the PR is
      clean, no review and a job-summary line. This needs the user to add the secret. Pushing
      and opening the PR need the user's OK.
