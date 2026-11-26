"""Score a results file and print the metrics table.

    uv run python -m benchmark.report benchmark/results/<file>.json

The threshold is applied here, so one paid run gives the whole threshold curve.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from benchmark.cases import Case, load_cases
from benchmark.results import ResultsFile, load_results
from benchmark.score import Spread, ThresholdReport, score
from reviewer.schemas import Category

BENCHMARK_DIR = Path(__file__).parent
THRESHOLDS = (0.0, 0.3, 0.5, 0.7, 0.9)


def format_spread(spread: Spread, *, percent: bool) -> str:
    if spread.mean is None:
        return "n/a"

    def fmt(value: float | None) -> str:
        assert value is not None
        return f"{value * 100:.0f}%" if percent else f"{value:.2f}"

    parts: list[str] = []
    if len(spread.ns) != 1 and spread.low != spread.high:
        low, high = fmt(spread.low), fmt(spread.high)
        parts.append(f"{low.rstrip('%')}-{high}" if percent else f"{low}-{high}")
    if spread.ns:
        distinct = list(dict.fromkeys(spread.ns))
        parts.append("n=" + "/".join(str(n) for n in distinct))
    return fmt(spread.mean) + (f" ({', '.join(parts)})" if parts else "")


def _money(spread: Spread) -> str:
    return "n/a" if spread.mean is None else f"${spread.mean:.4f}"


def _seconds(spread: Spread) -> str:
    return "n/a" if spread.mean is None else f"{spread.mean:.1f}s"


def format_report(results: ResultsFile, cases: Sequence[Case]) -> str:
    report = score(cases, results.runs, THRESHOLDS)
    commit = results.git_commit[:7] if results.git_commit else "no commit"
    lines = [
        f"# {results.config} x {results.model} on {results.split}",
        "",
        f"{len(results.runs)} runs x {len(cases)} cases; commit {commit}"
        + ("; DIRTY (uncommitted code, not publishable)" if results.dirty else "")
        + (f"; STOPPED EARLY: {results.stopped_early}" if results.stopped_early else ""),
        f"Pinned provider: {results.provider or 'none'}; served by: "
        + ", ".join(f"{name} x{count}" for name, count in results.providers().items()),
        "Cost method: "
        + ", ".join(f"{name} x{count}" for name, count in results.cost_methods().items())
        + f"; total ${results.total_cost():.4f}",
        "",
        "Rates are the mean over runs, with min-max when runs differ; n is the denominator.",
        "",
        "| Threshold | Recall | Precision | False alarms per clean PR | Clean PRs with a false "
        "alarm | Error rate | Location-only recall | Invalid locations |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for threshold, t in report.thresholds.items():
        lines.append(
            f"| {threshold:.1f} | {format_spread(t.recall, percent=True)} "
            f"| {format_spread(t.precision, percent=True)} "
            f"| {format_spread(t.false_alarms_per_clean_pr, percent=False)} "
            f"| {format_spread(t.clean_prs_with_false_alarm, percent=True)} "
            f"| {format_spread(t.error_rate, percent=True)} "
            f"| {format_spread(t.location_recall, percent=True)} "
            f"| {format_spread(t.invalid_locations, percent=False)} |"
        )

    base = report.thresholds[THRESHOLDS[0]]
    lines += ["", "## Recall by category (threshold 0.0)", ""]
    lines += ["| Category | Recall |", "|---|---|"]
    for category in Category:
        lines.append(
            f"| {category} | {format_spread(base.recall_by_category[category], percent=True)} |"
        )

    lines += [
        "",
        f"Cost per PR {_money(report.cost_per_pr)}; latency p50 {_seconds(report.latency_p50)}"
        f"; p95 {_seconds(report.latency_p95)}",
        f"Optional issues: {base.optional_issues}, flagged "
        f"{format_spread(base.optional_flagged, percent=False)} per run",
    ]
    lines += ["", *_per_case(base)]
    return "\n".join(lines) + "\n"


def _per_case(threshold: ThresholdReport) -> list[str]:
    """Which cases went wrong, per run, at threshold 0.0: what to look at next."""
    lines = [
        "## Per case (threshold 0.0)",
        "",
        "| Case | Kind | " + " | ".join(f"Run {i + 1}" for i in range(len(threshold.runs))) + " |",
        "|---|---|" + "---|" * len(threshold.runs),
    ]
    if not threshold.runs:
        return lines
    for index, first in enumerate(threshold.runs[0].cases):
        cells = []
        for run in threshold.runs:
            case = run.cases[index]
            if case.error:
                cells.append("ERROR")
                continue
            parts = []
            if case.hits:
                parts.append(f"{len(case.hits)} hit")
            if case.misses:
                parts.append(f"{len(case.misses)} miss")
            if case.false_alarms:
                parts.append(f"{len(case.false_alarms)} FA")
            cells.append(", ".join(parts) or "ok")
        lines.append(f"| {first.case_id} | {first.kind} | " + " | ".join(cells) + " |")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmark.report", description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args(argv)

    results = load_results(args.results)
    wanted = {result.case_id for run in results.runs for result in run.results}
    cases = [case for case in load_cases(BENCHMARK_DIR / "cases") if case.id in wanted]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(format_report(results, cases), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
