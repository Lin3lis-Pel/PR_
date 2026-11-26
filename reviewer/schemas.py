"""Types the reviewer produces. The benchmark imports these; they never import the benchmark."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    PII_STORAGE = "PII_STORAGE"
    PII_LOGGING = "PII_LOGGING"
    THIRD_PARTY_TRANSFER = "THIRD_PARTY_TRANSFER"
    ERASURE_REGRESSION = "ERASURE_REGRESSION"


class Side(StrEnum):
    """Which version of a file a line number refers to. GitHub calls these RIGHT and LEFT."""

    NEW = "new"
    OLD = "old"


class Finding(BaseModel):
    """One risk the reviewer reports at one line of the diff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    line: int = Field(ge=1)
    side: Side = Side.NEW
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    article: str = ""
    explanation: str = ""
    suggested_fix: str = ""


CostMethod = Literal["reported", "token_price", "unknown"]


class CallRecord(BaseModel):
    """One LLM request: what it cost, how long it took, and who served it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    provider: str | None  # upstream provider that served the call, as OpenRouter reports it
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    cost_method: CostMethod = "unknown"
    latency_s: float = Field(default=0.0, ge=0.0)
    error: str | None = None  # set when this attempt's output was rejected


class Review(BaseModel):
    """What review() returns: the findings, plus a record of every LLM call it made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...] = ()
    calls: tuple[CallRecord, ...] = ()

    @property
    def cost_usd(self) -> float | None:
        costs = [call.cost_usd for call in self.calls if call.cost_usd is not None]
        return sum(costs) if costs else None

    @property
    def latency_s(self) -> float:
        return sum(call.latency_s for call in self.calls)
