"""Review one pull request: fetch its diff, send it to the service, post one review.

Standard library only: the Action runs on the runner's own Python, with nothing installed.
All GitHub I/O happens here; the service never sees a GitHub token (ADR 0005).

    python3 review_pr.py   # reads the INPUT_* and GITHUB_* variables action.yml sets
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

FOOTER = "Automated check, not legal advice."
SERVICE_TIMEOUT_S = 120
GITHUB_TIMEOUT_S = 30


@dataclass(frozen=True)
class PullRequest:
    number: int
    base_sha: str
    head_sha: str  # the diff is taken up to this commit, and the review is posted against it


@dataclass(frozen=True)
class Inputs:
    max_comments: int = 10


@dataclass
class Outcome:
    summary: list[str]  # lines for the job summary
    failed: bool = False  # findings never fail the job


class GitHubClient(Protocol):
    def get_diff(self, pr: PullRequest) -> str: ...

    def create_review(self, number: int, review: dict[str, Any]) -> None: ...


class ReviewerClient(Protocol):
    def review(self, diff: str) -> dict[str, Any]: ...


def run(pr: PullRequest, inputs: Inputs, github: GitHubClient, reviewer: ReviewerClient) -> Outcome:
    diff = github.get_diff(pr)
    reply = reviewer.review(diff)
    findings = sorted(reply["findings"], key=lambda f: -f["confidence"])  # stable on ties

    inline: list[dict[str, Any]] = []
    elsewhere: list[tuple[dict[str, Any], str]] = []  # (finding, why it is not inline)
    for finding in findings:
        if not finding["postable"]:
            elsewhere.append((finding, "line not in the diff"))
        elif len(inline) >= inputs.max_comments:
            elsewhere.append((finding, "over the comment limit"))
        else:
            inline.append(finding)

    if findings:
        github.create_review(
            pr.number,
            {
                "commit_id": pr.head_sha,
                "event": "COMMENT",
                "body": _review_body(findings, elsewhere, reply),
                "comments": [_inline(f) for f in inline],
            },
        )
    return Outcome(summary=[_summary_line(findings, len(inline), reply)])


def _summary_line(findings: list[dict[str, Any]], inline: int, reply: dict[str, Any]) -> str:
    cost = reply.get("cost_usd")
    cost_text = "cost unknown" if cost is None else f"cost ${cost:.5f}"
    run_text = f"config {reply['config']} x model {reply['model']}, {cost_text}"
    if not findings:
        return f"GDPR PR Reviewer: no findings ({run_text})."
    noun = "finding" if len(findings) == 1 else "findings"
    return (
        f"GDPR PR Reviewer: {len(findings)} {noun}, {inline} inline and "
        f"{len(findings) - inline} in the review body ({run_text})."
    )


def _review_body(
    findings: list[dict[str, Any]],
    elsewhere: list[tuple[dict[str, Any], str]],
    reply: dict[str, Any],
) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["category"]] = counts.get(finding["category"], 0) + 1
    noun = "risk" if len(findings) == 1 else "risks"
    lines = [
        "## GDPR PR Reviewer",
        "",
        f"{len(findings)} possible personal-data {noun}: "
        + ", ".join(f"{category}: {n}" for category, n in counts.items())
        + ".",
    ]
    if elsewhere:
        lines += ["", "### Not shown inline", ""]
        for finding, why in elsewhere:
            where = _safe(f"{finding['file']}:{finding['line']}").replace("`", "'")
            lines.append(
                f"- `{where}` **{finding['category']}** ({why}): {_safe(finding['explanation'])}"
            )
    lines += [
        "",
        f"Reviewed by config {_safe(reply['config'])} x model {_safe(reply['model'])}. "
        "The diff was sent to OpenRouter and the upstream provider.",
        "",
        f"<sub>{FOOTER}</sub>",
    ]
    return "\n".join(lines)


def _inline(finding: dict[str, Any]) -> dict[str, Any]:
    """One review comment. Line numbers are the service's: new-file on RIGHT, old-file on LEFT."""
    return {
        "path": finding["file"],
        "line": finding["line"],
        "side": "LEFT" if finding["side"] == "old" else "RIGHT",
        "body": _comment_text(finding),
    }


