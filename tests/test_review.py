"""review(): the one interface every wrapper calls. Tested with the fake adapter only."""

import json
from typing import Any

import pytest

from reviewer.configs import ReviewConfig, load_config
from reviewer.llm import FakeLLM, LLMError
from reviewer.review import OUTPUT_SCHEMA, ReviewError, review
from reviewer.schemas import CallRecord, Category, Finding, Side

DIFF = """\
diff --git a/app/views.py b/app/views.py
--- a/app/views.py
+++ b/app/views.py
@@ -1,2 +1,3 @@
 def signup(user):
+    logger.info("signup %s", user.email)
     return user
"""

CONFIG = ReviewConfig(name="test", prompt="v1", temperature=0.0, max_retries=2)


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
    }
    return {**base, **overrides}


def output(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


def test_returns_the_models_findings() -> None:
    result = review(DIFF, config=CONFIG, llm=FakeLLM([output(finding())]))

    assert result.findings == (
        Finding(
            file="app/views.py",
            line=2,
            side=Side.NEW,
            category=Category.PII_LOGGING,
            confidence=0.9,
            article="Art. 5(1)(c)",
            explanation="Logs the user's email.",
            suggested_fix="Log user.id instead.",
        ),
    )
    assert len(result.calls) == 1


def test_no_findings_is_a_normal_answer() -> None:
    result = review(DIFF, config=CONFIG, llm=FakeLLM([output()]))

    assert result.findings == ()


def test_prompt_has_the_config_system_prompt_and_the_numbered_diff() -> None:
    llm = FakeLLM([output()])

    review(DIFF, config=CONFIG, llm=llm)

    [request] = llm.requests
    system, user = request.messages
    assert system.role == "system"
    assert "PII_LOGGING" in system.content
    assert user.role == "user"
    assert "=== app/views.py (modified) ===" in user.content
    assert '+           2 |     logger.info("signup %s", user.email)' in user.content
    assert "{diff}" not in user.content
    assert request.temperature == 0.0
    assert request.json_schema == OUTPUT_SCHEMA


def test_findings_outside_the_diff_are_kept_for_the_scorer() -> None:
    llm = FakeLLM([output(finding(file="elsewhere.py", line=999))])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert result.findings[0].file == "elsewhere.py"


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"wrong": []}),
        output(finding(category="COOKIES")),
        output(finding(confidence=1.5)),
        output(finding(line=0)),
        output({**finding(), "extra": "field"}),
    ],
)
def test_bad_output_is_retried(bad: str) -> None:
    llm = FakeLLM([bad, output(finding())])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert len(result.findings) == 1
    assert len(result.calls) == 2
    assert result.calls[0].error is not None
    assert result.calls[1].error is None


def test_gives_up_after_max_retries_and_keeps_every_call() -> None:
    llm = FakeLLM(["nope", "nope", "nope"])

    with pytest.raises(ReviewError, match="3 attempts") as raised:
        review(DIFF, config=CONFIG, llm=llm)

    assert len(raised.value.calls) == 3
    assert not raised.value.fatal


def test_api_errors_are_retried() -> None:
    llm = FakeLLM([LLMError("upstream 502"), output()])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert result.findings == ()
    assert len(llm.requests) == 2


def test_a_failed_call_that_still_cost_money_is_recorded() -> None:
    costly = CallRecord(model="fake/model", provider="fake", cost_usd=0.01, cost_method="reported")
    llm = FakeLLM([LLMError("empty response", call=costly), output()])

    result = review(DIFF, config=CONFIG, llm=llm)

    assert result.cost_usd == pytest.approx(0.01)
    assert result.calls[0].error == "empty response"


def test_fatal_errors_are_not_retried() -> None:
    llm = FakeLLM([LLMError("bad key", fatal=True)])

    with pytest.raises(ReviewError, match="bad key") as raised:
        review(DIFF, config=CONFIG, llm=llm)

    assert raised.value.fatal
    assert len(llm.requests) == 1


def test_v1_config_is_loadable_by_name() -> None:
    config = load_config("v1")

    assert config.name == "v1"
    assert config.temperature == 0.0
    assert config.max_retries == 2


def test_unknown_config_name_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="no config named 'v99'"):
        load_config("v99")


def test_v1_1_is_v1_with_a_threshold() -> None:
    v1, v1_1 = load_config("v1"), load_config("v1_1")

    assert v1_1.threshold == 0.7
    assert v1_1.model_dump(exclude={"name", "threshold"}) == v1.model_dump(
        exclude={"name", "threshold"}
    )


def test_v2_prompt_is_v1_plus_only_the_grouping_rule() -> None:
    import difflib

    from reviewer.review import PROMPTS

    v1 = (PROMPTS / "v1_system.md").read_text(encoding="utf-8").splitlines()
    v2 = (PROMPTS / "v2_system.md").read_text(encoding="utf-8").splitlines()
    changes = [line for line in difflib.ndiff(v1, v2) if line[:2] in ("+ ", "- ")]
    added = [line[2:] for line in changes if line.startswith("+ ")]

    assert all(line.startswith("+ ") for line in changes), "v2 must not remove v1 text"
    assert "One risk is one finding" in " ".join(added)
    assert len([line for line in added if line.strip()]) == 4
    assert (PROMPTS / "v1_user.md").read_text() == (PROMPTS / "v2_user.md").read_text()
