"""The service's HTTP interface, tested with TestClient and the fake LLM adapter.

The settings and the LLM are replaced through FastAPI dependency overrides, so no test
reads .env or calls a model.
"""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from reviewer.config import SettingsError
from reviewer.llm import FakeLLM, LLMError
from service.app import app, get_llm, get_settings
from service.settings import ServiceSettings, load_service_settings

TOKEN = "test-token-not-a-secret"


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield _client()
    app.dependency_overrides.clear()


def _client(*responses: str | LLMError, **settings: object) -> TestClient:
    service_settings = ServiceSettings(token=SecretStr(TOKEN), **settings)
    fake = FakeLLM(responses)
    app.dependency_overrides[get_settings] = lambda: service_settings
    app.dependency_overrides[get_llm] = lambda: fake
    return TestClient(app)


def test_healthz_names_the_config_and_model_without_a_token(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "config": "v2", "model": "fake/model"}


# --- settings and startup ---------------------------------------------------------------


def test_settings_default_to_v2_and_200_kb() -> None:
    settings = load_service_settings({"GDPR_REVIEWER_TOKEN": TOKEN})

    assert settings.config == "v2"
    assert settings.max_diff_bytes == 200_000
    assert settings.token.get_secret_value() == TOKEN


def test_settings_read_config_and_diff_limit() -> None:
    settings = load_service_settings(
        {
            "GDPR_REVIEWER_TOKEN": TOKEN,
            "GDPR_REVIEWER_CONFIG": "v1_1",
            "GDPR_REVIEWER_MAX_DIFF_BYTES": "5000",
        }
    )

    assert settings.config == "v1_1"
    assert settings.max_diff_bytes == 5000


@pytest.mark.parametrize("value", ["lots", "0", "-1"])
def test_settings_reject_a_bad_diff_limit(value: str) -> None:
    with pytest.raises(SettingsError, match="GDPR_REVIEWER_MAX_DIFF_BYTES"):
        load_service_settings({"GDPR_REVIEWER_TOKEN": TOKEN, "GDPR_REVIEWER_MAX_DIFF_BYTES": value})


@pytest.mark.parametrize("token", ["", "   "])
def test_settings_refuse_a_missing_token(token: str) -> None:
    with pytest.raises(SettingsError, match="GDPR_REVIEWER_TOKEN"):
        load_service_settings({"GDPR_REVIEWER_TOKEN": token})


def test_service_refuses_to_start_without_a_token() -> None:
    app.dependency_overrides[get_settings] = lambda: load_service_settings({})
    try:
        with pytest.raises(SettingsError), TestClient(app):
            pass
    finally:
        app.dependency_overrides.clear()


def test_service_refuses_to_start_with_an_unknown_config() -> None:
    app.dependency_overrides[get_settings] = lambda: ServiceSettings(
        token=SecretStr(TOKEN), config="no_such_config"
    )
    try:
        with pytest.raises(ValueError, match="no_such_config"), TestClient(app):
            pass
    finally:
        app.dependency_overrides.clear()


# --- POST /review ----------------------------------------------------------------------

