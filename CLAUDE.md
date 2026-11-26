# GDPR PR Reviewer

An LLM reviewer that flags personal-data risks in pull requests and publishes benchmark
numbers on how often it is right. It runs as a GitHub Action (through a Cloud Run service)
and as an MCP server for Claude Code. This is a portfolio project: **the benchmark is the
main deliverable**, and the reviewer is what it measures.

## Status

Greenfield. Build order: 0 prior-art check → 1 skeleton → 2 benchmark format + scorer →
3 first 30 cases → 4 v1 baseline → 5 measured improvements (5b: threshold, cheap model, v2,
LangGraph; then 5a: 50 harder cases; then v3 repo context; RAG dropped, see
`.scratch/step-5/spec.md`) → 6 service + GitHub Action → 6.5 learning from PRs →
7 MCP server → 8 Cloud Run → 9 README + dashboard.

**Current step: 5a (+50 harder cases), not started; begin with its design round** (adapted-case
rules, test-split timing). 5b is done on the current 30 cases (dev, 3 runs each, case set
231e5725a20e41ba except the first v1 file): v1 pro recall 100% / precision 96%; v1_1
(threshold 0.7) identical; v1 x mimo-v2.6-flash 100% / 86%, 4x cheaper; v2 100% / 100%; v2_1
96% / 96% with p95 latency 34.5s. No row had a false alarm on the 11 clean PRs, so threshold
and verify can't show value until 5a. After 5a, every config is re-run on the larger dev
split for the README. Plan: `.scratch/step-5/spec.md`. Step 0 is done (`docs/prior-art.md`).
The test split has not been run. Cases: 30, all `reviewed: true`, 19 dev / 11 test,
hand-edited in place (no generator in the repo). Update this line when a step is done.

Domain terms are in `GLOSSARY.md`; decisions are in `docs/adr/`. Read both before changing
`benchmark/`.

## Scope: four categories only

| Code | Risk | Main GDPR articles |
|---|---|---|
| `PII_STORAGE` | New personal-data column (email, phone, address, IBAN, birth date) with no encryption or retention rule | Art. 32, 5(1)(e), 25 |
| `PII_LOGGING` | Personal data written to logs or analytics events | Art. 5(1)(c), 32 |
| `THIRD_PARTY_TRANSFER` | Personal data sent to a new third-party API | Art. 28, 44–46 |
| `ERASURE_REGRESSION` | Deletion or anonymisation logic removed or weakened | Art. 17 |

Out of scope: consent UIs, cookies, DPIAs, privacy policies, legal advice. Ask before
adding a category: it changes the benchmark and every published number.

## Design principles

- **Low noise beats high recall.** When the reviewer isn't confident, it says nothing. A
  false alarm on a clean PR costs more than a missed issue.
- **One deep module, thin wrappers.** All review logic lives in `reviewer/` behind one small
  interface: `review(diff, repo_context=None, *, config, llm) -> Review`, where `Review`
  holds `.findings` and `.calls` (one `CallRecord` per LLM request: model, provider, tokens,
  cost, cost method, latency). `service/`, `mcp_server/`, `action/` and `benchmark/` only
  call it. Never copy review logic into a wrapper.
- **Structured output only.** Every finding is a Pydantic `Finding`: `file`, `line`,
  `category`, `article`, `explanation`, `suggested_fix`, `confidence`.
- **Change one thing, then measure.** Each improvement is a new named config with its own
  results row. A row is **config x model**: the config is the prompt and pipeline, the model
  comes from `.env` (`docs/adr/0003`).

## LLM access: OpenRouter

- All model calls go through **OpenRouter** (OpenAI-compatible API,
  `https://openrouter.ai/api/v1`) with the key in `OPENROUTER_API_KEY`. The OpenRouter
  adapter uses the plain `openai` SDK, which exposes OpenRouter's `provider` and `usage.cost`
  fields. LangGraph defines pipelines only: its nodes call the `LLM` seam, never
  `langchain-openai` (`docs/adr/0004`).
- **The LLM client is a seam with two adapters:** the OpenRouter adapter and a fake/recorded
  adapter for tests. Nothing outside `reviewer/llm.py` imports an LLM SDK.
