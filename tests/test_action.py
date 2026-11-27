"""The Action's script, tested through run() with a fake GitHub and a fake service.

No test calls GitHub, the service or an LLM.
"""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from action.review_pr import (
    CallError,
    Inputs,
    PullRequest,
    ignored_paths_from_config,
    inputs_from_env,
    pull_request_from_event,
    run,
)

DIFF = """\
diff --git a/app/views.py b/app/views.py
index 1111111..2222222 100644
--- a/app/views.py
+++ b/app/views.py
@@ -1,2 +1,3 @@
 def signup(user):
+    logger.info("signup %s", user.email)
     return user
"""
PR = PullRequest(number=7, base_sha="base000", head_sha="abc123")
INPUTS = Inputs(max_comments=10)
FOOTER = "Automated check, not legal advice."


@dataclass
class FakeGitHub:
    diff: str = DIFF
    files: dict[str, str] = field(default_factory=dict)  # path at the PR head -> content
    reviews: list[dict[str, Any]] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)  # texts on the PR before this fake's reviews
    fail_on: str | None = None  # name of the call that raises CallError
    calls: list[str] = field(default_factory=list)

    def get_diff(self, pr: PullRequest) -> str:
        self._called("get_diff")
        assert pr.number == PR.number
        return self.diff

    def get_file(self, path: str, ref: str) -> str | None:
        self._called("get_file")
        assert ref == PR.head_sha
        return self.files.get(path)

    def create_review(self, number: int, review: dict[str, Any]) -> None:
        self._called("create_review")
        assert number == PR.number
        self.reviews.append(review)

    def list_posted_texts(self, number: int) -> list[str]:
        """Bodies of the PR's reviews and review comments: seeded ones, then this fake's own,
        so a second run on the same fake sees what the first one posted."""
        self._called("list_posted_texts")
        assert number == PR.number
        own = [r["body"] for r in self.reviews]
        own += [c["body"] for r in self.reviews for c in r["comments"]]
        return self.posted + own

    def _called(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            raise CallError(f"{name}: HTTP 500 from GitHub")


@dataclass
class FakeReviewer:
    findings: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = 0.00019
    diffs: list[str] = field(default_factory=list)
    error: Exception | None = None

    def review(self, diff: str) -> dict[str, Any]:
        self.diffs.append(diff)
        if self.error is not None:
            raise self.error
        return {
            "findings": self.findings,
            "config": "v2",
            "model": "xiaomi/mimo-v2.6-flash",
            "provider": "Xiaomi",
            "cost_usd": self.cost_usd,
            "latency_s": 3.2,
        }


def finding(**overrides: Any) -> dict[str, Any]:
    base = {
        "file": "app/views.py",
        "line": 2,
        "side": "new",
        "category": "PII_LOGGING",
        "confidence": 0.9,
        "article": "Art. 5(1)(c)",
        "explanation": "Logs the user's email.",
        "suggested_fix": "Log user.id instead.",
        "postable": True,
        "fingerprint": "f00d",
    }
    return {**base, **overrides}


def test_a_finding_becomes_one_comment_review_at_the_reviewed_commit() -> None:
    github = FakeGitHub()
    reviewer = FakeReviewer([finding()])

    run(PR, INPUTS, github, reviewer)

    assert reviewer.diffs == [DIFF]
    [review] = github.reviews
    assert review["event"] == "COMMENT"
    assert review["commit_id"] == "abc123"
    [comment] = review["comments"]
    assert comment["path"] == "app/views.py"
    assert comment["line"] == 2
    assert comment["side"] == "RIGHT"
    assert FOOTER in review["body"]


def test_an_inline_comment_explains_the_finding_and_carries_its_fingerprint() -> None:
    github = FakeGitHub()

    run(PR, INPUTS, github, FakeReviewer([finding(confidence=0.85)]))

    [comment] = github.reviews[0]["comments"]
    text = comment["body"]
    assert "PII_LOGGING" in text
    assert "Art. 5(1)(c)" in text
    assert "Logs the user's email." in text
    assert "Log user.id instead." in text
    assert "0.85" in text
    assert FOOTER in text
    assert "<!-- gdpr-reviewer:f00d -->" in text


def test_a_finding_on_a_removed_line_goes_on_the_left_side() -> None:
    github = FakeGitHub()

    run(PR, INPUTS, github, FakeReviewer([finding(side="old", line=21)]))

    [comment] = github.reviews[0]["comments"]
    assert (comment["line"], comment["side"]) == (21, "LEFT")


def test_the_script_imports_only_the_standard_library() -> None:
    # It runs on the runner's Python with nothing installed.
    source = Path("action/review_pr.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def _inline_fingerprints(review: dict[str, Any]) -> list[str]:
    return [c["body"].rsplit("gdpr-reviewer:", 1)[1].split(" ")[0] for c in review["comments"]]


def test_inline_comments_go_highest_confidence_first() -> None:
    github = FakeGitHub()
    findings = [
        finding(fingerprint="low", confidence=0.6, line=1),
        finding(fingerprint="high", confidence=0.95, line=2),
        finding(fingerprint="mid", confidence=0.8, line=3),
    ]

    run(PR, INPUTS, github, FakeReviewer(findings))

    assert _inline_fingerprints(github.reviews[0]) == ["high", "mid", "low"]


def test_findings_over_the_cap_are_listed_in_the_body_instead() -> None:
    github = FakeGitHub()
    findings = [
        finding(fingerprint="a", confidence=0.9, line=1),
        finding(fingerprint="b", confidence=0.8, line=2, explanation="Second risk."),
        finding(fingerprint="c", confidence=0.7, line=3, explanation="Third risk."),
    ]

    run(PR, Inputs(max_comments=1), github, FakeReviewer(findings))

    [review] = github.reviews
    assert _inline_fingerprints(review) == ["a"]
    body = review["body"]
    assert "`app/views.py:2`" in body
    assert "Second risk." in body
    assert "`app/views.py:3`" in body
    assert "Third risk." in body
    assert body.index("Second risk.") < body.index("Third risk.")
    assert "comment limit" in body


def test_a_finding_the_diff_does_not_show_goes_in_the_body() -> None:
    github = FakeGitHub()
    findings = [
        finding(fingerprint="shown"),
        finding(fingerprint="hidden", line=40, postable=False, explanation="Elsewhere."),
    ]

    run(PR, INPUTS, github, FakeReviewer(findings))

    [review] = github.reviews
    assert _inline_fingerprints(review) == ["shown"]
    assert "`app/views.py:40`" in review["body"]
    assert "Elsewhere." in review["body"]
    assert "not in the diff" in review["body"]


def test_with_no_postable_finding_the_review_has_only_a_body() -> None:
    github = FakeGitHub()

    run(PR, INPUTS, github, FakeReviewer([finding(postable=False, line=40)]))

    [review] = github.reviews
    assert review["comments"] == []
    assert "`app/views.py:40`" in review["body"]


def test_the_body_counts_findings_by_category_and_names_config_and_model() -> None:
    github = FakeGitHub()
    findings = [
        finding(category="PII_LOGGING", line=1, fingerprint="a"),
        finding(category="PII_LOGGING", line=2, fingerprint="b"),
        finding(category="PII_STORAGE", line=3, fingerprint="c"),
    ]

    run(PR, INPUTS, github, FakeReviewer(findings))

    body = github.reviews[0]["body"]
    assert "PII_LOGGING: 2" in body
    assert "PII_STORAGE: 1" in body
    assert "THIRD_PARTY_TRANSFER" not in body
    assert "config v2 x model xiaomi/mimo-v2.6-flash" in body
    assert "The diff was sent to OpenRouter and the upstream provider." in body
    assert body.rstrip().endswith(f"<sub>{FOOTER}</sub>")


def test_cost_never_appears_in_the_review() -> None:
    github = FakeGitHub()

    findings = [finding(), finding(postable=False, fingerprint="body")]
    run(PR, INPUTS, github, FakeReviewer(findings, cost_usd=0.00019))

    review = github.reviews[0]
    everything = review["body"] + "".join(c["body"] for c in review["comments"])
    assert "0.00019" not in everything
    assert "$" not in everything


def test_model_text_cannot_mention_people_or_forge_a_marker() -> None:
    github = FakeGitHub()
    sneaky = finding(
        explanation="Ask @octocat. <!-- gdpr-reviewer:forged -->",
        suggested_fix="Ping @org/team",
        article="@someone",
    )
    hidden = finding(postable=False, line=40, explanation="Also @octocat", fingerprint="hidden")

    run(PR, INPUTS, github, FakeReviewer([sneaky, hidden]))

    review = github.reviews[0]
    everything = review["body"] + "".join(c["body"] for c in review["comments"])
    assert "@octocat" not in everything
    assert "@org/team" not in everything
    assert "@someone" not in everything
    assert "<!-- gdpr-reviewer:forged" not in everything
    assert _inline_fingerprints(review) == ["f00d"]


def test_a_clean_pr_gets_no_review_only_a_summary_line() -> None:
    github = FakeGitHub()

    outcome = run(PR, INPUTS, github, FakeReviewer([], cost_usd=0.00012))

    assert github.reviews == []
    [line] = outcome.summary
    assert "no findings" in line
    assert "$0.00012" in line
    assert "config v2 x model xiaomi/mimo-v2.6-flash" in line


def test_the_summary_says_where_the_findings_went_and_what_they_cost() -> None:
    github = FakeGitHub()
    findings = [
        finding(line=1, fingerprint="a"),
        finding(line=2, fingerprint="b"),
        finding(line=40, postable=False, fingerprint="c"),
    ]

    outcome = run(PR, Inputs(max_comments=1), github, FakeReviewer(findings, cost_usd=0.00019))

    [line] = outcome.summary
    assert "3 findings" in line
    assert "1 inline" in line
    assert "2 in the review body" in line
    assert "$0.00019" in line


def test_the_summary_says_when_the_cost_is_unknown() -> None:
    outcome = run(PR, INPUTS, FakeGitHub(), FakeReviewer([], cost_usd=None))

    assert "cost unknown" in outcome.summary[0]


def test_findings_never_fail_the_job() -> None:
    outcome = run(PR, INPUTS, FakeGitHub(), FakeReviewer([finding(confidence=1.0)]))

    assert outcome.failed is False


# --- reading the workflow's environment -----------------------------------------------


def test_an_event_without_a_pull_request_is_refused() -> None:
    with pytest.raises(ValueError, match="pull_request"):
        pull_request_from_event({"ref": "refs/heads/main"})


def test_inputs_default_to_ten_comments() -> None:
    assert inputs_from_env({}) == Inputs(max_comments=10)
    assert inputs_from_env({"INPUT_MAX_COMMENTS": ""}) == Inputs(max_comments=10)
    assert inputs_from_env({"INPUT_MAX_COMMENTS": "3"}) == Inputs(max_comments=3)


@pytest.mark.parametrize("value", ["many", "-1"])
def test_a_bad_comment_limit_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="max-comments"):
        inputs_from_env({"INPUT_MAX_COMMENTS": value})


# --- skips: nothing is sent to the service ----------------------------------------------


def _file(path: str, body: str = "+x = 1\n") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}"