DIFF = """\
diff --git a/app/views.py b/app/views.py
--- a/app/views.py
+++ b/app/views.py
@@ -1,2 +1,3 @@
 def signup(user):
+    logger.info("signup %s", user.email)
     return user
"""

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def finding(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
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


def output(*findings: dict[str, object]) -> str:
    return json.dumps({"findings": list(findings)})


def test_review_returns_the_findings_config_and_model() -> None:
    client = _client(output(finding()))

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["config"] == "v2"
    assert body["model"] == "fake/model"
    assert body["provider"] == "fake"
    assert body["cost_usd"] == 0.0
    assert body["latency_s"] >= 0.0
    [returned] = body["findings"]
    assert {key: returned[key] for key in finding()} == finding()


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic x"}],
)
def test_review_needs_the_bearer_token(headers: dict[str, str]) -> None:
    client = _client(output(finding()))

    response = client.post("/review", json={"diff": DIFF}, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_review_drops_findings_below_the_configs_threshold() -> None:
    # v1_1 is v1 with threshold 0.7: findings at or above it are shown.
    client = _client(
        output(finding(confidence=0.9), finding(confidence=0.7), finding(confidence=0.69)),
        config="v1_1",
    )

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 200
    assert [f["confidence"] for f in response.json()["findings"]] == [0.9, 0.7]
    assert response.json()["config"] == "v1_1"


def test_review_checks_the_token_before_the_body() -> None:
    client = _client()

    response = client.post("/review", json={"not": "a review request"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "diff", ["", "   \n", "this is not a diff", "--- a/x\n+++ b/x\n@@ broken @@\n"]
)
def test_review_rejects_an_empty_or_unparseable_diff_without_calling_the_model(
    diff: str,
) -> None:
    fake = FakeLLM([])
    client = _client()
    app.dependency_overrides[get_llm] = lambda: fake

    response = client.post("/review", json={"diff": diff}, headers=AUTH)

    assert response.status_code == 422
    assert fake.requests == []


def test_review_rejects_a_diff_over_the_size_limit_without_calling_the_model() -> None:
    fake = FakeLLM([])
    client = _client(max_diff_bytes=len(DIFF.encode()) - 1)
    app.dependency_overrides[get_llm] = lambda: fake

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 422
    assert "limit" in response.json()["detail"]
    assert fake.requests == []


def test_a_diff_at_the_size_limit_is_reviewed() -> None:
    client = _client(output(), max_diff_bytes=len(DIFF.encode()))

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [{"patch": DIFF}, {"diff": DIFF, "extra": DIFF}, {"diff": [DIFF]}, [DIFF]],
)
def test_a_malformed_request_is_rejected_without_echoing_the_diff(body: object) -> None:
    client = _client()

    response = client.post("/review", json=body, headers=AUTH)

    assert response.status_code == 422
    assert "user.email" not in response.text


def test_review_is_a_502_when_the_model_gives_no_valid_answer() -> None:
    client = _client("not json", "still not json", "{}")

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 502
    assert "user.email" not in response.text


def test_review_is_a_502_when_the_model_call_fails() -> None:
    client = _client(LLMError("AuthenticationError (HTTP 401): bad key", fatal=True))

    response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 502


# --- postable and fingerprint ----------------------------------------------------------

# Old side shows lines 10-13, new side 10-14: new 14 has no old-side twin.
EDIT = """\
diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -10,4 +10,5 @@ class UserService:
     def delete(self, user):
-        anonymise(user)
+        logger.info("deleting %s", user.email)
         user.delete()
     # end
+    # more
"""

RENAME = """\
diff --git a/app/old_name.py b/app/new_name.py
similarity index 90%
rename from app/old_name.py
rename to app/new_name.py
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -1,2 +1,2 @@
 def notify(user):
-    return user
+    print(user.email)
"""


def _shown(diff: str, *findings: dict[str, object]) -> list[dict[str, object]]:
    client = _client(output(*findings))
    response = client.post("/review", json={"diff": diff}, headers=AUTH)
    assert response.status_code == 200
    return response.json()["findings"]


@pytest.mark.parametrize(
    ("file", "line", "side", "postable"),
    [
        ("app/users.py", 11, "new", True),  # added line
        ("app/users.py", 11, "old", True),  # removed line
        ("app/users.py", 10, "new", True),  # context line, new side
        ("app/users.py", 10, "old", True),  # context line, old side
        ("app/users.py", 14, "new", True),  # added line past the old side's end
        ("app/users.py", 14, "old", False),  # wrong side: old side stops at 13
        ("app/users.py", 40, "new", False),  # outside every hunk
        ("app/other.py", 11, "new", False),  # file not in the diff
    ],
)
def test_postable_says_whether_the_line_can_take_an_inline_comment(
    file: str, line: int, side: str, postable: bool
) -> None:
    [shown] = _shown(EDIT, finding(file=file, line=line, side=side))

    assert shown["postable"] is postable


@pytest.mark.parametrize(
    ("file", "line", "side", "postable"),
    [
        ("app/new_name.py", 2, "new", True),
        ("app/new_name.py", 2, "old", True),  # GitHub keys both sides by the new path
        ("app/old_name.py", 2, "old", False),
    ],
)
def test_postable_keys_a_renamed_file_by_its_new_path(
    file: str, line: int, side: str, postable: bool
) -> None:
    [shown] = _shown(RENAME, finding(file=file, line=line, side=side))

    assert shown["postable"] is postable


MOVED = """\
diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -30,3 +30,4 @@ class UserService:
     def delete(self, user):
+            logger.info("deleting %s", user.email)
         user.delete()
     # end
"""


def test_fingerprint_follows_the_lines_content_not_its_number() -> None:
    [before] = _shown(EDIT, finding(file="app/users.py", line=11))
    [after] = _shown(MOVED, finding(file="app/users.py", line=31))

    assert before["fingerprint"] == after["fingerprint"]


def test_fingerprint_differs_by_file_category_and_content() -> None:
    base = finding(file="app/users.py", line=11)
    [same] = _shown(EDIT, base)
    [other_category] = _shown(EDIT, finding(file="app/users.py", line=11, category="PII_STORAGE"))
    [other_line] = _shown(EDIT, finding(file="app/users.py", line=12))
    [other_file] = _shown(RENAME, finding(file="app/new_name.py", line=2))

    fingerprints = {
        same["fingerprint"],
        other_category["fingerprint"],
        other_line["fingerprint"],
        other_file["fingerprint"],
    }
    assert len(fingerprints) == 4


def test_a_finding_on_a_line_the_diff_does_not_show_gets_a_fingerprint_by_number() -> None:
    [at_40] = _shown(EDIT, finding(file="app/users.py", line=40))
    [at_41] = _shown(EDIT, finding(file="app/users.py", line=41))
    [at_40_again] = _shown(EDIT, finding(file="app/users.py", line=40))

    assert at_40["fingerprint"]
    assert at_40["fingerprint"] != at_41["fingerprint"]
    assert at_40["fingerprint"] == at_40_again["fingerprint"]


def test_fingerprint_does_not_reveal_the_line() -> None:
    [shown] = _shown(EDIT, finding(file="app/users.py", line=11))

    assert "email" not in str(shown["fingerprint"])


# --- logging: metadata only ------------------------------------------------------------


def _service_log(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("service"))


def test_a_review_logs_one_metadata_line_and_names_it_in_the_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(output(finding(), finding(line=1)))

    with caplog.at_level(logging.INFO, logger="service"):
        response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    request_id = response.headers["X-Request-ID"]
    log = _service_log(caplog)
    assert f"request_id={request_id}" in log
    assert f"diff_bytes={len(DIFF.encode())}" in log
    assert "findings=2" in log
    assert "model=fake/model" in log
    assert "provider=fake" in log
    assert "cost_usd=0.0" in log
    assert "latency_s=" in log


def test_the_log_never_holds_the_diff_the_findings_or_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(output(finding()))

    with caplog.at_level(logging.DEBUG):
        client.post("/review", json={"diff": DIFF}, headers=AUTH)
        client.post("/review", json={"diff": DIFF}, headers={"Authorization": "Bearer wrong"})

    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert "user.email" not in everything
    assert "Logs the user's email" not in everything
    assert "Log user.id instead" not in everything
    assert TOKEN not in everything


def test_a_failed_review_logs_the_failure_without_the_models_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The model's invalid output can quote the diff; it must not reach the log.
    leaked = json.dumps({"findings": "user.email is logged"})
    client = _client(leaked, leaked, leaked)

    with caplog.at_level(logging.DEBUG):
        response = client.post("/review", json={"diff": DIFF}, headers=AUTH)

    assert response.status_code == 502
    log = _service_log(caplog)
    assert f"request_id={response.headers['X-Request-ID']}" in log
    assert "error=ReviewError" in log
    assert "user.email" not in "\n".join(r.getMessage() for r in caplog.records)