- **The model is chosen in `.env`, never in code:** `GDPR_REVIEWER_MODEL` (OpenRouter's
  `provider/model` form) and `GDPR_REVIEWER_PROVIDER` (upstream provider to pin; empty lets
  OpenRouter choose). `reviewer/config.py` reads them; switching models is an `.env` edit.
  Never hard-code a model ID. The runner's preflight checks the ID is listed on OpenRouter
  before spending anything. Current default: `xiaomi/mimo-v2.6-pro` pinned to `xiaomi`.
- **Reproducibility:** benchmark runs use temperature 0. OpenRouter can serve one model from
  different upstream providers, so record the provider that served each call, and pin the
  provider for open-weight models.
- **Cost:** record the cost OpenRouter reports for each request. If it isn't available, use
  token counts × the model's listed price, and mark in the results which method was used.
- OpenRouter makes cross-provider rows cheap to add (Claude, GPT, Gemini, open-weight
  models). Each one is a new config x model row, run on the same split.
- **This tool is itself a data transfer:** the diffs it reviews go to OpenRouter and the
  upstream provider. Say so in the README and in the Action's setup docs.

## Personal data: what counts

The policy lives in `docs/personal-data-policy.md` (rules for people, and the labelling
guide) and `reviewer/taxonomy.yaml` (the same rules for code). Each repo can declare its own
data in `.gdpr-reviewer.yml` (personal fields, pseudonymised fields, approved processors,
ignored paths). The reviewer never reads database contents or production data, only code.

## Learning from PRs (Step 6.5)

The repo's `.gdpr-reviewer.yml` grows with the code, and a human approves every change.

- **Learn new personal fields.** When a PR merges with a `PII_STORAGE` or `PII_LOGGING`
  finding on a field missing from the data map, and nobody rejected that finding, a
  workflow on `pull_request: closed` (merged only) opens a small PR: "Add `customer.iban`
  to the data map", linking the original PR and comment.
- **Learn from false alarms.** A 👎 reaction or a "not personal data" reply on a finding
  becomes a suggestion in the same kind of PR, adding the field to `not_personal` or
  `pseudonymised` so the false alarm doesn't repeat.
- **Rules:**
  - Never commit to the default branch. Every data-map change is a PR a human merges.
  - Only learn from merged PRs, never from open or closed-unmerged ones.
  - One data-map PR per source PR. If several are open, group the new fields into one.
  - Feedback is read from the GitHub API (reactions and replies). The optional feedback
    database only adds history for live-precision numbers. It is not needed for learning.
- **Benchmark stays fixed.** Each benchmark case carries its own frozen
  `.gdpr-reviewer.yml`, so learning never changes published numbers. Measure learning
  separately with **sequence cases**: PR 1 adds a field, PR 2 logs it. Report false alarms
  and misses on PR 2 with and without the learned map.

## Benchmark rules (read before touching `benchmark/`)

- One case per folder: `benchmark/cases/<id>/pr.diff`, `label.yaml`, and an optional
  `context/` folder (schema, `.gdpr-reviewer.yml`) read only by repo-context configs.
  The folder name must equal `id`. Unknown label fields are rejected.
  ```yaml
  id: pos-logging-007
  kind: positive            # positive | negative | tricky
  stack: django
  source: synthetic         # synthetic | adapted (adapted: url + license required)
  reviewed: false           # only the user sets this to true
  issues:
    - category: PII_LOGGING
      file: app/views.py    # new path; old path only for a deleted file
      line: 42
      end_line: 44          # optional, for multi-line issues
      side: new             # new (added lines) | old (removed lines, e.g. deleted erasure code)
      tolerance: 3          # default 3
      required: true        # false = borderline: never a hit, miss or false alarm
  ```
- `load_cases` rejects a label whose issue range covers no changed line on its side. A
  positive case needs a required issue; negative and tricky cases may only have optional ones.
- Every case has a line in `benchmark/NOTES.md` saying why it got its label. Every
  `required: false` issue needs one too, and they should stay rare.
