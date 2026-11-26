"""Score a results file and print the metrics table.

    uv run python -m benchmark.report benchmark/results/<file>.json

The threshold is applied here, so one paid run gives the whole threshold curve.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from benchmark.cases import Case, Kind, case_set_hash, load_cases
from benchmark.results import ResultsFile, load_results
from benchmark.score import Ratio, Spread, ThresholdReport, score
from reviewer.configs import ReviewConfig, load_config
from reviewer.schemas import Category

BENCHMARK_DIR = Path(__file__).parent
THRESHOLDS = (0.0, 0.3, 0.5, 0.7, 0.9)
# The threshold rule: the highest threshold whose recall is within this of threshold 0.0.
RECALL_SLACK = 0.05


class ReportError(ValueError):
    """The results file can't be scored the way it was asked for."""


def pick_threshold(recall_at: dict[float, float | None]) -> float:
    """Highest threshold whose mean recall is within RECALL_SLACK of the lowest threshold's."""
    lowest = min(recall_at)
    base = recall_at[lowest]
    if base is None:
        return lowest
    allowed = [t for t, r in recall_at.items() if r is not None and r >= base - RECALL_SLACK]
    return max(allowed)


def check_same_pipeline(ran: ReviewConfig, scored_as: ReviewConfig) -> None:
    """--as-config may only change what the report applies (name, threshold)."""
    ignored = {"name", "threshold"}
    a = ran.model_dump(exclude=ignored)
    b = scored_as.model_dump(exclude=ignored)
    different = sorted(key for key in a if a[key] != b[key])
    if different:
        raise ReportError(
            f"{scored_as.name} differs from {ran.name} in {', '.join(different)}: "
            "it needs its own run"
        )


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


def format_report(
    results: ResultsFile, cases: Sequence[Case], *, config: ReviewConfig | None = None
) -> str:
    chosen = config.threshold if config is not None else 0.0
    thresholds = tuple(sorted({*THRESHOLDS, chosen}))
    report = score(cases, results.runs, thresholds)
    commit = results.git_commit[:7] if results.git_commit else "no commit"
    name = config.name if config is not None else results.config
    scored_as = f" (scored from a {results.config} run)" if name != results.config else ""
    current = case_set_hash(cases)
    case_set = results.case_set or "not recorded"
    if results.case_set is not None and results.case_set != current:
        case_set += f" (CHANGED: the cases now hash to {current})"
    rule = pick_threshold({t: r.recall.mean for t, r in report.thresholds.items()})
    lines = [
        f"# {name} x {results.model} on {results.split}{scored_as}",
        "",
        f"{len(results.runs)} runs x {len(cases)} cases; commit {commit}; case set {case_set}"
        + ("; DIRTY (uncommitted code, not publishable)" if results.dirty else "")
        + (f"; STOPPED EARLY: {results.stopped_early}" if results.stopped_early else ""),
        f"Pinned provider: {results.provider or 'none'}; served by: "
        + ", ".join(f"{name} x{count}" for name, count in results.providers().items()),
        "Cost method: "
        + ", ".join(f"{name} x{count}" for name, count in results.cost_methods().items())
        + f"; total ${results.total_cost():.4f}",
        "",
        "Rates are the mean over runs, with min-max when runs differ; n is the denominator.",
        f"Config threshold: {chosen:.1f} (row marked *). The threshold rule (highest threshold "
        f"with recall within {RECALL_SLACK * 100:.0f} points of {thresholds[0]:.1f}) picks "
        f"{rule:.1f}.",
        "",
        "| Threshold | Recall | Precision | False alarms per clean PR | Clean PRs with a false "
        "alarm | Error rate | Location-only recall | Invalid locations |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for threshold, t in report.thresholds.items():
        mark = "*" if threshold == chosen else ""
        lines.append(
            f"| {threshold:.1f}{mark} | {format_spread(t.recall, percent=True)} "
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
    lines += ["", *_by_group(base, cases)]
    lines += ["", *_per_case(base)]
    return "\n".join(lines) + "\n"


def _by_group(threshold: ThresholdReport, cases: Sequence[Case]) -> list[str]:
    """Recall (positives) and share of clean PRs with a false alarm, per difficulty and
    pattern: where a config gets better or worse, not just whether."""
    groups: dict[str, set[str]] = {}
    for case in cases:
        names = [f"difficulty: {case.label.difficulty or 'unset'}"]
        names += [f"pattern: {pattern}" for pattern in case.label.patterns]
        for name in names:
            groups.setdefault(name, set()).add(case.id)

    lines = [
        "## By difficulty and pattern (threshold 0.0)",
        "",
        "| Group | Recall | Clean PRs with a false alarm |",
        "|---|---|---|",
    ]
    for name in sorted(groups, key=lambda n: (not n.startswith("difficulty"), n)):
        ids = groups[name]
        recall, clean = [], []
        for run in threshold.runs:
            scores = [s for s in run.cases if s.case_id in ids]
            required = sum(len(s.hits) + len(s.misses) for s in scores)
            recall.append(Ratio(sum(len(s.hits) for s in scores), required))
            clean_scores = [s for s in scores if s.kind != Kind.POSITIVE]
            clean.append(Ratio(sum(1 for s in clean_scores if s.false_alarms), len(clean_scores)))
        lines.append(
            f"| {name} | {format_spread(Spread.of_ratios(recall), percent=True)} "
            f"| {format_spread(Spread.of_ratios(clean), percent=True)} |"
        )
    return lines


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
    parser.add_argument(
        "--as-config",
        help="score as another config that differs only in name and threshold (e.g. v1_1)",
    )
    args = parser.parse_args(argv)

    results = load_results(args.results)
    try:
        config = load_config(results.config)
        if args.as_config:
            scored_as = load_config(args.as_config)
            check_same_pipeline(config, scored_as)
            config = scored_as
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    wanted = {result.case_id for run in results.runs for result in run.results}
    cases = [case for case in load_cases(BENCHMARK_DIR / "cases") if case.id in wanted]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(format_report(results, cases, config=config), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
