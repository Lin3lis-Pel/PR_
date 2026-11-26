"""Run a reviewer config over a benchmark split and save the raw results.

    uv run python -m benchmark.runner --config v1 --split dev --runs 3

The model comes from .env (GDPR_REVIEWER_MODEL, GDPR_REVIEWER_PROVIDER). Results go to
benchmark/results/<config>-<model>-<split>-<timestamp>.json; score them with
`uv run python -m benchmark.report <file>`.
"""

import argparse
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmark.cases import Case, load_cases
from benchmark.results import CaseResult, ResultsFile, Run, save_results
from benchmark.splits import load_splits
from reviewer.config import SettingsError, settings_from_environment
from reviewer.configs import ReviewConfig, load_config
from reviewer.llm import LLM, LLMError, OpenRouterLLM
from reviewer.review import ReviewError, review

BENCHMARK_DIR = Path(__file__).parent
REPO_ROOT = BENCHMARK_DIR.parent
RESULTS_DIR = BENCHMARK_DIR / "results"

# Changes here make a run unreproducible from its commit. Results files are excluded.
_CODE_PATHS = ("reviewer", "benchmark", "pyproject.toml", "uv.lock", ":!benchmark/results")


class RunnerError(Exception):
    """The run can't start: refuse before spending anything."""


@dataclass(frozen=True)
class Outcome:
    runs: tuple[Run, ...]
    stopped_early: str | None


Progress = Callable[[int, CaseResult], None]


def run_benchmark(
    cases: Sequence[Case],
    *,
    config: ReviewConfig,
    llm: LLM,
    runs: int,
    workers: int = 4,
    max_cost: float | None = None,
    on_result: Progress | None = None,
) -> Outcome:
    """Review every case `runs` times. Every case gets a result in every run, even when the
    run stops early (fatal error, cost cap): those count as failed, never dropped."""
    lock = threading.Lock()
    state = {"spent": 0.0, "stop": None}

    def one(run_index: int, case: Case) -> CaseResult:
        with lock:
            stop = state["stop"]
        if stop is not None:
            return CaseResult(case_id=case.id, error=f"not run: {stop}")

        result = _review_case(case, config, llm)

        with lock:
            state["spent"] += result.cost_usd or 0.0
            fatal = result.error is not None and result.error.startswith("fatal:")
            if fatal and state["stop"] is None:
                state["stop"] = f"fatal error on {case.id}: {result.error}"
            elif max_cost is not None and state["spent"] >= max_cost and state["stop"] is None:
                state["stop"] = f"cost cap ${max_cost:.2f} reached (${state['spent']:.4f})"
        if on_result is not None:
            on_result(run_index, result)
        return result

    all_runs: list[Run] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for run_index in range(runs):
            results = list(pool.map(lambda c, r=run_index: one(r, c), cases))
            all_runs.append(Run(results=tuple(results)))
    return Outcome(runs=tuple(all_runs), stopped_early=state["stop"])


def _review_case(case: Case, config: ReviewConfig, llm: LLM) -> CaseResult:
    try:
        result = review(case.diff_text, case.context_dir, config=config, llm=llm)
    except ReviewError as error:
        costs = [call.cost_usd for call in error.calls if call.cost_usd is not None]
        return CaseResult(
            case_id=case.id,
            error=f"fatal: {error}" if error.fatal else str(error),
            cost_usd=sum(costs) if costs else None,
            latency_s=sum(call.latency_s for call in error.calls) if error.calls else None,
            calls=error.calls,
        )
    return CaseResult(
        case_id=case.id,
        findings=result.findings,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        calls=result.calls,
    )


def check_split_allowed(split: str, *, confirmed_test: bool) -> None:
    if split == "test" and not confirmed_test:
        raise RunnerError(
            "the test split is held out: run it only for a version's final row, when the "
            "user asks. Pass --i-mean-test to confirm."
        )


def git_state(repo: Path = REPO_ROOT) -> tuple[str | None, bool]:
    """(HEAD commit or None, whether the code that affects results has uncommitted changes)."""
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
    )
    commit = head.stdout.strip() or None
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", *_CODE_PATHS],
        capture_output=True,
        text=True,
        check=True,
    )
    return commit, commit is None or bool(status.stdout.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmark.runner", description=__doc__)
    parser.add_argument("--config", required=True, help="config name in reviewer/configs/")
    parser.add_argument("--split", required=True, choices=["dev", "test"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-cost", type=float, default=2.0, help="stop at this USD total")
    parser.add_argument("--case", action="append", help="only these case ids (smoke tests)")
    parser.add_argument("--i-mean-test", action="store_true", help="confirm a test-split run")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run with uncommitted code; the results are marked dirty and not publishable",
    )
    args = parser.parse_args(argv)

    try:
        check_split_allowed(args.split, confirmed_test=args.i_mean_test)
        config = load_config(args.config)
        commit, dirty = git_state()
        if dirty and not args.allow_dirty:
            raise RunnerError(
                "code in reviewer/ or benchmark/ has uncommitted changes (or nothing is "
                "committed yet), so the results could not be reproduced from a commit. "
                "Commit first, or pass --allow-dirty for a trial run."
            )
        cases = _select_cases(args.split, args.case)
        settings = settings_from_environment()
        llm = OpenRouterLLM(settings)
        llm.preflight()
    except (RunnerError, SettingsError, LLMError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    total = len(cases) * args.runs
    done = 0
    print(
        f"{config.name} x {settings.model} (provider: {settings.provider or 'any'}) on "
        f"{args.split}: {len(cases)} cases x {args.runs} runs, cap ${args.max_cost:.2f}"
    )

    def progress(run_index: int, result: CaseResult) -> None:
        nonlocal done
        done += 1
        status = f"error: {result.error}" if result.error else f"{len(result.findings)} findings"
        print(f"  [{done}/{total}] run {run_index + 1} {result.case_id}: {status}", flush=True)

    outcome = run_benchmark(
        cases,
        config=config,
        llm=llm,
        runs=args.runs,
        workers=args.workers,
        max_cost=args.max_cost,
        on_result=progress,
    )
    results = ResultsFile(
        config=config.name,
        model=settings.model,
        provider=settings.provider,
        split=args.split if not args.case else f"{args.split}-subset",
        git_commit=commit,
        dirty=dirty,
        started_at=started,
        finished_at=datetime.now(UTC),
        stopped_early=outcome.stopped_early,
        runs=outcome.runs,
    )
    path = save_results(results, RESULTS_DIR)

    print(f"\nsaved {path.relative_to(REPO_ROOT)}")
    print(f"cost ${results.total_cost():.4f} ({results.cost_methods()})")
    print(f"providers {results.providers()}")
    if outcome.stopped_early:
        print(f"STOPPED EARLY: {outcome.stopped_early}")
    if dirty:
        print("DIRTY: uncommitted code; this row is not publishable")
    print(f"score it: uv run python -m benchmark.report {path.relative_to(REPO_ROOT)}")
    return 1 if outcome.stopped_early else 0


def _select_cases(split: str, only: Sequence[str] | None) -> list[Case]:
    splits = load_splits(BENCHMARK_DIR / "splits.yaml")
    cases = [c for c in load_cases(BENCHMARK_DIR / "cases") if splits.get(c.id) == split]
    if only:
        unknown = sorted(set(only) - {c.id for c in cases})
        if unknown:
            raise RunnerError(f"not in the {split} split: {', '.join(unknown)}")
        cases = [c for c in cases if c.id in set(only)]
    if not cases:
        raise RunnerError(f"no cases in the {split} split")
    return cases


if __name__ == "__main__":
    raise SystemExit(main())
