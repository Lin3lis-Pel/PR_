"""score: matches findings to labelled issues and turns runs into the results-table metrics."""

import pytest

from benchmark.cases import Case, Issue, Kind, Label, Source
from benchmark.results import CaseResult, Run
from benchmark.score import ScoreError, score
from reviewer.diff import parse_diff
from reviewer.schemas import Category, Finding, Side

FILE = "app/a.py"
LOGGING = Category.PII_LOGGING
STORAGE = Category.PII_STORAGE


def new_file_diff(path: str = FILE, lines: int = 40) -> str:
    body = "".join(f"+line {n}\n" for n in range(1, lines + 1))
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{lines} @@\n{body}"
    )


def issue(line: int, category: Category = LOGGING, **extra: object) -> Issue:
    return Issue(category=category, file=FILE, line=line, **extra)


def case(case_id: str, kind: Kind, *issues: Issue) -> Case:
    label = Label(id=case_id, kind=kind, stack="django", source=Source.SYNTHETIC, issues=issues)
    return Case(id=case_id, label=label, diff=parse_diff(new_file_diff()), context_dir=None)


def finding(
    line: int, category: Category = LOGGING, confidence: float = 0.9, **extra: object
) -> Finding:
    return Finding(file=FILE, line=line, category=category, confidence=confidence, **extra)


def run(**findings_by_case: list[Finding]) -> Run:
    return Run(
        results=tuple(
            CaseResult(case_id=case_id, findings=tuple(found))
            for case_id, found in findings_by_case.items()
        )
    )


def single(cases: list[Case], one_run: Run, threshold: float = 0.0):
    """Score one run at one threshold and return that threshold's report."""
    return score(cases, [one_run], [threshold]).thresholds[threshold]


def case_score(report, case_id: str):
    [run_score] = report.runs
    return next(c for c in run_score.cases if c.case_id == case_id)


# --- matching -----------------------------------------------------------------------------


def test_exact_hit_gives_full_recall_and_precision() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]

    report = single(cases, run(p1=[finding(10)]))

    assert report.recall.mean == 1.0
    assert report.precision.mean == 1.0


def test_hit_within_tolerance_but_not_beyond() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10)), case("p2", Kind.POSITIVE, issue(10))]

    report = single(cases, run(p1=[finding(13)], p2=[finding(14)]))

    assert len(case_score(report, "p1").hits) == 1
    p2 = case_score(report, "p2")
    assert len(p2.misses) == 1
    assert len(p2.false_alarms) == 1


def test_range_issue_matches_anywhere_inside_it_plus_tolerance() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10, end_line=20))]

    report = single(cases, run(p1=[finding(22)]))

    assert report.recall.mean == 1.0


def test_duplicate_finding_counts_as_false_alarm() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]

    report = single(cases, run(p1=[finding(10), finding(11)]))

    p1 = case_score(report, "p1")
    assert len(p1.hits) == 1
    assert len(p1.false_alarms) == 1
    assert report.precision.mean == 0.5


def test_wrong_category_is_a_miss_and_a_false_alarm_but_a_location_hit() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10, category=LOGGING))]

    report = single(cases, run(p1=[finding(10, category=STORAGE)]))

    assert report.recall.mean == 0.0
    assert report.precision.mean == 0.0
    assert report.location_recall.mean == 1.0


def test_finding_on_the_other_side_does_not_match() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]

    report = single(cases, run(p1=[finding(10, side=Side.OLD)]))

    assert report.recall.mean == 0.0


def test_highest_confidence_finding_claims_the_issue() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]
    weak, strong = finding(10, confidence=0.5), finding(12, confidence=0.9)

    report = single(cases, run(p1=[weak, strong]))

    p1 = case_score(report, "p1")
    assert [hit_finding for hit_finding, _ in p1.hits] == [strong]
    assert p1.false_alarms == (weak,)


def test_findings_below_threshold_are_dropped() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]
    one_run = run(p1=[finding(10, confidence=0.9), finding(30, confidence=0.4)])

    report = score(cases, [one_run], [0.0, 0.5])

    assert report.thresholds[0.0].precision.mean == 0.5
    assert report.thresholds[0.5].precision.mean == 1.0


