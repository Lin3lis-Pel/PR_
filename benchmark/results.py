"""Raw reviewer output for benchmark runs. Scores are always recomputed from these.

One results file per runner command (docs/adr/0001): every run's raw findings with no
threshold applied, plus the model, the upstream providers that served it, and cost.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from reviewer.schemas import CallRecord, Finding


class CaseResult(BaseModel):
    """Everything the reviewer returned for one case in one run, before any threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    findings: tuple[Finding, ...] = ()
    error: str | None = None
    cost_usd: float | None = Field(default=None, ge=0.0)
    latency_s: float | None = Field(default=None, ge=0.0)
    calls: tuple[CallRecord, ...] = ()


class Run(BaseModel):
    """One pass of a config over a set of cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[CaseResult, ...]


class ResultsFile(BaseModel):
    """What one runner command wrote: config x model x split, all runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config: str
    model: str
    provider: str | None  # provider that was pinned; None = OpenRouter chose
    split: str
    git_commit: str | None
    case_set: str | None = None  # benchmark.cases.case_set_hash of the cases run
    dirty: bool  # True: code had uncommitted changes, so the row is not publishable
    started_at: datetime
    finished_at: datetime
    stopped_early: str | None = None  # why the run stopped (cost cap, fatal error)
    runs: tuple[Run, ...]

    def calls(self) -> list[CallRecord]:
        return [call for run in self.runs for result in run.results for call in result.calls]

    def providers(self) -> dict[str, int]:
        """Upstream providers that served the calls, with counts."""
        return dict(Counter(call.provider or "unknown" for call in self.calls()))

    def cost_methods(self) -> dict[str, int]:
        return dict(Counter(call.cost_method for call in self.calls()))

    def total_cost(self) -> float:
        return sum(call.cost_usd or 0.0 for call in self.calls())


def results_filename(config: str, model: str, split: str, started_at: datetime) -> str:
    model_slug = re.sub(r"[^A-Za-z0-9.-]+", "_", model)
    return f"{config}-{model_slug}-{split}-{started_at:%Y%m%dT%H%M%SZ}.json"


def save_results(results: ResultsFile, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / results_filename(
        results.config, results.model, results.split, results.started_at
    )
    path.write_text(results.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def load_results(path: Path) -> ResultsFile:
    return ResultsFile.model_validate_json(path.read_text(encoding="utf-8"))