- **Held-out split.** `benchmark/splits.yaml` assigns each case to `dev` or `test`: 40% test,
  stratified by kind and category, seeded, append-only (`uv run python -m benchmark.splits`
  assigns only new cases). While working on prompts, checklists or the graph, never open,
  print or tune against `test` cases. Run the `test` split only when the user asks for a
  version's final row.
- **Never change a label to raise a score.** If a label looks wrong, tell the user and log
  it in `NOTES.md`; the user decides.
- **`reviewed: true` only on the user's word.** LLM-drafted cases count only once the user
  has checked them. Set the flag only when the user explicitly approves a named batch in
  chat ("batch 2 reviewed"), and only for the cases in that batch.
- **What to flag** is decided by `docs/personal-data-policy.md`. Labels follow the policy; if
  a case doesn't fit, change the policy first (with the user's OK), then the label.
- **Matching rule** (`benchmark/score.py`, `docs/adr/0002`): category, file and side equal,
  line within the issue range ± tolerance; highest confidence first, one finding per issue;
  duplicates and wrong categories are false alarms. Changing this needs the user's OK.
- **Metrics:** recall (overall and per category), precision, location-only recall
  (diagnostic), false alarms per clean PR, % of clean PRs with a false alarm, error rate,
  invalid locations, cost per PR, p50/p95 latency. Clean PR = negative or tricky. A metric
  with nothing to measure is n/a, never 0% or 100%; show `n` next to every rate. Across 3
  runs, show mean with min–max.
- **Failed cases** (API error, bad output after 2 retries, or missing from a run) count as
  no findings and raise the error rate. Never drop them.
- **Results** (`docs/adr/0001`): one file per runner command at
  `benchmark/results/<config>-<model>-<split>-<timestamp>.json`, holding every run's raw
  findings with no threshold applied, plus model ID, pinned and serving providers, git
  commit, cost method, cost and latency. The runner refuses to run with uncommitted changes
  in `reviewer/`, `benchmark/`, `pyproject.toml` or `uv.lock`; `--allow-dirty` runs anyway
  and marks the file `dirty: true` (never publishable).
  The threshold is applied when scoring, so one paid run gives the whole threshold curve.
  Commit results with the code that produced them. The README table is generated from these
  files and never typed by hand.
- **Configs are frozen once published.** A config with results in the README is never
  edited, and neither are the prompt files it names: copy them to a new version name
  instead, so every row stays reproducible.

## Repo layout

```
reviewer/        the deep module: review(diff, repo_context, *, config, llm) -> Review
  review.py      review(), output schema, retries
  llm.py         LLM seam: OpenRouter adapter + fake adapter
  config.py      settings from .env (model, provider, key)
  configs/       one file per version (v1, v2, v2_1, ... v3_cheap)
  prompts/       prompt and checklist text as .md files, not inline strings
benchmark/       cases/, splits.yaml, NOTES.md, runner.py, report.py, score.py, results/
service/         FastAPI app for Cloud Run (POST /review)
mcp_server/      FastMCP server: review_diff, review_staged
action/          GitHub Action that posts one review with inline comments
docs/            results table, threshold curve, dashboard; adr/ for decisions
GLOSSARY.md      domain terms (created when the first term is settled)
.scratch/        tickets and specs (see Skills)
```

## Stack

Python 3.12, uv, Pydantic v2, `openai` SDK (pointed at OpenRouter), python-dotenv, pytest,
ruff, LangGraph (pipelines only). Planned: FastAPI (6), FastMCP (7), Langfuse (optional, 6). Deploy: GCP Cloud Run + Secret Manager.

## Commands

```
uv sync                                  # install deps (Python 3.12, pinned in .python-version)
uv run pytest                            # unit tests; skips tests marked `integration`
uv run pytest -m integration             # real LLM calls through OpenRouter: costs money
uv run ruff check . ; uv run ruff format .
uv run python -m benchmark.runner --config v1 --split dev --runs 3   # paid: model from .env
uv run python -m benchmark.report benchmark/results/<file>.json     # score, all thresholds
uv run python -m benchmark.report <file> --as-config v1_1  # score as a threshold-only variant
```

Runner flags: `--model` / `--provider` (override `.env` for one run), `--max-cost 2.0` (stop
at this USD total), `--workers 4`, `--case <id>`
(smoke-test a few cases; saved as split `dev-subset`), `--allow-dirty`, and `--i-mean-test`
(required for `--split test`, which runs only when the user asks).