@pytest.mark.parametrize(
    ("pr", "reason"),
    [
        (PullRequest(7, "base000", "abc123", draft=True), "draft"),
        (PullRequest(7, "base000", "abc123", fork=True), "fork"),
    ],
)
def test_drafts_and_forks_are_skipped_before_anything_is_fetched(
    pr: PullRequest, reason: str
) -> None:
    github = FakeGitHub()
    reviewer = FakeReviewer([finding()])

    outcome = run(pr, INPUTS, github, reviewer)

    assert github.calls == []
    assert reviewer.diffs == []
    [line] = outcome.summary
    assert "skipped" in line
    assert reason in line
    assert outcome.warnings == []
    assert outcome.failed is False


def test_a_diff_over_the_size_limit_is_skipped() -> None:
    big = _file("app/big.py", "+x = 1\n" * 40_000)  # about 280 KB
    reviewer = FakeReviewer([finding()])
    github = FakeGitHub(diff=big)

    outcome = run(PR, INPUTS, github, reviewer)

    assert reviewer.diffs == []
    assert github.reviews == []
    assert "skipped" in outcome.summary[0]
    assert "limit" in outcome.summary[0]


def test_the_size_limit_applies_after_ignored_files_are_removed() -> None:
    big = _file("vendor/big.py", "+x = 1\n" * 40_000)
    github = FakeGitHub(
        diff=big + DIFF, files={".gdpr-reviewer.yml": "ignored_paths:\n  - vendor/**\n"}
    )
    reviewer = FakeReviewer([])

    run(PR, INPUTS, github, reviewer)

    assert reviewer.diffs == [DIFF]


