# GDPR PR Reviewer

An automated reviewer that reports personal-data risks in pull requests, and the benchmark
that measures how often it is right.

## Benchmark

**Case**:
One labelled pull request in the benchmark: a diff, its label, and optional repo context.
_Avoid_: test case, sample, example

**Label**:
The human-checked answer for a case: its kind and the issues it contains.
_Avoid_: annotation, ground truth file

**Issue**:
A real risk a case contains, at a file, side and line range, in one category. A required
issue must be found; an optional issue may be found without penalty.
_Avoid_: violation, bug, problem

**Kind**:
Whether a case is positive (has a required issue), negative (ordinary change, no personal
data concern) or tricky (looks risky but is not, such as fake data or a keyed hash).

**Clean PR**:
A negative or tricky case. False alarms are measured on these.
_Avoid_: safe PR, negative case (when tricky cases are included)

**Split**:
Whether a case belongs to dev (used while improving the reviewer) or test (held out, run
only for a version's final numbers).
_Avoid_: fold, partition

**Config**:
A frozen, named version of the reviewer's prompt and pipeline (v1, v2, v3-cheap). It does
not include the model, which comes from `.env`.
_Avoid_: variant, experiment

**Row**:
One line of the results table: a config x model on a split, scored from one results file.
_Avoid_: entry, result

**Run**:
One pass of a config over a set of cases, producing raw findings.
_Avoid_: trial, execution

## Reviewer output

**Finding**:
One risk the reviewer reports at one line, with a category and a confidence.
_Avoid_: comment (that is how a finding is shown on GitHub), alert, flag

**Category**:
One of the four risk types in scope: PII storage, PII logging, third-party transfer,
erasure regression.
_Avoid_: type, rule

**Side**:
Which version of a file a line number refers to: new (after the change) or old (before it).
_Avoid_: LEFT/RIGHT (GitHub's names, used only at the GitHub edge)

**Shown finding**:
A finding at or above its config's threshold. Only shown findings reach a pull request;
results files keep every finding.
_Avoid_: filtered finding, visible finding

**Posted finding**:
A shown finding sent to a pull request as an inline comment. A shown finding the diff does
not show, or one over the comment limit, is listed in the review body instead.
_Avoid_: comment (that is the GitHub object, not the finding), published finding

**Fingerprint**:
What makes two findings the same risk across pushes: the file, the category and the
content of the line, not its number. A finding whose fingerprint is already on the pull
request is not posted again.
_Avoid_: finding id, hash (that is its form, not its meaning)

## Running the reviewer

**Service**:
The hosted reviewer: it takes a diff and returns shown findings, and never talks to GitHub.
_Avoid_: API, server, backend

**Action**:
The GitHub Action that reviews a pull request: it sends the diff to the service and posts
the findings as one review.
_Avoid_: bot, workflow (the repo's file that runs the Action), integration

## Scoring

**Hit**:
A finding that matches a required issue.
_Avoid_: true positive (in prose; fine in metric names)

**Miss**:
A required issue no finding matched.

**False alarm**:
A finding that matches no unclaimed issue, including a duplicate of a hit and a finding
with the wrong category.
_Avoid_: false positive (in prose), noise

**Invalid location**:
A false alarm whose file or line is not shown in the diff, so it could not be posted as an
inline comment.
