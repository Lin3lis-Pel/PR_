"""FastAPI wrapper around review(). Diff in, findings out; it never talks to GitHub.

Run locally: uv run uvicorn service.app:app --reload
"""

import hashlib
import hmac
import logging
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, NoReturn

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from reviewer.config import settings_from_environment
from reviewer.configs import load_config
from reviewer.diff import DiffError, ParsedDiff, parse_diff
from reviewer.llm import LLM, OpenRouterLLM
from reviewer.review import ReviewError, review
from reviewer.schemas import Finding, Review, Side
from service.settings import ServiceSettings, service_settings_from_environment


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    return service_settings_from_environment()


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    return OpenRouterLLM(settings_from_environment())


Settings = Annotated[ServiceSettings, Depends(get_settings)]
Model = Annotated[LLM, Depends(get_llm)]

logger = logging.getLogger("service")

_bearer = HTTPBearer(auto_error=False)


def require_token(
    settings: Settings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """The one auth seam: a shared bearer token now, GitHub OIDC later (ADR 0006)."""
    expected = settings.token.get_secret_value().encode()
    given = credentials.credentials.encode() if credentials else b""
    if not hmac.compare_digest(given, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or wrong bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail at startup, not on the first request: no token or an unknown config stops it."""
    settings = app.dependency_overrides.get(get_settings, get_settings)()
    load_config(settings.config)
    if not logger.handlers:  # uvicorn configures only its own loggers; Cloud Run reads stdout
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    yield


app = FastAPI(title="GDPR PR Reviewer", lifespan=_lifespan)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 body echoes the input, which would put the diff in the reply."""
    detail = [
        {"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type")}
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail}
    )


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diff: str


class ShownFinding(Finding):
    """A finding at or above the config's threshold, with what the Action needs to post it."""

    postable: bool  # file, line and side are shown in the diff, so GitHub takes an inline comment
    fingerprint: str  # same risk on the same line content -> same value, even if the line moved


class ReviewResponse(BaseModel):
    findings: tuple[ShownFinding, ...]
    config: str
    model: str
    provider: str | None  # upstream provider that served the call, as OpenRouter reports it
    cost_usd: float | None
    latency_s: float


@app.get("/healthz")
def healthz(settings: Settings, llm: Model) -> dict[str, str]:
    return {"status": "ok", "config": settings.config, "model": llm.model}


@app.post("/review", dependencies=[Depends(require_token)])
def review_diff(
    request: ReviewRequest, response: Response, settings: Settings, llm: Model
) -> ReviewResponse:
    request_id = uuid.uuid4().hex
    diff_bytes = len(request.diff.encode("utf-8"))
    parsed = _check_diff(request.diff, diff_bytes, settings.max_diff_bytes)
    config = load_config(settings.config)
    try:
        result = review(request.diff, config=config, llm=llm)
    except ReviewError as error:
        # Only the error's type: its message can quote the model's output, and so the diff.
        _log(request_id, diff_bytes, llm, Review(calls=error.calls), type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="the model gave no usable answer; try again later",
            headers={"X-Request-ID": request_id},
        ) from error
    shown = tuple(
        _shown(finding, parsed)
        for finding in result.findings
        if finding.confidence >= config.threshold
    )
    _log(request_id, diff_bytes, llm, result, None, shown=len(shown))
    response.headers["X-Request-ID"] = request_id
    return ReviewResponse(
        findings=shown,
        config=config.name,
        model=llm.model,
        provider=_serving_provider(result),
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
    )


def _serving_provider(result: Review) -> str | None:
    providers = [call.provider for call in result.calls if call.provider]
    return providers[-1] if providers else None


def _log(
    request_id: str,
    diff_bytes: int,
    llm: LLM,
    result: Review,
    error: str | None,
    *,
    shown: int = 0,
) -> None:
    """One line per review, metadata only: never the diff, finding text or the token."""
    logger.info(
        "review request_id=%s diff_bytes=%d findings=%d calls=%d cost_usd=%s latency_s=%.2f "
        "model=%s provider=%s error=%s",
        request_id,
        diff_bytes,
        shown,
        len(result.calls),
        result.cost_usd,
        result.latency_s,
        llm.model,
        _serving_provider(result),
        error,
    )


def _shown(finding: Finding, parsed: ParsedDiff) -> ShownFinding:
    text = parsed.line_text(finding.file, finding.side, finding.line)
    # Keyed by the line's content, so a later push that only moves the line keeps the same
    # fingerprint. With no content to go by, the line number is the best key there is.
    where = f"text:{text.strip()}" if text is not None else f"line:{finding.line}"
    key = "\0".join((finding.file, finding.category.value, where))
    return ShownFinding(
        **finding.model_dump(),
        postable=text is not None,
        fingerprint=hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
    )


def _check_diff(diff: str, size: int, max_bytes: int) -> ParsedDiff:
    """Refuse before any model call: an oversized, empty or unparseable diff costs nothing."""
    if size > max_bytes:
        _unprocessable(f"diff is {size} bytes; the limit is {max_bytes}")
    try:
        parsed = parse_diff(diff)
    except DiffError:
        _unprocessable("not a unified diff")
    if not any(parsed.visible_lines(file, side) for file in parsed.files for side in Side):
        _unprocessable("the diff shows no lines")
    return parsed


def _unprocessable(detail: str) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
