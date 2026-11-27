# Step 6: service + GitHub Action

Agreed 2026-11-27 (design grill, 21 decisions). Deploying to Cloud Run is Step 8; learning
from PRs is Step 6.5. Neither is part of this step.

## Shape

The Action does all GitHub I/O. The service turns a diff into findings and never sees a
GitHub token (ADR 0005). Both are thin wrappers: review logic stays in `reviewer/`.

```
PR event -> Action: fetch diff, drop ignored_paths -> POST /review (bearer token)
         -> service: review() with fake/OpenRouter LLM, threshold, postable flag
         -> Action: dedupe against posted markers -> one COMMENT review
```

## Service (`service/`, FastAPI)

- `POST /review`. Request `{diff: str}`. Response
  `{findings: [Finding + postable: bool + fingerprint: str], config, model, provider,
  cost_usd, latency_s}`.
- `fingerprint` is a hash of file, category and the commented line's text, not its line
  number. The service computes it, so the Action never parses hunks (changed 2026-11-27,
  while writing the tickets).
- Config and model come from the service's env, never from the request:
  `GDPR_REVIEWER_CONFIG` (default `v2`) and the existing `GDPR_REVIEWER_MODEL` /
  `GDPR_REVIEWER_PROVIDER`. The deployed default is v2 x `xiaomi/mimo-v2.6-flash` pinned to
  `xiaomi`, which matches the v2 x flash test row.
- The service applies the config's `threshold`. Callers get shown findings only.
- `postable` is true when file, line and side fall inside a hunk of the diff, context
  lines included. The service computes it with `reviewer/diff.py`.
- Errors: `401` for a missing or wrong token. `422` for an empty, unparseable or oversized
  diff (limit 200 KB, setting `GDPR_REVIEWER_MAX_DIFF_BYTES`). `502` when `review()` raises
  `ReviewError`. The error body never echoes the diff.
- `GET /healthz` needs no token and returns config and model names.
- Auth: `Authorization: Bearer <GDPR_REVIEWER_TOKEN>`, compared in constant time. If the
  token is unset, the service refuses to start. The check sits behind one small seam, so
  GitHub OIDC can replace it later (ADR 0006).
- The endpoint is a plain `def`, so FastAPI runs it in its thread pool.
- Settings live in `service/settings.py`. `reviewer/config.py` is unchanged.
- Logging covers metadata only: request id, diff bytes, number of findings, cost, latency,
  model, serving provider. It never includes diffs, finding text or the token. Nothing is
  stored, and there is no Langfuse in this step.
- Cost ceiling: there is no in-service spend counter, because instances are stateless. The
  setup docs tell the operator to set a credit limit on the OpenRouter key. One request is at
  most 1 + `max_retries` calls on a diff of at most 200 KB.
- Deps: `fastapi` and `uvicorn` (runtime), `httpx` (dev, for `TestClient`). No Dockerfile
  until Step 8.
- Run: `uv run uvicorn service.app:app --reload`.

## Action (`action/`, composite)

- `action/action.yml` runs one stdlib-only Python script on the runner's Python. It needs
  no `uv sync` and no image.
- Inputs: `service-url`, `service-token`, `github-token` (default `github.token`),
  `max-comments` (default 10), `fail-on-error` (default false), `config-file` (default
  `.gdpr-reviewer.yml`).
- Trigger (documented for users): `pull_request: [opened, synchronize, reopened,
  ready_for_review]`, never `pull_request_target`. It needs `pull-requests: write` and
  `contents: read`.
- Skips, each with a job-summary notice: draft PRs, fork PRs (secrets are not available to
  them), diffs over the limit, diffs that are empty after filtering.
- It fetches the full PR diff on every run: the compare diff between the event's base and
  head commits (`Accept: application/vnd.github.diff`). That is the same three-dot diff the
  PR shows, pinned to the commit the review is posted against, so a push during the run
  can't shift the lines (changed 2026-11-27, in ticket 02).
- `ignored_paths` from `.gdpr-reviewer.yml` (read from the PR head) uses globs on
  repo-relative paths, and `**` matches any number of folders. A rename is dropped if
  either its old or its new path matches. A deleted file is matched on its old path. Other
  keys in the file are ignored until v3 / Step 6.5. YAML is parsed with a minimal stdlib
  reader for this one key (list of strings). Anything else in the file is skipped, not
  parsed.
- Dedupe: each inline comment carries `<!-- gdpr-reviewer:<fingerprint> -->`, using the
  fingerprint the service returns. Findings whose fingerprint is already on the PR are not
  posted again.
- One review, `event: COMMENT`, never `REQUEST_CHANGES`:
  - Inline: postable, new findings, highest confidence first, at most `max-comments`. A
    finding on an old-side line goes on `side: LEFT`, a new-side one on `side: RIGHT`.
  - Inline comment text: category, article, explanation, suggested fix, confidence, footer.
  - Body: short header, counts by category, findings that are not postable or are over the
    cap (file:line, category, explanation), the line "Reviewed by config <c> x model <m>.
    The diff was sent to OpenRouter and the upstream provider.", footer.
  - Footer: "Automated check, not legal advice."
- No new findings: no review. The job summary gets one line with the counts and cost.
- Findings never fail the check. A service error or a GitHub API error becomes a warning
  annotation, and the job fails only if `fail-on-error: true`.
- Referenced as `Lin3lis-Pel/PR_/action@main` until a `v1` tag in Step 9.

## Seams and tests

- LLM: the existing fake adapter, put in through a FastAPI dependency override. Service
  tests use `TestClient`.
- `GitHubClient` (get PR, get diff, get file, list review comments, create review) and
  `ReviewerClient` (POST /review). Each has a fake for the Action script's tests. No unit
  test calls GitHub, the service or an LLM.
- Built test-first (`tdd`). Cover the postable flag (added, removed, context, outside hunk,
  renamed, wrong side), ignored_paths (rename, delete, `**`), dedupe, the cap and ordering,
  LEFT/RIGHT, the skips, and the error paths.

## This repo reviews its own PRs

- A root `.gdpr-reviewer.yml` with `ignored_paths: [benchmark/cases/**,
  benchmark/results/**, tests/fixtures/**]`. The README says why: those files hold fake
  personal data on purpose.
- `.github/workflows/gdpr-review.yml`: on same-repo PRs, start the service with uvicorn on
  localhost inside the job (a random `GDPR_REVIEWER_TOKEN` per job), wait for `/healthz`,
  then run the local Action against it. It uses repo secret `OPENROUTER_API_KEY` and the
  flash model, at about $0.0001 per PR.
- The user adds the secret and approves the first push.

## Docs

- ADR 0005: the service is GitHub-agnostic, and the Action does all GitHub I/O.
- ADR 0006: bearer-token auth now, OIDC later; the cost ceiling is the key's credit limit.
- `GLOSSARY.md`: Service, Action, Shown finding (at or above the config's threshold),
  Posted finding (a shown finding sent as an inline comment).
- `action/README.md`: setup, inputs, permissions, the data-transfer note (diffs go to the
  service, OpenRouter and the upstream provider), and the credit-limit advice.
- CLAUDE.md: move the status line to Step 6 in progress / done when it lands.

## Done when

- `uv run pytest` and ruff pass, with service and Action tests that use fakes only.
- `uv run uvicorn service.app:app` answers `/healthz` and `POST /review` locally.
- After the user's OK to push, a PR in this repo gets a review from the in-job service, or
  no review plus a job-summary line when the PR is clean.