CI (`.github/workflows/ci.yml`) runs `uv sync --locked`, ruff check, ruff format --check and
pytest on every push to `main` and every PR. Commit `uv.lock` whenever dependencies change.

The project is not an installed package (`[tool.uv] package = false`): the top-level folders
run in place, and pytest adds the repo root to the path. Add a runtime dependency in the
step that first needs it, not before.

Planned, not built yet:

```
uv run uvicorn service.app:app --reload                              # Step 6
uv run python -m mcp_server                                          # Step 7
```

## Code conventions

These are the coding standards the `code-review` skill checks against.

- Type hints everywhere; Pydantic models for anything that crosses a module interface.
- **Line numbers are always new-file line numbers** (GitHub review API: `line` + `side:
  RIGHT`). The diff parser has tests for added, removed, renamed and multi-hunk files.
- Unit tests never call an LLM: they use the fake adapter or recorded responses. Real calls
  happen only in benchmark runs and in tests marked `integration`.
- Record token usage, cost, latency and upstream provider on every LLM call (`CallRecord`).
  Langfuse tracing is optional and comes in Step 6 at the earliest; traces contain diffs,
  so Langfuse would be one more place the reviewed code goes.
- Every PR comment ends with a short "automated check, not legal advice" footer.

## Skills in `.claude/skills/`

| Skill | Use it for |
|---|---|
| `grilling` (`/grill-me`) | Stress-test a step's design before building it. Do this at the start of Steps 2, 5, 6 and 7. |
| `to-tickets` | Split a step into vertical-slice tickets once its design is agreed. |
| `tdd` | Build `reviewer/`, the diff parser and the scorer test-first. Agree the seams with the user before writing any test. |
| `codebase-design` | Vocabulary for module shape: module, interface, depth, seam, adapter. Use those terms. |
| `domain-modeling` | Keep `GLOSSARY.md` and `docs/adr/` current. Terms to settle early: Case, Label, Finding, Category, Config, Run, Split, False alarm. |
| `code-review` | Review a branch: Standards (the conventions above) and Spec (the ticket it came from). |
| `improve-codebase-architecture` | Not before Step 6: it needs commit history to find hot spots. |

**Issue tracker:** local markdown files, one ticket per file, under
`.scratch/<step-slug>/issues/<NN>-<slug>.md`. Triage label: `ready-for-agent`. Specs go in
`.scratch/<step-slug>/spec.md`. Switch to GitHub Issues once the repo has a remote, and
update this section when you do.

**ADR candidates** already decided: OpenRouter for all LLM calls; a file-based benchmark
instead of a database; a held-out test split. Write them up in `docs/adr/` when the folder
is first needed.

## Git

- Remote: `origin` = https://github.com/Lin3lis-Pel/PR_.git. Pushing needs the user's OK.
- No AI attribution anywhere in git: no `Co-Authored-By` trailers, no "Generated with" lines
  in PR descriptions.
- Commit messages and PR text are plain and factual: what changed and why. No filler.
- Stage files by name after checking `git status`; never `git add -A` or `git add .`.

## Secrets and personal data

- Keys come from the environment (`OPENROUTER_API_KEY`, `LANGFUSE_*`, `GITHUB_TOKEN`),
  loaded from `.env` by `reviewer/config.py`.
  `.env` is gitignored. Never commit, log or print a key.
- **No real personal data anywhere:** not in cases, fixtures, logs or traces. Use Faker,
  `example.com` addresses and reserved test numbers. This repo must pass its own reviewer.
- Adapted open-source commits keep their source URL and license in `label.yaml`.

## Ask the user first

- Adding or removing a category, changing the matching rule or metrics.
- Moving a case between `dev` and `test`, or running the `test` split.
- Adding a model or provider to the published table (it costs money to run 3× per split).
- Adding a database. It is planned only as an optional feedback store (PR comment
  reactions → live precision numbers), never for benchmark cases or results.
- Changing what the learning workflow may do on its own (it only opens PRs).
- Anything that deploys, pushes or posts to GitHub.
