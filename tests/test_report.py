"""benchmark.report renders a results file as the metrics table. n/a is never 0%."""

import json
from datetime import UTC, datetime

from benchmark.cases import Case, Issue, Kind, Label, Source
from benchmark.report import format_report, format_spread
from benchmark.results import CaseResult, ResultsFile, Run
from benchmark.score import Spread
from reviewer.diff import parse_diff
from reviewer.schemas import Category, Finding

DIFF = """\
diff --git a/app/a.py b/app/a.py
--- a/app/a.py
+++ b/app/a.py
@@ -1,1 +1,2 @@
 x = 1
+log(user.email)
"""


def test_spread_formats() -> None:
    assert format_spread(Spread(None, None, None, (0,)), percent=True) == "n/a"
    assert format_spread(Spread(0.5, 0.5, 0.5, (4,)), percent=True) == "50% (n=4)"
    assert format_spread(Spread(0.5, 0.25, 0.75, (4, 4)), percent=True) == "50% (25-75%, n=4)"
    assert format_spread(Spread(0.5, 0.5, 0.5, (4, 2)), percent=True) == "50% (n=4/2)"
    assert format_spread(Spread(1.5, 1.0, 2.0), percent=False) == "1.50 (1.00-2.00)"


def test_report_shows_identity_metrics_and_per_case_detail() -> None:
    positive = Case(
        id="pos-x",
        label=Label(
            id="pos-x",
            kind=Kind.POSITIVE,
            stack="django",
            source=Source.SYNTHETIC,
            issues=(Issue(category=Category.PII_LOGGING, file="app/a.py", line=2),),
        ),
        diff=parse_diff(DIFF),
        context_dir=None,
        diff_text=DIFF,
    )
    hit = Finding(file="app/a.py", line=2, category=Category.PII_LOGGING, confidence=0.9)
    started = datetime(2026, 11, 26, tzinfo=UTC)
    results = ResultsFile(
        config="v1",
        model="xiaomi/mimo-v2.6-pro",
        provider="xiaomi",
        split="dev",
        git_commit="0123456789abcdef0123456789abcdef01234567",
        dirty=True,
        started_at=started,
        finished_at=started,
        runs=(
            Run(
                results=(
                    CaseResult(case_id="pos-x", findings=(hit,), cost_usd=0.001, latency_s=2.0),
                )
            ),
        ),
    )

    text = format_report(results, [positive])

    assert "v1 x xiaomi/mimo-v2.6-pro" in text
    assert "0123456" in text
    assert "DIRTY" in text
    assert "| 0.0 | 100% (n=1)" in text
    assert "False alarms per clean PR" in text
    assert "n/a" in text  # no clean PRs here
    assert "pos-x" in text
    json.dumps(text)  # plain text, no objects leaking into the output