def test_a_diff_with_only_ignored_files_is_skipped() -> None:
    github = FakeGitHub(files={".gdpr-reviewer.yml": "ignored_paths:\n  - app/**\n"})
    reviewer = FakeReviewer([finding()])

    outcome = run(PR, INPUTS, github, reviewer)

    assert reviewer.diffs == []
    assert "skipped" in outcome.summary[0]
    assert "ignored" in outcome.summary[0]


def test_a_diff_with_only_renames_or_binary_files_is_skipped() -> None:
    renames_and_binaries = (
        "diff --git a/app/old.py b/app/new.py\n"
        "similarity index 100%\n"
        "rename from app/old.py\n"
        "rename to app/new.py\n"
        "diff --git a/logo.png b/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    reviewer = FakeReviewer([finding()])

    outcome = run(PR, INPUTS, FakeGitHub(diff=renames_and_binaries), reviewer)

    assert reviewer.diffs == []
    assert "skipped" in outcome.summary[0]
    assert "no changed lines" in outcome.summary[0]


# --- ignored_paths ----------------------------------------------------------------------

CONFIG = ".gdpr-reviewer.yml"


def _sent(diff: str, config: str | None, inputs: Inputs = INPUTS) -> str | None:
    """The diff the service received, or None when nothing was sent."""
    files = {} if config is None else {inputs.config_file: config}
    reviewer = FakeReviewer([])
    run(PR, inputs, FakeGitHub(diff=diff, files=files), reviewer)
    return reviewer.diffs[0] if reviewer.diffs else None


def test_ignored_files_are_removed_and_the_rest_is_sent_unchanged() -> None:
    kept = _file("app/models.py")
    diff = _file("benchmark/cases/pos-1/pr.diff") + kept + _file("tests/fixtures/x.py") + DIFF

    sent = _sent(diff, "ignored_paths:\n  - benchmark/cases/**\n  - tests/fixtures/**\n")

    assert sent == kept + DIFF


@pytest.mark.parametrize(
    ("pattern", "path", "ignored"),
    [
        ("benchmark/cases/**", "benchmark/cases/pos-1/pr.diff", True),
        ("benchmark/cases/**", "benchmark/cases.py", False),
        ("benchmark/cases/", "benchmark/cases/a/b.py", True),  # a folder means all of it
        ("**/fixtures/**", "fixtures/a.py", True),  # ** may match no folder at all
        ("**/fixtures/**", "tests/unit/fixtures/a.py", True),
        ("*.lock", "uv.lock", True),
        ("*.lock", "sub/uv.lock", False),  # * stays inside one folder
        ("**/*.lock", "sub/uv.lock", True),
        ("app/?.py", "app/a.py", True),
        ("app/?.py", "app/ab.py", False),
        ("docs/a+b.md", "docs/a+b.md", True),  # other characters are literal
        ("./docs/**", "docs/x.md", True),
    ],
)
def test_ignored_paths_globs(pattern: str, path: str, ignored: bool) -> None:
    sent = _sent(_file(path) + DIFF, f'ignored_paths:\n  - "{pattern}"\n')

    assert sent == (DIFF if ignored else _file(path) + DIFF)


RENAMED = (
    "diff --git a/vendor/old.py b/app/new.py\n"
    "similarity index 90%\n"
    "rename from vendor/old.py\n"
    "rename to app/new.py\n"
    "--- a/vendor/old.py\n"
    "+++ b/app/new.py\n"
    "@@ -1 +1 @@\n"
    "-x = 0\n"
    "+x = 1\n"
)
DELETED = (
    "diff --git a/vendor/gone.py b/vendor/gone.py\n"
    "deleted file mode 100644\n"
    "--- a/vendor/gone.py\n"
    "+++ /dev/null\n"
    "@@ -1 +0,0 @@\n"
    "-anonymise(user)\n"
)


@pytest.mark.parametrize("pattern", ["vendor/**", "app/new.py"])
def test_a_rename_is_dropped_when_either_path_matches(pattern: str) -> None:
    assert _sent(RENAMED + DIFF, f"ignored_paths: [{pattern}]\n") == DIFF


def test_a_deleted_file_is_matched_on_its_old_path() -> None:
    assert _sent(DELETED + DIFF, "ignored_paths: [vendor/**]\n") == DIFF


def test_without_a_config_file_nothing_is_ignored() -> None:
    assert _sent(DELETED + DIFF, None) == DELETED + DIFF


def test_the_config_file_is_read_from_the_input_path_at_the_pr_head() -> None:
    inputs = Inputs(config_file="config/reviewer.yml")
    github = FakeGitHub(
        diff=DELETED + DIFF, files={"config/reviewer.yml": "ignored_paths: [vendor/**]\n"}
    )
    reviewer = FakeReviewer([])

    run(PR, inputs, github, reviewer)

    assert reviewer.diffs == [DIFF]


def test_the_config_reader_skips_other_keys_and_comments() -> None:
    config = """\
# data map for this repo
personal_fields:
  customer:
    - email
    - iban
ignored_paths:   # never sent to the service
  - vendor/**    # third-party code
  - 'docs/**'

  - "*.lock"
approved_processors: [stripe]
"""
    diff = DELETED + _file("docs/a.md") + _file("uv.lock") + DIFF

    assert _sent(diff, config) == DIFF


@pytest.mark.parametrize(
    "config",
    [
        "ignored_paths: vendor/**\n",  # a string, not a list
        "ignored_paths:\n  vendor: yes\n",  # a mapping
        "ignored_paths: [vendor/**\n",  # unclosed flow list
        "ignored_paths:\n  - [nested]\n",
    ],
)
def test_an_ignored_paths_value_the_reader_cannot_parse_skips_the_review(config: str) -> None:
    # Fail closed: sending files the repo meant to exclude is worse than not reviewing.
    reviewer = FakeReviewer([finding()])

    outcome = run(PR, INPUTS, FakeGitHub(diff=DELETED + DIFF, files={CONFIG: config}), reviewer)

    assert reviewer.diffs == []
    [warning] = outcome.warnings
    assert CONFIG in warning
    assert "ignored_paths" in warning


def test_an_empty_ignored_paths_ignores_nothing() -> None:
    assert _sent(DELETED + DIFF, "ignored_paths:\nother: 1\n") == DELETED + DIFF
    assert _sent(DELETED + DIFF, "ignored_paths: []\n") == DELETED + DIFF


# --- errors become warnings -------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        CallError("POST https://reviewer.example.com/review: HTTP 502 (no usable answer)"),
        TimeoutError("timed out"),
        KeyError("findings"),
    ],
)
def test_a_service_error_is_a_warning_and_the_job_passes(error: Exception) -> None:
    github = FakeGitHub()

    outcome = run(PR, INPUTS, github, FakeReviewer(error=error))

    assert github.reviews == []
    [warning] = outcome.warnings
    assert "could not review" in warning
    assert outcome.failed is False
    assert "could not review" in outcome.summary[0]


