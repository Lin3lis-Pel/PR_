"""Review one pull request: fetch its diff, send it to the service, post one review.

Standard library only: the Action runs on the runner's own Python, with nothing installed.
All GitHub I/O happens here; the service never sees a GitHub token (ADR 0005).

    python3 review_pr.py   # reads the INPUT_* and GITHUB_* variables action.yml sets
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

FOOTER = "Automated check, not legal advice."
SERVICE_TIMEOUT_S = 120
GITHUB_TIMEOUT_S = 30
# The service's default limit. A diff over it is skipped here instead of being sent.
MAX_DIFF_BYTES = 200_000
DEFAULT_CONFIG_FILE = ".gdpr-reviewer.yml"
# Reading earlier comments: 100 per page, at most 30 pages (3,000) per kind.
PAGE_SIZE = 100
MAX_PAGES = 30


@dataclass(frozen=True)
class PullRequest:
    number: int
    base_sha: str
    head_sha: str  # the diff is taken up to this commit, and the review is posted against it
    draft: bool = False
    fork: bool = False  # head and base are different repositories: no secrets on its runs


@dataclass(frozen=True)
class Inputs:
    max_comments: int = 10
    fail_on_error: bool = False
    config_file: str = DEFAULT_CONFIG_FILE


@dataclass
class Outcome:
    summary: list[str]  # lines for the job summary
    warnings: list[str] = field(default_factory=list)  # shown as workflow warning annotations
    failed: bool = False  # only an error with fail-on-error; findings never fail the job


class CallError(Exception):
    """A request to GitHub or the service that failed. The message never holds a token."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ConfigError(ValueError):
    """The config file's ignored_paths can't be read by this Action's small reader."""


class GitHubClient(Protocol):
    def get_diff(self, pr: PullRequest) -> str: ...

    def get_file(self, path: str, ref: str) -> str | None: ...  # None: no such file

    def create_review(self, number: int, review: dict[str, Any]) -> None: ...

    def list_posted_texts(self, number: int) -> list[str]: ...  # review and comment bodies


class ReviewerClient(Protocol):
    def review(self, diff: str) -> dict[str, Any]: ...


def run(pr: PullRequest, inputs: Inputs, github: GitHubClient, reviewer: ReviewerClient) -> Outcome:
    if pr.draft:
        return _skipped("the PR is a draft")
    if pr.fork:
        return _skipped("the PR comes from a fork, and workflow secrets are not available to it")
    try:
        return _review(pr, inputs, github, reviewer)
    except Exception as error:  # the boundary: any failure is a warning, never a crash
        message = f"GDPR PR Reviewer could not review this PR: {_describe(error)}"
        return Outcome(summary=[message], warnings=[message], failed=inputs.fail_on_error)


def _review(
    pr: PullRequest, inputs: Inputs, github: GitHubClient, reviewer: ReviewerClient
) -> Outcome:
    diff = github.get_diff(pr)
    config = github.get_file(inputs.config_file, pr.head_sha)
    try:
        patterns = [_glob(p) for p in ignored_paths_from_config(config or "")]
    except ConfigError as error:
        raise ConfigError(
            f"{inputs.config_file}: {error}. Nothing was sent, so no ignored file leaves the repo."
        ) from None

    files = _split_files(diff)
    kept = [chunk for chunk in files if not _is_ignored(chunk, patterns)]
    if files and not kept:
        return _skipped(f"every changed file matches ignored_paths in {inputs.config_file}")
    if not any(_has_hunk(chunk) for chunk in kept):
        return _skipped("the diff has no changed lines (only renames or binary files)")
    to_send = "".join(kept)
    size = len(to_send.encode("utf-8"))
    if size > MAX_DIFF_BYTES:
        return _skipped(f"the diff is {size:,} bytes, over the {MAX_DIFF_BYTES:,}-byte limit")

    reply = reviewer.review(to_send)
    findings = _one_per_fingerprint(sorted(reply["findings"], key=lambda f: -f["confidence"]))
    already = 0
    if findings:  # skip the extra GitHub calls when there is nothing to post anyway
        posted = posted_fingerprints(github.list_posted_texts(pr.number))
        new = [f for f in findings if f["fingerprint"] not in posted]
        already, findings = len(findings) - len(new), new

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
    return Outcome(summary=[_summary_line(findings, len(inline), already, reply)])


