"""Score benchmark runs: match findings to labelled issues, then compute the table metrics.

Matching rules (docs/adr/0002):
- A finding matches an issue when category, file and side are equal and its line is within
  [issue.line - tolerance, issue.last_line + tolerance].
- Findings at or above the threshold are matched highest confidence first; each claims the
  closest unclaimed issue it matches. Raising the threshold can only remove hits.
- An unmatched finding is a false alarm, including duplicates on an already-claimed issue.
  If its location is not shown in the diff it is also listed as an invalid location.
- An issue with required=False is never a hit or a miss, and a finding on it is not a
  false alarm.
- A case with an error, or with no result in a run, counts as having no findings.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from benchmark.cases import Case, Issue, Kind
from benchmark.results import CaseResult, Run
from reviewer.schemas import Category, Finding


class ScoreError(ValueError):
    """The runs don't fit the cases being scored."""


@dataclass(frozen=True)
class Ratio:
    num: float
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None


@dataclass(frozen=True)
class Spread:
    """A metric across runs. None when no run had anything to measure ("n/a")."""

    mean: float | None
    low: float | None
    high: float | None
    ns: tuple[int, ...] = ()  # denominator in each run, for "(n=8)" in the table

    @classmethod
    def of(cls, values: Sequence[float | None], ns: tuple[int, ...] = ()) -> "Spread":
        present = [value for value in values if value is not None]
        if not present:
            return cls(None, None, None, ns)
        return cls(fmean(present), min(present), max(present), ns)

    @classmethod
    def of_ratios(cls, ratios: Sequence[Ratio]) -> "Spread":
        return cls.of([ratio.value for ratio in ratios], tuple(ratio.den for ratio in ratios))


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    kind: Kind
    hits: tuple[tuple[Finding, Issue], ...]
    misses: tuple[Issue, ...]
    false_alarms: tuple[Finding, ...]
    invalid_locations: tuple[Finding, ...]
    optional_flagged: tuple[Issue, ...]
    location_hits: int  # required issues with a finding nearby, of any category
    error: str | None


@dataclass(frozen=True)
class RunScore:
    cases: tuple[CaseScore, ...]


@dataclass(frozen=True)
class ThresholdReport:
    recall: Spread
    recall_by_category: dict[Category, Spread]
    precision: Spread
    location_recall: Spread
    false_alarms_per_clean_pr: Spread
    clean_prs_with_false_alarm: Spread
    error_rate: Spread
    invalid_locations: Spread
    optional_issues: int
    optional_flagged: Spread
    runs: tuple[RunScore, ...]


@dataclass(frozen=True)
class Report:
    thresholds: dict[float, ThresholdReport]
    cost_per_pr: Spread
    latency_p50: Spread
    latency_p95: Spread


def score(cases: Sequence[Case], runs: Sequence[Run], thresholds: Sequence[float]) -> Report:
    results_per_run = [_results_by_case(cases, run) for run in runs]
    return Report(
        thresholds={
            threshold: _score_threshold(cases, results_per_run, threshold)
            for threshold in thresholds
        },
        cost_per_pr=Spread.of([_mean_cost(results) for results in results_per_run]),
        latency_p50=Spread.of([_latency_percentile(results, 50) for results in results_per_run]),
        latency_p95=Spread.of([_latency_percentile(results, 95) for results in results_per_run]),
    )


def _results_by_case(cases: Sequence[Case], run: Run) -> dict[str, CaseResult]:
    case_ids = {case.id for case in cases}
    results = {result.case_id: result for result in run.results}
    unknown = sorted(set(results) - case_ids)
    if unknown:
        raise ScoreError(f"results for cases that are not being scored: {', '.join(unknown)}")
    return {
        case.id: results.get(case.id, CaseResult(case_id=case.id, error="no result in run"))
        for case in cases
    }


def _score_threshold(
    cases: Sequence[Case], results_per_run: Sequence[dict[str, CaseResult]], threshold: float
) -> ThresholdReport:
    run_scores = [
        RunScore(tuple(_score_case(case, results[case.id], threshold) for case in cases))
        for results in results_per_run
    ]
    metrics = [_RunMetrics.of(run_score.cases) for run_score in run_scores]

    def spread(name: str) -> Spread:
        return Spread.of_ratios([getattr(m, name) for m in metrics])

    return ThresholdReport(
        recall=spread("recall"),
        recall_by_category={
            category: Spread.of_ratios([m.recall_by_category[category] for m in metrics])
            for category in Category
        },
        precision=spread("precision"),
        location_recall=spread("location_recall"),
        false_alarms_per_clean_pr=spread("false_alarms_per_clean_pr"),
        clean_prs_with_false_alarm=spread("clean_prs_with_false_alarm"),
        error_rate=spread("error_rate"),
        invalid_locations=Spread.of([m.invalid_locations for m in metrics]),
        optional_issues=sum(
            1 for case in cases for issue in case.label.issues if not issue.required
        ),
        optional_flagged=Spread.of([m.optional_flagged for m in metrics]),
        runs=tuple(run_scores),
    )