@pytest.mark.parametrize("call", ["get_diff", "get_file", "create_review"])
def test_a_github_error_is_a_warning_and_the_job_passes(call: str) -> None:
    github = FakeGitHub(fail_on=call)

    outcome = run(PR, INPUTS, github, FakeReviewer([finding()]))

    [warning] = outcome.warnings
    assert "HTTP 500" in warning
    assert outcome.failed is False


def test_fail_on_error_makes_an_error_fail_the_job() -> None:
    inputs = Inputs(fail_on_error=True)

    failed = run(PR, inputs, FakeGitHub(), FakeReviewer(error=TimeoutError("timed out")))
    clean = run(PR, inputs, FakeGitHub(), FakeReviewer([]))
    with_findings = run(PR, inputs, FakeGitHub(), FakeReviewer([finding()]))

    assert failed.failed is True
    assert clean.failed is False
    assert with_findings.failed is False


def test_fail_on_error_also_covers_an_unreadable_config() -> None:
    github = FakeGitHub(files={CONFIG: "ignored_paths: vendor/**\n"})

    outcome = run(PR, Inputs(fail_on_error=True), github, FakeReviewer([]))

    assert outcome.failed is True


def test_skips_never_fail_the_job_even_with_fail_on_error() -> None:
    draft = PullRequest(7, "base000", "abc123", draft=True)

    outcome = run(draft, Inputs(fail_on_error=True), FakeGitHub(), FakeReviewer([]))

    assert outcome.failed is False
    assert outcome.warnings == []


