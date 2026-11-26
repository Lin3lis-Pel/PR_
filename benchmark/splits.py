"""Assign benchmark cases to dev or test: stratified, seeded, append-only.

Run `uv run python -m benchmark.splits` after adding cases. Existing assignments in
benchmark/splits.yaml never change; only new cases are assigned.
"""

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

import yaml

from benchmark.cases import Case, load_cases

SEED = 2026
TEST_FRACTION = 0.4
BENCHMARK_DIR = Path(__file__).parent


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


def assign_splits(
    cases: Sequence[Case],
    existing: Mapping[str, Split],
    seed: int,
    test_fraction: float = TEST_FRACTION,
) -> dict[str, Split]:
    splits = dict(existing)
    strata: defaultdict[str, list[Case]] = defaultdict(list)
    for case in cases:
        strata[_stratum(case)].append(case)

    for stratum, members in sorted(strata.items()):
        target_test = math.floor(len(members) * test_fraction + 0.5)
        already_test = sum(1 for case in members if splits.get(case.id) == Split.TEST)
        new_ids = sorted(case.id for case in members if case.id not in splits)
        random.Random(f"{seed}-{stratum}").shuffle(new_ids)
        for case_id in new_ids:
            if already_test < target_test:
                splits[case_id] = Split.TEST
                already_test += 1
            else:
                splits[case_id] = Split.DEV
    return splits


def _stratum(case: Case) -> str:
    categories = sorted(issue.category for issue in case.label.issues if issue.required)
    return f"{case.label.kind}/{categories[0] if categories else 'none'}"


def load_splits(path: Path) -> dict[str, Split]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return {case_id: Split(split) for case_id, split in raw.items()}


def save_splits(path: Path, splits: Mapping[str, Split]) -> None:
    ordered = {case_id: str(splits[case_id]) for case_id in sorted(splits)}
    path.write_text(yaml.safe_dump(ordered, sort_keys=False))


def main() -> None:
    cases_dir = BENCHMARK_DIR / "cases"
    splits_path = BENCHMARK_DIR / "splits.yaml"
    if not cases_dir.is_dir():
        raise SystemExit(f"no cases yet: {cases_dir} does not exist")
    existing = load_splits(splits_path)
    splits = assign_splits(load_cases(cases_dir), existing, SEED)
    save_splits(splits_path, splits)
    added = len(splits) - len(existing)
    print(f"{added} new cases assigned; {len(splits)} in {splits_path.name}")


if __name__ == "__main__":
    main()
