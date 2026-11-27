# gdpr-reviewer

An AI reviewer that catches personal-data risks in pull requests, with published numbers on
how often it's right.

**Status:** early development. The benchmark results table will appear here once the first
reviewer version is measured.

## This tool is itself a data transfer

The reviewer sends the diffs it reviews to [OpenRouter](https://openrouter.ai) and from
there to the upstream model provider. The GitHub Action's setup docs say what is sent and
how to keep files out: [action/README.md](action/README.md).

## This repo reviews its own pull requests

Every PR here is reviewed by the GDPR PR Reviewer: a workflow starts the review service
inside the job and runs the Action against it. Three paths are left out of that review, in
[.gdpr-reviewer.yml](.gdpr-reviewer.yml):

- `benchmark/cases/`: each case is a diff full of fake personal data, written so the
  reviewer has something to find. Reviewing them would comment on every case.
- `benchmark/results/`: the reviewer's own findings on those cases.
- `tests/fixtures/`: test inputs of the same kind.

Everything else is reviewed, tests included. Under the
[policy](docs/personal-data-policy.md), clearly fake data in tests is not a finding, so
test PRs also check that rule live.

## Development

```
uv sync
uv run pytest
uv run ruff check .
```