def _comment_text(finding: dict[str, Any]) -> str:
    lines = [
        f"**{finding['category']}** · {_safe(finding['article'])} · "
        f"confidence {finding['confidence']:.2f}",
        "",
        _safe(finding["explanation"]),
    ]
    if finding["suggested_fix"]:
        lines += ["", f"**Suggested fix:** {_safe(finding['suggested_fix'])}"]
    lines += ["", f"<sub>{FOOTER}</sub>", _marker(finding["fingerprint"])]
    return "\n".join(lines)


def _safe(text: str) -> str:
    """Model text, made inert: no @mentions (GitHub would notify), no HTML comments (which
    could forge a fingerprint marker)."""
    return text.replace("@", "@​").replace("<!--", "&lt;!--")


def _marker(fingerprint: str) -> str:
    return f"<!-- gdpr-reviewer:{fingerprint} -->"


# --- the workflow's environment ---------------------------------------------------------


def pull_request_from_event(event: Mapping[str, Any]) -> PullRequest:
    pr = event.get("pull_request")
    if not isinstance(pr, Mapping):
        raise ValueError("this event has no pull_request; run the Action on pull_request events")
    return PullRequest(
        number=int(pr["number"]), base_sha=pr["base"]["sha"], head_sha=pr["head"]["sha"]
    )


def inputs_from_env(env: Mapping[str, str]) -> Inputs:
    raw = env.get("INPUT_MAX_COMMENTS", "").strip()
    try:
        max_comments = int(raw) if raw else Inputs().max_comments
    except ValueError:
        raise ValueError(f"max-comments must be a whole number, got {raw!r}") from None
    if max_comments < 0:
        raise ValueError(f"max-comments must not be negative, got {max_comments}")
    return Inputs(max_comments=max_comments)


# --- adapters: real HTTP, used only by main() ------------------------------------------


def _request(
    url: str, *, token: str, method: str = "GET", body: Any = None, accept: str, timeout: int
) -> bytes:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", accept)
    request.add_header("User-Agent", "gdpr-pr-reviewer-action")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class GitHubAPI:
    def __init__(self, token: str, repository: str, api_url: str = "https://api.github.com"):
        self._token = token
        self._base = f"{api_url.rstrip('/')}/repos/{repository}"

    def get_diff(self, pr: PullRequest) -> str:
        # The diff between the event's two commits (the same three-dot diff the PR shows),
        # so a push during the run can't shift the lines the review is posted against.
        raw = self._call(
            f"/compare/{pr.base_sha}...{pr.head_sha}", accept="application/vnd.github.diff"
        )
        return raw.decode("utf-8")

    def create_review(self, number: int, review: dict[str, Any]) -> None:
        self._call(f"/pulls/{number}/reviews", method="POST", body=review)

    def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Any = None,
        accept: str = "application/vnd.github+json",
    ) -> bytes:
        return _request(
            self._base + path,
            token=self._token,
            method=method,
            body=body,
            accept=accept,
            timeout=GITHUB_TIMEOUT_S,
        )


class ServiceAPI:
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/") + "/review"
        self._token = token

    def review(self, diff: str) -> dict[str, Any]:
        raw = _request(
            self._url,
            token=self._token,
            method="POST",
            body={"diff": diff},
            accept="application/json",
            timeout=SERVICE_TIMEOUT_S,
        )
        reply: dict[str, Any] = json.loads(raw)
        return reply


def main(env: Mapping[str, str] = os.environ) -> int:
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as file:
        pr = pull_request_from_event(json.load(file))
    outcome = run(
        pr,
        inputs_from_env(env),
        GitHubAPI(
            env["INPUT_GITHUB_TOKEN"],
            env["GITHUB_REPOSITORY"],
            env.get("GITHUB_API_URL", "https://api.github.com"),
        ),
        ServiceAPI(env["INPUT_SERVICE_URL"], env["INPUT_SERVICE_TOKEN"]),
    )
    for line in outcome.summary:
        print(line)
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as file:
            file.write("\n".join(outcome.summary) + "\n")
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    sys.exit(main())
