"""The LLM seam: the fake adapter for tests, and the OpenRouter adapter against a stub client."""

from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from reviewer.config import load_settings
from reviewer.llm import FakeLLM, LLMError, Message, OpenRouterLLM

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
MESSAGES = [Message(role="system", content="sys"), Message(role="user", content="diff")]


def settings(provider: str = "xiaomi"):
    return load_settings(
        {
            "OPENROUTER_API_KEY": "sk-or-test",
            "GDPR_REVIEWER_MODEL": "xiaomi/mimo-v2.6-pro",
            "GDPR_REVIEWER_PROVIDER": provider,
        }
    )


# --- fake adapter ---------------------------------------------------------------------


def test_fake_returns_scripted_responses_in_order_and_records_requests() -> None:
    llm = FakeLLM(['{"a": 1}', '{"b": 2}'])

    first = llm.complete(MESSAGES, schema_name="out", json_schema=SCHEMA, temperature=0.0)
    second = llm.complete(MESSAGES, schema_name="out", json_schema=SCHEMA, temperature=0.0)

    assert (first.text, second.text) == ('{"a": 1}', '{"b": 2}')
    assert len(llm.requests) == 2
    assert llm.requests[0].messages == tuple(MESSAGES)
    assert llm.requests[0].temperature == 0.0


def test_fake_raises_scripted_errors() -> None:
    llm = FakeLLM([LLMError("upstream down")])

    with pytest.raises(LLMError, match="upstream down"):
        llm.complete(MESSAGES, schema_name="out", json_schema=SCHEMA, temperature=0.0)


def test_fake_fails_loudly_when_script_runs_out() -> None:
    llm = FakeLLM([])

    with pytest.raises(AssertionError, match="no scripted response"):
        llm.complete(MESSAGES, schema_name="out", json_schema=SCHEMA, temperature=0.0)


# --- OpenRouter adapter ---------------------------------------------------------------


class StubCompletions:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return ChatCompletion.model_validate(self.response)


class StubModels:
    def __init__(self, models: list[dict[str, Any]]) -> None:
        self.models = models

    def list(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=m["id"], model_extra=m) for m in self.models]


def stub_client(response: dict[str, Any] | Exception, models: list[dict[str, Any]] | None = None):
    completions = StubCompletions(response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions), models=StubModels(models or [])
    )
    return client, completions


def completion(usage: dict[str, Any], provider: str | None = "Xiaomi") -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "xiaomi/mimo-v2.6-pro",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"findings": []}'},
            }
        ],
        "usage": usage,
    }
    if provider is not None:
        body["provider"] = provider
    return body


REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}


def test_sends_model_temperature_schema_and_pinned_provider() -> None:
    client, completions = stub_client(completion({**USAGE, "cost": 0.0004}))
    llm = OpenRouterLLM(settings(), client=client)

    llm.complete(MESSAGES, schema_name="review", json_schema=SCHEMA, temperature=0.0)

    sent = completions.kwargs
    assert sent["model"] == "xiaomi/mimo-v2.6-pro"
    assert sent["temperature"] == 0.0
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "diff"},
    ]
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "review", "strict": True, "schema": SCHEMA},
    }
    assert sent["extra_body"]["provider"] == {"order": ["xiaomi"], "allow_fallbacks": False}
    assert sent["extra_body"]["usage"] == {"include": True}


def test_without_provider_openrouter_chooses() -> None:
    client, completions = stub_client(completion({**USAGE, "cost": 0.0004}))
    llm = OpenRouterLLM(settings(provider=""), client=client)

    llm.complete(MESSAGES, schema_name="review", json_schema=SCHEMA, temperature=0.0)

    assert "provider" not in completions.kwargs["extra_body"]


def test_records_reported_cost_tokens_provider_and_latency() -> None:
    client, _ = stub_client(completion({**USAGE, "cost": 0.0004}))
    llm = OpenRouterLLM(settings(), client=client)

    result = llm.complete(MESSAGES, schema_name="review", json_schema=SCHEMA, temperature=0.0)

    assert result.text == '{"findings": []}'
    call = result.call
    assert call.model == "xiaomi/mimo-v2.6-pro"
    assert call.provider == "Xiaomi"
    assert (call.input_tokens, call.output_tokens) == (1000, 200)
    assert call.cost_usd == pytest.approx(0.0004)
    assert call.cost_method == "reported"
    assert call.latency_s >= 0.0


def test_falls_back_to_listed_token_price_when_cost_is_not_reported() -> None:
    models = [
        {"id": "xiaomi/mimo-v2.6-pro", "pricing": {"prompt": "0.0000005", "completion": "0.000001"}}
    ]
    client, _ = stub_client(completion(USAGE), models)
    llm = OpenRouterLLM(settings(), client=client)
    llm.preflight()

    call = llm.complete(MESSAGES, schema_name="r", json_schema=SCHEMA, temperature=0.0).call

    assert call.cost_usd == pytest.approx(1000 * 0.0000005 + 200 * 0.000001)
    assert call.cost_method == "token_price"


def test_cost_unknown_when_neither_reported_nor_priced() -> None:
    client, _ = stub_client(completion(USAGE, provider=None))
    llm = OpenRouterLLM(settings(), client=client)

    call = llm.complete(MESSAGES, schema_name="r", json_schema=SCHEMA, temperature=0.0).call

    assert call.cost_usd is None
    assert call.cost_method == "unknown"
    assert call.provider is None


def test_preflight_rejects_a_model_openrouter_does_not_list() -> None:
    client, _ = stub_client(completion(USAGE), [{"id": "other/model", "pricing": {}}])
    llm = OpenRouterLLM(settings(), client=client)

    with pytest.raises(LLMError, match=r"xiaomi/mimo-v2\.6-pro.*not listed"):
        llm.preflight()


def test_sdk_errors_become_llm_errors() -> None:
    error = openai.APIConnectionError(request=REQUEST)
    client, _ = stub_client(error)
    llm = OpenRouterLLM(settings(), client=client)

    with pytest.raises(LLMError, match="APIConnectionError") as raised:
        llm.complete(MESSAGES, schema_name="r", json_schema=SCHEMA, temperature=0.0)
    assert not raised.value.fatal


def test_bad_key_is_fatal_so_nobody_retries_it() -> None:
    response = httpx2.Response(401, request=REQUEST)
    error = openai.AuthenticationError("bad key", response=response, body=None)
    client, _ = stub_client(error)
    llm = OpenRouterLLM(settings(), client=client)

    with pytest.raises(LLMError) as raised:
        llm.complete(MESSAGES, schema_name="r", json_schema=SCHEMA, temperature=0.0)
    assert raised.value.fatal


def test_empty_content_is_an_llm_error() -> None:
    body = completion({**USAGE, "cost": 0.0})
    body["choices"][0]["message"]["content"] = None
    client, _ = stub_client(body)
    llm = OpenRouterLLM(settings(), client=client)

    with pytest.raises(LLMError, match="empty"):
        llm.complete(MESSAGES, schema_name="r", json_schema=SCHEMA, temperature=0.0)
