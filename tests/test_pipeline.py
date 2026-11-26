"""detect_verify (v2_1): one detect call, then one verify call per candidate finding."""

import json
from typing import Any

import pytest

from reviewer.configs import ReviewConfig, load_config
from reviewer.llm import FakeLLM, LLMError
from reviewer.review import VERIFY_SCHEMA, ReviewError, review

DIFF = """\
diff --git a/app/views.py b/app/views.py
--- a/app/views.py
+++ b/app/views.py
@@ -1,2 +1,4 @@
 def signup(user):
+    logger.info("signup %s", user.email)
+    logger.info("plan %s", user.plan)
     return user
diff --git a/app/other.py b/app/other.py
--- a/app/other.py
+++ b/app/other.py
@@ -1,1 +1,2 @@
 X = 1
+Y = 2
"""

CONFIG = ReviewConfig(
    name="t", prompt="v2", pipeline="detect_verify", verify_prompt="verify_v1", max_retries=1
)


def finding(line: int, confidence: float = 0.8) -> dict[str, Any]:
    return {
        "file": "app/views.py",
        "line": line,
        "side": "new",
        "category": "PII_LOGGING",
        "confidence": confidence,
        "article": "Art. 5(1)(c)",
        "explanation": "Logs personal data.",
        "suggested_fix": "Log the id.",
    }


def detected(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


def verdict(keep: bool, confidence: float = 0.9) -> str:
    return json.dumps({"keep": keep, "confidence": confidence, "reason": "because"})


def test_no_candidates_means_no_verify_call() -> None:
    llm = FakeLLM([detected()])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert result.findings == ()
    assert len(llm.requests) == 1


def test_verify_keeps_with_its_confidence_or_drops() -> None:
    llm = FakeLLM([detected(finding(2), finding(3)), verdict(True, 0.95), verdict(False, 0.2)])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert [(f.line, f.confidence) for f in result.findings] == [(2, 0.95)]
    assert len(result.calls) == 3


def test_verify_request_shows_the_finding_and_only_its_file() -> None:
    llm = FakeLLM([detected(finding(2)), verdict(True)])

    review(DIFF, config=CONFIG, llm=llm)

    verify = llm.requests[1]
    system, user = verify.messages
    assert "keep" in system.content
    assert '"line": 2' in user.content
    assert "=== app/views.py (modified) ===" in user.content
    assert "app/other.py" not in user.content
    assert verify.json_schema == VERIFY_SCHEMA
    assert "{finding}" not in user.content and "{diff}" not in user.content


def test_verify_of_a_file_not_in_the_diff_shows_the_whole_diff() -> None:
    elsewhere = {**finding(2), "file": "nowhere.py"}
    llm = FakeLLM([detected(elsewhere), verdict(False)])

    review(DIFF, config=CONFIG, llm=llm)

    assert "app/other.py" in llm.requests[1].messages[1].content


def test_bad_verify_output_is_retried() -> None:
    llm = FakeLLM([detected(finding(2)), "nope", verdict(True)])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert len(result.findings) == 1
    assert result.calls[1].error is not None


def test_verify_failure_fails_the_review_and_keeps_every_call() -> None:
    llm = FakeLLM([detected(finding(2)), "nope", "nope"])

    with pytest.raises(ReviewError) as raised:
        review(DIFF, config=CONFIG, llm=llm)

    assert len(raised.value.calls) == 3


def test_fatal_error_in_verify_is_fatal() -> None:
    llm = FakeLLM([detected(finding(2)), LLMError("no credit", fatal=True)])

    with pytest.raises(ReviewError) as raised:
        review(DIFF, config=CONFIG, llm=llm)

    assert raised.value.fatal


def test_v2_1_config() -> None:
    config = load_config("v2_1")

    assert (config.prompt, config.pipeline, config.verify_prompt) == (
        "v2",
        "detect_verify",
        "verify_v1",
    )
