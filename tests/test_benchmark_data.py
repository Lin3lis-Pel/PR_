"""The real benchmark data stays valid: every case loads and every case has a split."""

from pathlib import Path

from benchmark.cases import load_cases
from benchmark.splits import load_splits

BENCHMARK = Path(__file__).parent.parent / "benchmark"


def test_every_case_loads_and_has_exactly_one_split() -> None:
    cases = load_cases(BENCHMARK / "cases")
    splits = load_splits(BENCHMARK / "splits.yaml")

    assert cases, "no benchmark cases found"
    assert {case.id for case in cases} == set(splits)


def test_every_case_has_a_note() -> None:
    notes = (BENCHMARK / "NOTES.md").read_text()

    for case in load_cases(BENCHMARK / "cases"):
        assert f"**{case.id}**" in notes, f"{case.id} has no line in NOTES.md"