def _one_per_fingerprint(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first (highest-confidence) finding of each fingerprint: the same risk on two
    identical lines is one comment."""
    seen: set[str] = set()
    unique = []
    for finding in findings:
        if finding["fingerprint"] not in seen:
            seen.add(finding["fingerprint"])
            unique.append(finding)
    return unique


_MARKER = re.compile(r"<!-- gdpr-reviewer:([^\s<>]+) -->")


def posted_fingerprints(texts: list[str]) -> set[str]:
    """Fingerprints in the reviewer's own markers. Only the exact HTML-comment form counts,
    which model text can't produce: _safe() turns its '<!--' into '&lt;!--'."""
    return {match for text in texts for match in _MARKER.findall(text)}


def _skipped(reason: str) -> Outcome:
    return Outcome(summary=[f"GDPR PR Reviewer: skipped, {reason}."])


def _describe(error: Exception) -> str:
    if isinstance(error, (CallError, ConfigError)):
        return str(error)
    if isinstance(error, TimeoutError):
        return "a request timed out"
    return f"{type(error).__name__}: {error}"


def _summary_line(
    findings: list[dict[str, Any]], inline: int, already: int, reply: dict[str, Any]
) -> str:
    """findings: the new ones this run posts. already: shown findings that were on the PR."""
    cost = reply.get("cost_usd")
    cost_text = "cost unknown" if cost is None else f"cost ${cost:.5f}"
    run_text = f"config {reply['config']} x model {reply['model']}, {cost_text}"
    earlier = f"{already} already posted on this PR" if already else ""
    if not findings:
        if earlier:
            return f"GDPR PR Reviewer: no new findings; {earlier} ({run_text})."
        return f"GDPR PR Reviewer: no findings ({run_text})."
    noun = "finding" if len(findings) == 1 else "findings"
    return (
        f"GDPR PR Reviewer: {len(findings)} {noun}, {inline} inline and "
        f"{len(findings) - inline} in the review body"
        + (f"; {earlier}" if earlier else "")
        + f" ({run_text})."
    )


# --- the review's text ------------------------------------------------------------------


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
                f" {_marker(finding['fingerprint'])}"  # so a later run doesn't list it again
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


# --- ignored_paths: reading the config, matching files ------------------------------------


def ignored_paths_from_config(text: str) -> list[str]:
    """The top-level ignored_paths list, as a block list or a flow list. Other keys are
    skipped unparsed: this is not a YAML parser, only enough of one for this key."""
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        line = _strip_comment(raw)
        if not line.strip() or raw[:1] in (" ", "\t"):
            continue  # blank, a comment, or nested under another key
        key, colon, rest = line.partition(":")
        if key.strip() != "ignored_paths" or not colon:
            continue
        rest = rest.strip()
        return _flow_list(rest) if rest else _block_list(lines[index + 1 :])
    return []


def _flow_list(text: str) -> list[str]:
    if not (text.startswith("[") and text.endswith("]")):
        raise ConfigError("ignored_paths must be a list of path globs")
    inner = text[1:-1]
    if any(bracket in inner for bracket in "[]{}"):
        raise ConfigError("ignored_paths must be a flat list of path globs")
    items = [_unquote(item.strip()) for item in inner.split(",")]
    return [item for item in items if item]


def _block_list(lines: list[str]) -> list[str]:
    items: list[str] = []
    for raw in lines:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if raw[:1] not in (" ", "\t", "-"):
            break  # the next top-level key
        item = line.strip()
        if not (item == "-" or item.startswith("- ")):
            raise ConfigError("ignored_paths must be a list of path globs, one per '- ' line")
        value = item[1:].strip()
        if not value or value[0] in "[{":
            raise ConfigError("each ignored_paths entry must be one path glob")
        items.append(_unquote(value))
    return items


def _strip_comment(line: str) -> str:
    """The line without a trailing '# comment' (a '#' at the start or after a space, outside
    quotes)."""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _glob(pattern: str) -> re.Pattern[str]:
    """A repo-relative path glob: '*' and '?' stay inside one folder, '**' spans any number
    of folders (none included), and a trailing '/' means everything under that folder."""
    pattern = pattern.strip().removeprefix("./").lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def _split_files(diff: str) -> list[str]:
    """The diff cut into one chunk per file, each starting at its 'diff --git' line. Joining
    the chunks gives back the diff unchanged."""
    chunks: list[str] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            chunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _is_ignored(chunk: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.match(path) for path in _paths(chunk) for pattern in patterns)


def _paths(chunk: str) -> set[str]:
    """Every path a file chunk names: old and new, so a rename or a delete matches too."""
    paths: set[str] = set()
    header = ""
    for line in chunk.splitlines():
        if line.startswith("@@"):
            break  # past the file header: '--- ' lines below are removed content
        if line.startswith("diff --git "):
            header = line
        for prefix in ("rename from ", "rename to ", "copy from ", "copy to "):
            if line.startswith(prefix):
                paths.add(_git_path(line[len(prefix) :]))
        if line.startswith(("--- ", "+++ ")):
            path = _git_path(line[4:])
            if path != "/dev/null":
                paths.add(path.removeprefix("a/").removeprefix("b/"))
    if not paths and header:  # binary files and pure mode changes have only the header
        match = re.match(r"diff --git a/(.+) b/(.+)$", header)
        if match:
            paths |= {match.group(1), match.group(2)}
    return paths


def _git_path(text: str) -> str:
    """Git quotes paths with unusual characters: "a/caf\\303\\251.py"."""
    text = text.rstrip("\t")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            raw = text[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return text[1:-1]
    return text


def _has_hunk(chunk: str) -> bool:
    return any(line.startswith("@@ ") for line in chunk.splitlines())


# --- the workflow's environment ---------------------------------------------------------


def pull_request_from_event(event: Mapping[str, Any]) -> PullRequest:
    pr = event.get("pull_request")
    if not isinstance(pr, Mapping):
        raise ValueError("this event has no pull_request; run the Action on pull_request events")
    head_repo = (pr.get("head") or {}).get("repo") or {}
    base_repo = (pr.get("base") or {}).get("repo") or {}
    same_repo = bool(head_repo) and head_repo.get("full_name") == base_repo.get("full_name")
    return PullRequest(
        number=int(pr["number"]),
        base_sha=pr["base"]["sha"],
        head_sha=pr["head"]["sha"],
        draft=bool(pr.get("draft", False)),
        fork=not same_repo,  # a deleted fork has no head repo: treat it as a fork
    )


def inputs_from_env(env: Mapping[str, str]) -> Inputs:
    raw = env.get("INPUT_MAX_COMMENTS", "").strip()
    try:
        max_comments = int(raw) if raw else Inputs().max_comments
    except ValueError:
        raise ValueError(f"max-comments must be a whole number, got {raw!r}") from None
    if max_comments < 0:
        raise ValueError(f"max-comments must not be negative, got {max_comments}")

    raw_fail = env.get("INPUT_FAIL_ON_ERROR", "").strip().lower()
    if raw_fail not in ("", "true", "false"):
        raise ValueError(f"fail-on-error must be true or false, got {raw_fail!r}")

    return Inputs(
        max_comments=max_comments,
        fail_on_error=raw_fail == "true",
        config_file=env.get("INPUT_CONFIG_FILE", "").strip() or DEFAULT_CONFIG_FILE,
    )


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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise CallError(f"{method} {url}: HTTP {error.code} {detail}", status=error.code) from None
    except urllib.error.URLError as error:
        raise CallError(f"{method} {url}: {error.reason}") from None


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

    def get_file(self, path: str, ref: str) -> str | None:
        query = urllib.parse.urlencode({"ref": ref})
        try:
            raw = self._call(
                f"/contents/{urllib.parse.quote(path)}?{query}",
                accept="application/vnd.github.raw+json",
            )
        except CallError as error:
            if error.status == 404:
                return None
            raise
        return raw.decode("utf-8")

    def create_review(self, number: int, review: dict[str, Any]) -> None:
        self._call(f"/pulls/{number}/reviews", method="POST", body=review)

    def list_posted_texts(self, number: int) -> list[str]:
        texts: list[str] = []
        for kind in ("comments", "reviews"):  # inline comments, then review bodies
            texts += [item.get("body") or "" for item in self._pages(f"/pulls/{number}/{kind}")]
        return texts

    def _pages(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = json.loads(self._call(f"{path}?per_page={PAGE_SIZE}&page={page}"))
            items += batch
            if len(batch) < PAGE_SIZE:
                break
        return items

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


def _annotation(kind: str, message: str) -> str:
    """A workflow command; its message must not break onto a new line."""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind}::{escaped}"


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
    for warning in outcome.warnings:
        print(_annotation("warning", warning))
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as file:
            file.write("\n".join(outcome.summary) + "\n")
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    sys.exit(main())
