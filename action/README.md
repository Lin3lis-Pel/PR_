# GDPR PR Reviewer: GitHub Action

Posts one review on a pull request, with an inline comment for each personal-data risk the
reviewer finds. The four risks are personal data stored with no protection, written to
logs, sent to a new third party, or erasure logic weakened. A PR with no findings gets no
review, only a line in the job summary. Every comment ends with "Automated check, not legal
advice."

The Action does all the GitHub work. The review itself runs in a separate service, which
gets the diff and returns findings, and never sees your GitHub token (see
[ADR 0005](../docs/adr/0005-service-never-touches-github.md)).

## Before you install it: where your code goes

**This Action sends your PR's diff out of GitHub.** The diff goes to the review service,
from there to [OpenRouter](https://openrouter.ai), and from OpenRouter to the upstream
model provider. The service's operator picks the model: the default is Xiaomi's
`mimo-v2.6-flash`, served by Xiaomi. If the code in your PRs must not leave GitHub, don't
use this Action.

Two ways to send less:
- List files that should never be sent under `ignored_paths` in `.gdpr-reviewer.yml`
  (below). They are removed from the diff before anything is sent.
- The service keeps nothing: it logs only metadata (sizes, counts, cost, latency, model)
  and stores no diffs or findings.

## Setup

1. **Run the review service** somewhere your workflow can reach. For now that means
   running it yourself (`uv run uvicorn service.app:app`, with the environment below); a
   Cloud Run deployment comes in Step 8. The service needs:
   - `OPENROUTER_API_KEY`: the OpenRouter key it spends. **Set a credit limit on this key
     in OpenRouter's dashboard.** That limit is the only spending cap: the service has no
     budget of its own, and anyone with the service token can spend up to it (see
     [ADR 0006](../docs/adr/0006-bearer-token-and-the-cost-ceiling.md)).
   - `GDPR_REVIEWER_TOKEN`: a long random token callers must send. The service won't start
     without one.
   - Optional: `GDPR_REVIEWER_CONFIG` (default `v2`), `GDPR_REVIEWER_MODEL` and
     `GDPR_REVIEWER_PROVIDER` (the model and its pinned provider),
     `GDPR_REVIEWER_MAX_DIFF_BYTES` (default 200,000).
2. **Add two repo secrets** in the repo you want reviewed: the service's URL, and the same
   token as `GDPR_REVIEWER_TOKEN`.
3. **Add a workflow**, for example `.github/workflows/gdpr-review.yml`:

   ```yaml
   name: GDPR review
   on:
     pull_request:
       types: [opened, synchronize, reopened, ready_for_review]
   permissions:
     contents: read
     pull-requests: write
   concurrency:
     group: gdpr-review-${{ github.event.pull_request.number }}
     cancel-in-progress: true
   jobs:
     review:
       runs-on: ubuntu-latest
       steps:
         - uses: Lin3lis-Pel/PR_/action@main
           with:
             service-url: ${{ secrets.GDPR_REVIEWER_URL }}
             service-token: ${{ secrets.GDPR_REVIEWER_TOKEN }}
   ```

   Use `pull_request`, never `pull_request_target`. The latter runs with secrets on code
   from forks.

## Inputs

| Input | Default | What it does |
|---|---|---|
| `service-url` | required | Base URL of the review service. |
| `service-token` | required | The service's bearer token. Pass it from a secret. |
| `github-token` | the workflow token | Reads the diff and the config, and posts the review. Needs `pull-requests: write` and `contents: read`. |
| `max-comments` | `10` | Most inline comments per review, highest confidence first. The rest are listed in the review body. |
| `fail-on-error` | `false` | When `true`, the job fails if the review could not run. Findings never fail the job. |
| `config-file` | `.gdpr-reviewer.yml` | Where the repo's config is, read at the PR's head commit. |

## `.gdpr-reviewer.yml`

The Action reads one key, `ignored_paths`: globs for files that are never sent.

```yaml
ignored_paths:
  - vendor/**
  - "**/*.lock"
  - docs/
```

- Globs match the whole repo-relative path. `*` and `?` stay inside one folder, `**`
  spans any number of folders (none included), and a trailing `/` means everything under
  that folder. So `*.lock` matches only `uv.lock` at the root; use `**/*.lock` for every
  folder. This is not `.gitignore` matching.
- A renamed file is dropped if either its old or its new path matches. A deleted file is
  matched on its old path.
- If the Action can't read `ignored_paths` (a string instead of a list, an unclosed `[`),
  it skips the review with a warning rather than guess and send files you meant to
  exclude.
- The file is read from the PR's own head, so a PR can change it. A change to
  `ignored_paths` shows in the PR's diff like any other line. Fork PRs are skipped, so
  outside contributors can't use this.

## What it does on each run

- **Skips**, with a line in the job summary and nothing sent: draft PRs, PRs from forks
  (their runs get no secrets), diffs where every file is ignored, diffs with no changed
  lines (only renames or binary files), and diffs over 200,000 bytes after filtering.
- **Reviews the whole PR** at the commit the run was triggered for, then posts one review
  with `event: COMMENT`. It never requests changes.
- **Doesn't repeat itself.** Each comment carries a hidden marker. A later run (a new
  push, a reopen) posts only findings that aren't on the PR yet, even if their line
  moved.
- **Turns failures into warnings.** If the service or GitHub fails, the job shows a
  warning and passes, unless `fail-on-error` is `true`.
- **Puts the cost in the job summary**, never in the review.