@dataclass(frozen=True)
class _RunMetrics:
    """The table metrics for one run at one threshold."""

    recall: Ratio
    recall_by_category: dict[Category, Ratio]
    precision: Ratio
    location_recall: Ratio
    false_alarms_per_clean_pr: Ratio
    clean_prs_with_false_alarm: Ratio
    error_rate: Ratio
    invalid_locations: int
    optional_flagged: int

    @classmethod
    def of(cls, scores: Sequence[CaseScore]) -> "_RunMetrics":
        required = [issue for s in scores for issue in (*s.misses, *(i for _, i in s.hits))]
        hit_issues = [issue for s in scores for _, issue in s.hits]
        false_alarms = sum(len(s.false_alarms) for s in scores)
        clean = [s for s in scores if s.kind != Kind.POSITIVE]

        def recall_for(category: Category) -> Ratio:
            return Ratio(
                sum(1 for issue in hit_issues if issue.category == category),
                sum(1 for issue in required if issue.category == category),
            )

        return cls(
            recall=Ratio(len(hit_issues), len(required)),
            recall_by_category={category: recall_for(category) for category in Category},
            precision=Ratio(len(hit_issues), len(hit_issues) + false_alarms),
            location_recall=Ratio(sum(s.location_hits for s in scores), len(required)),
            false_alarms_per_clean_pr=Ratio(sum(len(s.false_alarms) for s in clean), len(clean)),
            clean_prs_with_false_alarm=Ratio(sum(1 for s in clean if s.false_alarms), len(clean)),
            error_rate=Ratio(sum(1 for s in scores if s.error is not None), len(scores)),
            invalid_locations=sum(len(s.invalid_locations) for s in scores),
            optional_flagged=sum(len(s.optional_flagged) for s in scores),
        )


def _score_case(case: Case, result: CaseResult, threshold: float) -> CaseScore:
    findings = [f for f in result.findings if f.confidence >= threshold]
    ordered = sorted(findings, key=lambda f: (-f.confidence, f.file, f.side, f.line))

    unclaimed = list(case.label.issues)
    hits: list[tuple[Finding, Issue]] = []
    optional_flagged: list[Issue] = []
    false_alarms: list[Finding] = []
    invalid: list[Finding] = []

    for finding in ordered:
        candidates = [issue for issue in unclaimed if _matches(finding, issue)]
        if candidates:
            claimed = min(candidates, key=lambda issue: _distance(finding, issue))
            unclaimed.remove(claimed)
            if claimed.required:
                hits.append((finding, claimed))
            else:
                optional_flagged.append(claimed)
            continue
        false_alarms.append(finding)
        if finding.line not in case.diff.visible_lines(finding.file, finding.side):
            invalid.append(finding)

    required = [issue for issue in case.label.issues if issue.required]
    return CaseScore(
        case_id=case.id,
        kind=case.label.kind,
        hits=tuple(hits),
        misses=tuple(issue for issue in unclaimed if issue.required),
        false_alarms=tuple(false_alarms),
        invalid_locations=tuple(invalid),
        optional_flagged=tuple(optional_flagged),
        location_hits=sum(
            1 for issue in required if any(_near(finding, issue) for finding in findings)
        ),
        error=result.error,
    )


def _near(finding: Finding, issue: Issue) -> bool:
    """Same place, any category."""
    return (
        finding.file == issue.file
        and finding.side == issue.side
        and issue.line - issue.tolerance <= finding.line <= issue.last_line + issue.tolerance
    )


def _matches(finding: Finding, issue: Issue) -> bool:
    return finding.category == issue.category and _near(finding, issue)


def _distance(finding: Finding, issue: Issue) -> int:
    if finding.line < issue.line:
        return issue.line - finding.line
    return max(0, finding.line - issue.last_line)


def _mean_cost(results: dict[str, CaseResult]) -> float | None:
    costs = [r.cost_usd for r in results.values() if r.cost_usd is not None]
    return fmean(costs) if costs else None


def _latency_percentile(results: dict[str, CaseResult], percentile: int) -> float | None:
    """Nearest-rank percentile: honest for small samples, no interpolation."""
    latencies = sorted(r.latency_s for r in results.values() if r.latency_s is not None)
    if not latencies:
        return None
    rank = math.ceil(percentile / 100 * len(latencies))
    return latencies[max(rank, 1) - 1]
