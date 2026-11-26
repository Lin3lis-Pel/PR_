"""review(): the one interface. service/, mcp_server/, action/ and benchmark/ only call this."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from reviewer.configs import ReviewConfig
from reviewer.diff import number_diff, numbered_file
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


VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["keep", "confidence", "reason"],
    "additionalProperties": False,
}


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[Finding, ...]


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


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
    """Review a unified diff. repo_context (the case's context/ folder) is unused before v3."""
    del repo_context  # read only by repo-context configs (v3 and later)
    calls: list[CallRecord] = []
    if config.pipeline == "detect_verify":
        findings = _detect_verify(diff, config, llm, calls)
    else:
        findings = _detect(diff, config, llm, calls)
    return Review(findings=tuple(findings), calls=tuple(calls))


# --- pipelines ------------------------------------------------------------------------


def _detect(diff: str, config: ReviewConfig, llm: LLM, calls: list[CallRecord]) -> list[Finding]:
    messages = _messages(config.prompt, diff=number_diff(diff))
    output = _ask(llm, messages, "gdpr_review", OUTPUT_SCHEMA, _Output, config, calls)
    return list(output.findings)


class _State(TypedDict):
    candidates: list[Finding]
    findings: list[Finding]


def _detect_verify(
    diff: str, config: ReviewConfig, llm: LLM, calls: list[CallRecord]
) -> list[Finding]:
    """detect -> verify as a LangGraph graph. Nodes call the LLM seam; `calls` collects every
    request, so a failure anywhere still reports what it cost."""
    verify_prompt = config.verify_prompt
    if verify_prompt is None:
        raise ValueError(f"config {config.name} uses detect_verify but has no verify_prompt")

    def detect(_: _State) -> dict[str, Any]:
        return {"candidates": _detect(diff, config, llm, calls)}

    def verify(state: _State) -> dict[str, Any]:
        kept: list[Finding] = []
        for candidate in state["candidates"]:
            messages = _messages(
                verify_prompt,
                finding=json.dumps(candidate.model_dump(mode="json"), indent=2),
                diff=numbered_file(diff, candidate.file) or number_diff(diff),
            )
            verdict = _ask(llm, messages, "gdpr_verify", VERIFY_SCHEMA, _Verdict, config, calls)
            if verdict.keep:
                kept.append(candidate.model_copy(update={"confidence": verdict.confidence}))
        return {"findings": kept}

    def after_detect(state: _State) -> str:
        return "verify" if state["candidates"] else END

    graph = StateGraph(_State)
    graph.add_node("detect", detect)
    graph.add_node("verify", verify)
    graph.add_edge(START, "detect")
    graph.add_conditional_edges("detect", after_detect, ["verify", END])
    graph.add_edge("verify", END)
    final = graph.compile().invoke({"candidates": [], "findings": []})
    return list(final["findings"])


# --- one validated LLM answer, with retries -------------------------------------------


def _ask[T: BaseModel](
    llm: LLM,
    messages: Sequence[Message],
    schema_name: str,
    schema: dict[str, Any],
    output_type: type[T],
    config: ReviewConfig,
    calls: list[CallRecord],
) -> T:
    """Ask until the answer validates, at most max_retries + 1 times. Every attempt that
    reached the model is appended to `calls`."""
    attempts = config.max_retries + 1
    last_error = ""
    for _ in range(attempts):
        try:
            completion = llm.complete(
                messages,
                schema_name=schema_name,
                json_schema=schema,
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
            output = output_type.model_validate(json.loads(completion.text))
        except (json.JSONDecodeError, ValidationError) as error:
            last_error = f"invalid output: {_short(error)}"
            calls.append(completion.call.model_copy(update={"error": last_error}))
            continue

        calls.append(completion.call)
        return output

    raise ReviewError(
        f"{schema_name}: no valid answer after {attempts} attempts; last error: {last_error}",
        calls=tuple(calls),
    )


def _messages(prompt: str, **values: str) -> list[Message]:
    """The prompt set's system text, and its user text with {placeholders} filled in."""
    system = (PROMPTS / f"{prompt}_system.md").read_text(encoding="utf-8")
    user = (PROMPTS / f"{prompt}_user.md").read_text(encoding="utf-8")
    for key, value in values.items():
        user = user.replace("{" + key + "}", value)
    return [Message(role="system", content=system), Message(role="user", content=user)]


def _short(error: Exception, limit: int = 300) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