# --- more of the workflow's environment ---------------------------------------------------


def _event(**pr: Any) -> dict[str, Any]:
    base = {
        "number": 7,
        "draft": False,
        "base": {"sha": "base000", "repo": {"full_name": "owner/repo"}},
        "head": {"sha": "abc123", "repo": {"full_name": "owner/repo"}},
    }
    return {"action": "synchronize", "pull_request": {**base, **pr}}


def test_the_event_says_whether_the_pr_is_a_draft_or_from_a_fork() -> None:
    fork_head = {"sha": "abc123", "repo": {"full_name": "someone/repo"}}
    deleted_fork = {"sha": "abc123", "repo": None}

    assert pull_request_from_event(_event()) == PR
    assert pull_request_from_event(_event(draft=True)).draft is True
    assert pull_request_from_event(_event(head=fork_head)).fork is True
    assert pull_request_from_event(_event(head=deleted_fork)).fork is True


def test_fail_on_error_and_config_file_inputs() -> None:
    assert inputs_from_env({}) == Inputs(fail_on_error=False, config_file=CONFIG)
    assert inputs_from_env({"INPUT_FAIL_ON_ERROR": "TRUE"}).fail_on_error is True
    assert inputs_from_env({"INPUT_FAIL_ON_ERROR": "false"}).fail_on_error is False
    assert inputs_from_env({"INPUT_CONFIG_FILE": "a/b.yml"}).config_file == "a/b.yml"
    with pytest.raises(ValueError, match="fail-on-error"):
        inputs_from_env({"INPUT_FAIL_ON_ERROR": "yes please"})


