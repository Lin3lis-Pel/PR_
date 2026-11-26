"""The LLM seam. Nothing outside this module imports an LLM SDK.

Two adapters:
- OpenRouterLLM: real calls through OpenRouter's OpenAI-compatible API.
- FakeLLM: scripted responses for unit tests. Unit tests never call a real model.
"""

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import openai
from pydantic import BaseModel, ConfigDict

from reviewer.config import ModelSettings
from reviewer.schemas import CallRecord, CostMethod

# The SDK retries connection errors, timeouts, 429s and 5xx with backoff before giving up.
SDK_RETRIES = 2
TIMEOUT_S = 180.0

# Errors where retrying the same request can't help: bad key, no credit, unknown model, a
# request the provider rejects. They stop a benchmark run instead of failing every case.
_FATAL = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.BadRequestError,
)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user"]
    content: str


class Completion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    call: CallRecord


class LLMError(Exception):
    """A call that produced no usable response. `call` is set when it still cost something."""

    def __init__(self, message: str, *, fatal: bool = False, call: CallRecord | None = None):
        super().__init__(message)
        self.fatal = fatal
        self.call = call


class LLM(Protocol):
    model: str

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema_name: str,
        json_schema: Mapping[str, Any],
        temperature: float,
    ) -> Completion: ...


# --- OpenRouter -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Pricing:
    input_per_token: float
    output_per_token: float


class OpenRouterLLM:
    def __init__(self, settings: ModelSettings, *, client: Any = None) -> None:
        self.model = settings.model
        self.provider = settings.provider
        self._client = client or openai.OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            max_retries=SDK_RETRIES,
            timeout=TIMEOUT_S,
        )
        self._pricing: _Pricing | None = None

    def preflight(self) -> None:
        """Check the model exists on OpenRouter and read its listed price. Costs nothing."""
        try:
            listed = {model.id: model for model in self._client.models.list()}
        except openai.OpenAIError as error:
            raise LLMError(f"could not list OpenRouter models: {_describe(error)}") from error
        if self.model not in listed:
            raise LLMError(
                f"model {self.model} is not listed on OpenRouter; check GDPR_REVIEWER_MODEL",
                fatal=True,
            )
        pricing = (listed[self.model].model_extra or {}).get("pricing") or {}
        try:
            self._pricing = _Pricing(float(pricing["prompt"]), float(pricing["completion"]))
        except (KeyError, TypeError, ValueError):
            self._pricing = None

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema_name: str,
        json_schema: Mapping[str, Any],
        temperature: float,
    ) -> Completion:
        extra_body: dict[str, Any] = {"usage": {"include": True}}
        if self.provider:
            extra_body["provider"] = {"order": [self.provider], "allow_fallbacks": False}

        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[message.model_dump() for message in messages],
                temperature=temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
                },
                extra_body=extra_body,
            )
        except openai.OpenAIError as error:
            raise LLMError(_describe(error), fatal=isinstance(error, _FATAL)) from error
        latency = time.perf_counter() - started

        call = self._record(response, latency)
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise LLMError("model returned an empty response", call=call)
        return Completion(text=content, call=call)

    def _record(self, response: Any, latency: float) -> CallRecord:
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        reported = (usage.model_extra or {}).get("cost") if usage else None

        cost: float | None
        method: CostMethod
        if reported is not None:
            cost, method = float(reported), "reported"
        elif self._pricing is not None and usage is not None:
            cost = (
                input_tokens * self._pricing.input_per_token
                + output_tokens * self._pricing.output_per_token
            )
            method = "token_price"
        else:
            cost, method = None, "unknown"

        return CallRecord(
            model=self.model,
            provider=(response.model_extra or {}).get("provider"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cost_method=method,
            latency_s=latency,
        )


def _describe(error: openai.OpenAIError) -> str:
    status = getattr(error, "status_code", None)
    prefix = f"{type(error).__name__}" + (f" (HTTP {status})" if status else "")
    return f"{prefix}: {error}"


# --- Fake -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeRequest:
    messages: tuple[Message, ...]
    schema_name: str
    json_schema: Mapping[str, Any]
    temperature: float


class FakeLLM:
    """Returns scripted responses in order. A scripted LLMError is raised instead."""

    model = "fake/model"

    def __init__(self, responses: Iterable[str | LLMError]) -> None:
        self._responses = list(responses)
        self.requests: list[FakeRequest] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema_name: str,
        json_schema: Mapping[str, Any],
        temperature: float,
    ) -> Completion:
        self.requests.append(FakeRequest(tuple(messages), schema_name, json_schema, temperature))
        assert self._responses, "FakeLLM: no scripted response left"
        response = self._responses.pop(0)
        if isinstance(response, LLMError):
            raise response
        call = CallRecord(model=self.model, provider="fake", cost_usd=0.0, cost_method="reported")
        return Completion(text=response, call=call)
