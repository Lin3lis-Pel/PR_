"""The Action's script, tested through run() with a fake GitHub and a fake service.

No test calls GitHub, the service or an LLM.
"""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from action.review_pr import Inputs, PullRequest, inputs_from_env, pull_request_from_event, run

DIFF = "diff --git a/app/views.py b/app/views.py\n(the diff the service reviews)\n"
PR = PullRequest(number=7, base_sha="base000", head_sha="abc123")
INPUTS = Inputs(max_comments=10)
FOOTER = "Automated check, not legal advice."


@dataclass
class FakeGitHub:
    diff: str = DIFF
    reviews: list[dict[str, Any]] = field(default_factory=list)

    def get_diff(self, pr: PullRequest) -> str:
        assert pr == PR
        return self.diff

    def create_review(self, number: int, review: dict[str, Any]) -> None:
        assert number == PR.number
        self.reviews.append(review)


@dataclass
class FakeReviewer:
    findings: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = 0.00019
    diffs: list[str] = field(default_factory=list)

    def review(self, diff: str) -> dict[str, Any]:
        self.diffs.append(diff)
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
        finding(category="PII_LOGGING", line=1),
        finding(category="PII_LOGGING", line=2),
        finding(category="PII_STORAGE", line=3),
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

    run(PR, INPUTS, github, FakeReviewer([finding(), finding(postable=False)], cost_usd=0.00019))

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
    hidden = finding(postable=False, line=40, explanation="Also @octocat")

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
    findings = [finding(line=1), finding(line=2), finding(line=40, postable=False)]

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


def test_the_pull_request_comes_from_the_event_payload() -> None:
    event = {
        "action": "opened",
        "pull_request": {"number": 7, "base": {"sha": "base000"}, "head": {"sha": "abc123"}},
    }

    assert pull_request_from_event(event) == PR


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