def test_a_path_git_quotes_is_matched_on_its_real_name() -> None:
    quoted = (
        'diff --git "a/docs/caf\\303\\251.md" "b/docs/caf\\303\\251.md"\n'
        '--- "a/docs/caf\\303\\251.md"\n'
        '+++ "b/docs/caf\\303\\251.md"\n'
        "@@ -0,0 +1 @@\n"
        "+x\n"
    )

    assert _sent(quoted + DIFF, "ignored_paths: [docs/café.md]\n") == DIFF


def test_a_list_written_without_indent_is_read() -> None:
    assert _sent(DELETED + DIFF, "ignored_paths:\n- vendor/**\nother: 1\n") == DIFF


def test_a_removed_line_that_looks_like_a_file_header_is_not_a_path() -> None:
    # '--- a/vendor/x' inside a hunk is removed content, not this file's old path.
    tricky = (
        "diff --git a/app/notes.md b/app/notes.md\n"
        "--- a/app/notes.md\n"
        "+++ b/app/notes.md\n"
        "@@ -1,2 +1 @@\n"
        "--- a/vendor/x\n"
        " keep\n"
    )

    assert _sent(tricky + DIFF, "ignored_paths: [vendor/**]\n") == tricky + DIFF


# --- no repeat comments -----------------------------------------------------------------


def test_a_second_run_with_the_same_findings_posts_nothing() -> None:
    github = FakeGitHub()
    findings = [finding(fingerprint="a"), finding(fingerprint="b", postable=False, line=40)]

    run(PR, INPUTS, github, FakeReviewer(findings))
    second = run(PR, INPUTS, github, FakeReviewer(findings))

    assert len(github.reviews) == 1  # only the first run's
    [line] = second.summary
    assert "no new findings" in line
    assert "2 already posted" in line


