"""Settings from the environment. The model is chosen in .env, never in code.

Switching models means editing GDPR_REVIEWER_MODEL (and GDPR_REVIEWER_PROVIDER) in .env.
Results files record the model and provider actually used, so every row stays reproducible.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

KEY_VAR = "OPENROUTER_API_KEY"
MODEL_VAR = "GDPR_REVIEWER_MODEL"
PROVIDER_VAR = "GDPR_REVIEWER_PROVIDER"

ENV_FILE = Path(__file__).parent.parent / ".env"


class SettingsError(ValueError):
    """The environment doesn't say which model to use, or how to reach it."""


class ModelSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str  # OpenRouter's provider/model form
    provider: str | None  # upstream provider to pin; None lets OpenRouter choose
    api_key: SecretStr
    base_url: str = OPENROUTER_BASE_URL


def load_settings(env: Mapping[str, str]) -> ModelSettings:
    key = env.get(KEY_VAR, "").strip()
    model = env.get(MODEL_VAR, "").strip()
    provider = env.get(PROVIDER_VAR, "").strip() or None

    for name, value in ((KEY_VAR, key), (MODEL_VAR, model)):
        if not value:
            raise SettingsError(f"{name} is not set. Add it to .env (see .env.example).")
    if "/" not in model:
        raise SettingsError(
            f"{MODEL_VAR}={model!r} is not in OpenRouter's provider/model form, "
            "e.g. xiaomi/mimo-v2.6-pro"
        )
    return ModelSettings(model=model, provider=provider, api_key=SecretStr(key))


def settings_from_environment(env_file: Path = ENV_FILE) -> ModelSettings:
    """Read .env (without overriding variables already set), then the process environment."""
    load_dotenv(env_file, override=False)
    return load_settings(os.environ)
