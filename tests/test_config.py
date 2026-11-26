"""Settings come from the environment: switching models is an .env change, never a code change."""

import pytest

from reviewer.config import OPENROUTER_BASE_URL, SettingsError, load_settings

KEY = "sk-or-test-not-a-real-key"


def env(**overrides: str) -> dict[str, str]:
    base = {
        "OPENROUTER_API_KEY": KEY,
        "GDPR_REVIEWER_MODEL": "xiaomi/mimo-v2.6-pro",
        "GDPR_REVIEWER_PROVIDER": "xiaomi",
    }
    return {**base, **overrides}


def test_reads_model_provider_and_key() -> None:
    settings = load_settings(env())

    assert settings.model == "xiaomi/mimo-v2.6-pro"
    assert settings.provider == "xiaomi"
    assert settings.api_key.get_secret_value() == KEY
    assert settings.base_url == OPENROUTER_BASE_URL


def test_empty_provider_means_openrouter_chooses() -> None:
    assert load_settings(env(GDPR_REVIEWER_PROVIDER="")).provider is None
    no_provider = env()
    del no_provider["GDPR_REVIEWER_PROVIDER"]
    assert load_settings(no_provider).provider is None


def test_values_are_stripped() -> None:
    settings = load_settings(env(GDPR_REVIEWER_MODEL="  openai/gpt-x  "))

    assert settings.model == "openai/gpt-x"


@pytest.mark.parametrize("missing", ["OPENROUTER_API_KEY", "GDPR_REVIEWER_MODEL"])
def test_missing_required_value_names_the_variable(missing: str) -> None:
    with pytest.raises(SettingsError, match=missing):
        load_settings(env(**{missing: ""}))


def test_model_must_use_provider_slash_model_form() -> None:
    with pytest.raises(SettingsError, match="provider/model"):
        load_settings(env(GDPR_REVIEWER_MODEL="mimo-v2.6-pro"))


def test_key_never_appears_in_repr_or_errors() -> None:
    settings = load_settings(env())
    assert KEY not in repr(settings)
    assert KEY not in str(settings)

    with pytest.raises(SettingsError) as error:
        load_settings(env(GDPR_REVIEWER_MODEL="bad"))
    assert KEY not in str(error.value)
