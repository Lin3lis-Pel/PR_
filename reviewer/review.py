"""review(): the one interface. service/, mcp_server/, action/ and benchmark/ only call this."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from reviewer.configs import ReviewConfig
from reviewer.diff import number_diff
from reviewer.llm import LLM, LLMError, Message
from reviewer.schemas import CallRecord, Category, Finding, Review, Side

PROMPTS = Path(__file__).parent / "prompts"

# What the model must return. Strict: every field required, nothing extra.
_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "line": {"type": "integer", "minimum": 1},
        "side": {"type": "string", "enum": [side.value for side in Side]},
        "category": {"type": "string", "enum": [category.value for category in Category]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "article": {"type": "string"},
        "explanation": {"type": "string"},
        "suggested_fix": {"type": "string"},
    },
    "required": [
        "file",
        "line",
        "side",
        "category",
        "confidence",
        "article",
        "explanation",
        "suggested_fix",
    ],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": _FINDING_SCHEMA}},
    "required": ["findings"],
    "additionalProperties": False,
}


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[Finding, ...]


class ReviewError(Exception):
    """No usable answer. `calls` holds every attempt, so failed cases still show their cost."""

    def __init__(self, message: str, *, calls: tuple[CallRecord, ...], fatal: bool = False):
        super().__init__(message)
        self.calls = calls
        self.fatal = fatal


def review(
    diff: str,
    repo_context: Path | None = None,
    *,
    config: ReviewConfig,
    llm: LLM,
) -> Review:
    """Review a unified diff. repo_context (the case's context/ folder) is unused by v1."""
    del repo_context  # read only by repo-context configs (v3 and later)
    messages = _messages(diff, config)
    calls: list[CallRecord] = []
    attempts = config.max_retries + 1
    last_error = ""

    for _ in range(attempts):
        try:
            completion = llm.complete(
                messages,
                schema_name="gdpr_review",
                json_schema=OUTPUT_SCHEMA,
                temperature=config.temperature,
            )
        except LLMError as error:
            last_error = str(error)
            if error.call is not None:
                calls.append(error.call.model_copy(update={"error": last_error}))
            if error.fatal:
                raise ReviewError(last_error, calls=tuple(calls), fatal=True) from error
            continue

        try:
            output = _Output.model_validate(json.loads(completion.text))
        except (json.JSONDecodeError, ValidationError) as error:
            last_error = f"invalid output: {_short(error)}"
            calls.append(completion.call.model_copy(update={"error": last_error}))
            continue

        calls.append(completion.call)
        return Review(findings=output.findings, calls=tuple(calls))

    raise ReviewError(
        f"no valid answer after {attempts} attempts; last error: {last_error}",
        calls=tuple(calls),
    )


def _messages(diff: str, config: ReviewConfig) -> list[Message]:
    system = (PROMPTS / f"{config.prompt}_system.md").read_text(encoding="utf-8")
    user = (PROMPTS / f"{config.prompt}_user.md").read_text(encoding="utf-8")
    return [
        Message(role="system", content=system),
        Message(role="user", content=user.replace("{diff}", number_diff(diff))),
    ]


def _short(error: Exception, limit: int = 300) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
