"""The service's own settings, from the environment.

The model and provider are not here: they come from reviewer/config.py, like every other
wrapper. This file holds what only the service needs: the token callers must send, which
config it serves and how large a diff it accepts.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from reviewer.config import ENV_FILE, SettingsError

TOKEN_VAR = "GDPR_REVIEWER_TOKEN"
CONFIG_VAR = "GDPR_REVIEWER_CONFIG"
MAX_DIFF_VAR = "GDPR_REVIEWER_MAX_DIFF_BYTES"

DEFAULT_CONFIG = "v2"
DEFAULT_MAX_DIFF_BYTES = 200_000


class ServiceSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: SecretStr  # callers send it as "Authorization: Bearer <token>"
    config: str = DEFAULT_CONFIG  # name of a module in reviewer/configs/
    max_diff_bytes: int = Field(default=DEFAULT_MAX_DIFF_BYTES, gt=0)


def load_service_settings(env: Mapping[str, str]) -> ServiceSettings:
    token = env.get(TOKEN_VAR, "").strip()
    if not token:
        raise SettingsError(
            f"{TOKEN_VAR} is not set. The service refuses to start without one, so it is "
            "never open to anyone who can reach it."
        )
    config = env.get(CONFIG_VAR, "").strip() or DEFAULT_CONFIG
    raw_max = env.get(MAX_DIFF_VAR, "").strip()
    try:
        max_diff_bytes = int(raw_max) if raw_max else DEFAULT_MAX_DIFF_BYTES
    except ValueError as error:
        raise SettingsError(f"{MAX_DIFF_VAR}={raw_max!r} is not a whole number") from error
    if max_diff_bytes <= 0:
        raise SettingsError(f"{MAX_DIFF_VAR} must be positive, got {max_diff_bytes}")
    return ServiceSettings(token=SecretStr(token), config=config, max_diff_bytes=max_diff_bytes)


def service_settings_from_environment(env_file: Path = ENV_FILE) -> ServiceSettings:
    """Read .env (without overriding variables already set), then the process environment."""
    load_dotenv(env_file, override=False)
    return load_service_settings(os.environ)