def test_a_finding_whose_line_moved_but_not_its_content_is_not_posted_again() -> None:
    # The service gives the same fingerprint when only the line number changed.
    github = FakeGitHub()
    run(PR, INPUTS, github, FakeReviewer([finding(fingerprint="same", line=2)]))

    run(PR, INPUTS, github, FakeReviewer([finding(fingerprint="same", line=9)]))

    assert len(github.reviews) == 1


def test_only_the_new_finding_is_posted() -> None:
    github = FakeGitHub()
    run(PR, INPUTS, github, FakeReviewer([finding(fingerprint="old")]))

    outcome = run(
        PR,
        INPUTS,
        github,
        FakeReviewer([finding(fingerprint="old"), finding(fingerprint="new", line=1)]),
    )

    second = github.reviews[1]
    assert _inline_fingerprints(second) == ["new"]
    assert "1 possible personal-data risk:" in second["body"]
    assert "1 already posted" in outcome.summary[0]


def test_the_cap_and_ordering_apply_to_new_findings_only() -> None:
    github = FakeGitHub(
        posted=["<!-- gdpr-reviewer:top -->", "earlier body\n<!-- gdpr-reviewer:second -->"]
    )
    findings = [
        finding(fingerprint="top", confidence=0.99, line=1),
        finding(fingerprint="second", confidence=0.98, line=2),
        finding(fingerprint="low", confidence=0.5, line=3, explanation="Low one."),
        finding(fingerprint="mid", confidence=0.7, line=4),
    ]

    run(PR, Inputs(max_comments=1), github, FakeReviewer(findings))

    [review] = github.reviews
    assert _inline_fingerprints(review) == ["mid"]
    assert "Low one." in review["body"]
    assert "comment limit" in review["body"]


def test_a_finding_listed_in_an_earlier_review_body_is_not_listed_again() -> None:
    github = FakeGitHub()
    hidden = finding(fingerprint="hidden", postable=False, line=40, explanation="Elsewhere.")
    run(PR, INPUTS, github, FakeReviewer([hidden]))
    assert "<!-- gdpr-reviewer:hidden -->" in github.reviews[0]["body"]

    run(PR, INPUTS, github, FakeReviewer([hidden, finding(fingerprint="fresh")]))

    assert "Elsewhere." not in github.reviews[1]["body"]


def test_only_the_reviewers_own_marker_form_counts() -> None:
    github = FakeGitHub(
        posted=[
            "<!-- other-tool:f00d -->",  # another bot's marker
            "gdpr-reviewer:f00d",  # not inside an HTML comment
            "&lt;!-- gdpr-reviewer:f00d -->",  # model text, made inert by an earlier run
            "`<!-- gdpr-reviewer:f00d` without its end",
        ]
    )

    run(PR, INPUTS, github, FakeReviewer([finding(fingerprint="f00d")]))

    [review] = github.reviews
    assert _inline_fingerprints(review) == ["f00d"]


def test_findings_that_share_a_fingerprint_are_posted_once() -> None:
    # Two identical lines in one file give the same fingerprint: one comment is enough.
    github = FakeGitHub()
    twins = [
        finding(fingerprint="twin", line=2, confidence=0.9),
        finding(fingerprint="twin", line=5, confidence=0.8),
    ]

    run(PR, INPUTS, github, FakeReviewer(twins))

    [review] = github.reviews
    [comment] = review["comments"]
    assert comment["line"] == 2


def test_the_posted_texts_are_read_only_when_there_is_something_to_post() -> None:
    github = FakeGitHub()

    run(PR, INPUTS, github, FakeReviewer([]))

    assert "list_posted_texts" not in github.calls


def test_an_error_reading_the_posted_texts_is_a_warning() -> None:
    github = FakeGitHub(fail_on="list_posted_texts")

    outcome = run(PR, INPUTS, github, FakeReviewer([finding()]))

    assert github.reviews == []
    [warning] = outcome.warnings
    assert "HTTP 500" in warning


def test_this_repos_own_config_is_read_by_the_action() -> None:
    config = Path(".gdpr-reviewer.yml").read_text(encoding="utf-8")

    assert ignored_paths_from_config(config) == [
        "benchmark/cases/**",
        "benchmark/results/**",
        "tests/fixtures/**",
    ]