def test_optional_issue_is_neither_hit_miss_nor_false_alarm() -> None:
    cases = [
        case("t1", Kind.TRICKY, issue(10, required=False)),
        case("t2", Kind.TRICKY, issue(10, required=False)),
    ]

    report = single(cases, run(t1=[finding(10)], t2=[]))

    assert report.false_alarms_per_clean_pr.mean == 0.0
    assert report.recall.mean is None
    assert report.optional_issues == 2
    assert report.optional_flagged.mean == 1.0


# --- false alarms on clean PRs --------------------------------------------------------------


def test_false_alarms_on_clean_prs_are_averaged_and_counted() -> None:
    cases = [
        case("n1", Kind.NEGATIVE),
        case("n2", Kind.NEGATIVE),
        case("t1", Kind.TRICKY),
        case("p1", Kind.POSITIVE, issue(10)),
    ]
    one_run = run(n1=[finding(5), finding(6)], n2=[], t1=[finding(7)], p1=[finding(30)])

    report = single(cases, one_run)

    assert report.false_alarms_per_clean_pr.mean == 1.0  # 3 false alarms / 3 clean PRs
    assert report.clean_prs_with_false_alarm.mean == pytest.approx(2 / 3)
    assert report.false_alarms_per_clean_pr.ns == (3,)


def test_finding_outside_the_diff_is_an_invalid_false_alarm() -> None:
    cases = [case("n1", Kind.NEGATIVE)]
    outside = Finding(file="app/ghost.py", line=1, category=LOGGING, confidence=0.9)

    report = single(cases, run(n1=[outside, finding(99)]))

    n1 = case_score(report, "n1")
    assert len(n1.false_alarms) == 2
    assert len(n1.invalid_locations) == 2
    assert report.invalid_locations.mean == 2.0


# --- errors and n/a ---------------------------------------------------------------------------


def test_failed_case_counts_as_no_findings_and_raises_error_rate() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10)), case("p2", Kind.POSITIVE, issue(10))]
    one_run = Run(
        results=(
            CaseResult(case_id="p1", findings=(finding(10),)),
            CaseResult(case_id="p2", error="timeout"),
        )
    )

    report = single(cases, one_run)

    assert report.recall.mean == 0.5
    assert report.error_rate.mean == 0.5
    assert case_score(report, "p2").error == "timeout"


def test_case_missing_from_a_run_counts_as_an_error() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]

    report = single(cases, run())

    assert report.error_rate.mean == 1.0
    assert report.recall.mean == 0.0


def test_result_for_an_unknown_case_is_rejected() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10))]

    with pytest.raises(ScoreError, match="ghost"):
        score(cases, [run(p1=[], ghost=[])], [0.0])


def test_metrics_with_nothing_to_measure_are_none() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10, category=LOGGING))]

    report = single(cases, run(p1=[]))

    assert report.recall_by_category[LOGGING].mean == 0.0
    assert report.recall_by_category[STORAGE].mean is None
    assert report.precision.mean is None
    assert report.false_alarms_per_clean_pr.mean is None


# --- several runs, cost and latency -----------------------------------------------------------


def test_spread_across_runs_gives_mean_min_and_max() -> None:
    cases = [case("p1", Kind.POSITIVE, issue(10), issue(20), issue(30), issue(40))]
    runs = [
        run(p1=[finding(10), finding(20), finding(30)]),
        run(p1=[finding(10), finding(20)]),
        run(p1=[finding(10)]),
    ]

    recall = score(cases, runs, [0.0]).thresholds[0.0].recall

    assert recall.mean == pytest.approx(0.5)
    assert (recall.low, recall.high) == (0.25, 0.75)
    assert recall.ns == (4, 4, 4)


def test_cost_and_latency_are_reported_per_pr() -> None:
    cases = [case(f"n{i}", Kind.NEGATIVE) for i in range(1, 5)]
    one_run = Run(
        results=tuple(
            CaseResult(case_id=f"n{i}", cost_usd=0.01 * i, latency_s=float(i)) for i in range(1, 5)
        )
    )

    report = score(cases, [one_run], [0.0])

    assert report.cost_per_pr.mean == pytest.approx(0.025)
    assert report.latency_p50.mean == 2.0
    assert report.latency_p95.mean == 4.0
