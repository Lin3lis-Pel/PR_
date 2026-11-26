"""The benchmark runner: every case in every run gets a result, failures included."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmark.cases import Case, Kind, Label, Source
from benchmark.results import ResultsFile, load_results, results_filename, save_results
from benchmark.runner import RunnerError, check_split_allowed, git_state, run_benchmark
from reviewer.configs import ReviewConfig
from reviewer.diff import parse_diff
from reviewer.llm import FakeLLM, LLMError

DIFF = """\
diff --git a/app/a.py b/app/a.py
--- a/app/a.py
+++ b/app/a.py
@@ -1,1 +1,2 @@
 x = 1
+y = 2
"""

CONFIG = ReviewConfig(name="test", prompt="v1", max_retries=0)
NONE = json.dumps({"findings": []})
ONE = json.dumps(
    {
        "findings": [
            {
                "file": "app/a.py",
                "line": 2,
                "side": "new",
                "category": "PII_LOGGING",
                "confidence": 0.8,
                "article": "",
                "explanation": "",
                "suggested_fix": "",
            }
        ]
    }
)


def case(case_id: str) -> Case:
    label = Label(id=case_id, kind=Kind.NEGATIVE, stack="django", source=Source.SYNTHETIC)
    return Case(id=case_id, label=label, diff=parse_diff(DIFF), context_dir=None, diff_text=DIFF)


CASES = [case("neg-a"), case("neg-b")]


def test_every_case_gets_a_result_in_every_run() -> None:
    llm = FakeLLM([ONE, NONE, ONE, NONE])

    outcome = run_benchmark(CASES, config=CONFIG, llm=llm, runs=2, workers=1)

    assert len(outcome.runs) == 2
    for run in outcome.runs:
        assert [r.case_id for r in run.results] == ["neg-a", "neg-b"]
    assert outcome.stopped_early is None
    assert sum(len(r.findings) for run in outcome.runs for r in run.results) == 2


def test_results_keep_cost_latency_and_calls() -> None:
    outcome = run_benchmark(CASES[:1], config=CONFIG, llm=FakeLLM([NONE]), runs=1, workers=1)

    [result] = outcome.runs[0].results
    assert result.cost_usd == 0.0
    assert result.latency_s is not None
    assert len(result.calls) == 1


def test_a_failed_case_is_recorded_not_dropped() -> None:
    llm = FakeLLM(["garbage", NONE])

    outcome = run_benchmark(CASES, config=CONFIG, llm=llm, runs=1, workers=1)

    first, second = outcome.runs[0].results
    assert first.error is not None and "invalid output" in first.error
    assert first.findings == ()
    assert len(first.calls) == 1
    assert second.error is None


def test_fatal_error_stops_the_run_and_marks_the_rest_not_run() -> None:
    llm = FakeLLM([LLMError("bad key", fatal=True)])

    outcome = run_benchmark(CASES, config=CONFIG, llm=llm, runs=2, workers=1)

    assert outcome.stopped_early is not None and "bad key" in outcome.stopped_early
    results = [r for run in outcome.runs for r in run.results]
    assert len(results) == 4
    assert all(r.error for r in results)
    assert len(llm.requests) == 1


def test_cost_cap_stops_new_cases() -> None:
    llm = FakeLLM([NONE, NONE, NONE, NONE])

    outcome = run_benchmark(CASES, config=CONFIG, llm=llm, runs=2, workers=1, max_cost=0.0)

    assert outcome.stopped_early is not None and "cost cap" in outcome.stopped_early
    results = [r for run in outcome.runs for r in run.results]
    assert len(results) == 4
    assert results[0].error is None
    assert all(r.error and "not run" in r.error for r in results[1:])


def test_parallel_workers_keep_case_order() -> None:
    cases = [case(f"neg-{i:02d}") for i in range(8)]
    llm = FakeLLM([NONE] * 8)

    outcome = run_benchmark(cases, config=CONFIG, llm=llm, runs=1, workers=4)

    assert [r.case_id for r in outcome.runs[0].results] == [c.id for c in cases]


def test_test_split_needs_explicit_confirmation() -> None:
    check_split_allowed("dev", confirmed_test=False)
    check_split_allowed("test", confirmed_test=True)
    with pytest.raises(RunnerError, match="--i-mean-test"):
        check_split_allowed("test", confirmed_test=False)


def test_results_file_round_trips_and_is_named_by_config_model_split() -> None:
    started = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
    outcome = run_benchmark(CASES[:1], config=CONFIG, llm=FakeLLM([ONE]), runs=1, workers=1)
    results = ResultsFile(
        config="v1",
        model="xiaomi/mimo-v2.6-pro",
        provider="xiaomi",
        split="dev",
        git_commit="abc123",
        dirty=False,
        started_at=started,
        finished_at=started,
        runs=tuple(outcome.runs),
    )

    assert results_filename("v1", "xiaomi/mimo-v2.6-pro", "dev", started) == (
        "v1-xiaomi_mimo-v2.6-pro-dev-20261126T120000Z.json"
    )
    assert results.providers() == {"fake": 1}
    assert results.cost_methods() == {"reported": 1}


def test_save_and_load(tmp_path: Path) -> None:
    started = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
    results = ResultsFile(
        config="v1",
        model="m/x",
        provider=None,
        split="dev",
        git_commit=None,
        dirty=True,
        started_at=started,
        finished_at=started,
        runs=(),
    )

    path = save_results(results, tmp_path)

    assert load_results(path) == results


# --- git guard ------------------------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "reviewer").mkdir()
    (tmp_path / "reviewer" / "a.py").write_text("x = 1\n")
    return tmp_path


def test_git_state_without_commits(repo: Path) -> None:
    commit, dirty = git_state(repo)

    assert commit is None
    assert dirty


def test_git_state_clean_then_dirty(repo: Path) -> None:
    git(repo, "add", "reviewer/a.py")
    git(repo, "commit", "-q", "-m", "init")

    commit, dirty = git_state(repo)
    assert commit is not None and len(commit) == 40
    assert not dirty

    (repo / "reviewer" / "a.py").write_text("x = 2\n")
    assert git_state(repo)[1]


def test_new_results_files_do_not_make_the_tree_dirty(repo: Path) -> None:
    git(repo, "add", "reviewer/a.py")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "benchmark" / "results").mkdir(parents=True)
    (repo / "benchmark" / "results" / "r.json").write_text("{}")
    (repo / "notes.txt").write_text("unrelated")

    assert not git_state(repo)[1]
