"""Named reviewer configs. One module per version; a published config is never edited.

A config is the prompt and the pipeline. The model is not part of it: it comes from .env,
and every results file records which model and provider served the run.
"""

import importlib

from pydantic import BaseModel, ConfigDict, Field


class ReviewConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    prompt: str  # prompt set in reviewer/prompts/: <prompt>_system.md and <prompt>_user.md
    temperature: float = Field(default=0.0, ge=0.0)
    max_retries: int = Field(default=2, ge=0)


def load_config(name: str) -> ReviewConfig:
    try:
        module = importlib.import_module(f"reviewer.configs.{name}")
    except ModuleNotFoundError as error:
        raise ValueError(f"no config named {name!r} in reviewer/configs/") from error
    config: ReviewConfig = module.CONFIG
    return config
