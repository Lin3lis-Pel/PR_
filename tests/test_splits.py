"""assign_splits: a stratified dev/test split that never moves an assigned case."""

from collections import Counter

from benchmark.cases import Case, Issue, Kind, Label, Source
from benchmark.splits import Split, assign_splits
from reviewer.diff import parse_diff
from reviewer.schemas import Category

EMPTY_DIFF = parse_diff("")


def positive(case_id: str, category: Category = Category.PII_LOGGING) -> Case:
    issue = Issue(category=category, file="app/a.py", line=1)
    label = Label(
        id=case_id, kind=Kind.POSITIVE, stack="go", source=Source.SYNTHETIC, issues=(issue,)
    )
    return Case(id=case_id, label=label, diff=EMPTY_DIFF, context_dir=None)


def negative(case_id: str) -> Case:
    label = Label(id=case_id, kind=Kind.NEGATIVE, stack="go", source=Source.SYNTHETIC)
    return Case(id=case_id, label=label, diff=EMPTY_DIFF, context_dir=None)


def counts(splits: dict[str, Split], ids: list[str]) -> Counter[Split]:
    return Counter(splits[case_id] for case_id in ids)


def test_forty_percent_of_a_stratum_goes_to_test() -> None:
    cases = [negative(f"n{i}") for i in range(10)]

    splits = assign_splits(cases, existing={}, seed=1)

    assert counts(splits, [c.id for c in cases]) == {Split.TEST: 4, Split.DEV: 6}


def test_each_kind_and_category_is_split_separately() -> None:
    logging = [positive(f"pl{i}") for i in range(5)]
    storage = [positive(f"ps{i}", Category.PII_STORAGE) for i in range(5)]
    clean = [negative(f"n{i}") for i in range(5)]

    splits = assign_splits(logging + storage + clean, existing={}, seed=1)

    for group in (logging, storage, clean):
        assert counts(splits, [c.id for c in group]) == {Split.TEST: 2, Split.DEV: 3}


def test_same_seed_gives_the_same_split() -> None:
    cases = [negative(f"n{i}") for i in range(10)]

    assert assign_splits(cases, {}, seed=7) == assign_splits(list(reversed(cases)), {}, seed=7)


def test_existing_assignments_never_move() -> None:
    old = [negative(f"old{i}") for i in range(4)]
    new = [negative(f"new{i}") for i in range(6)]
    existing = {case.id: Split.TEST for case in old}

    splits = assign_splits(old + new, existing=existing, seed=1)

    assert all(splits[case.id] == Split.TEST for case in old)
    assert counts(splits, [c.id for c in new]) == {Split.DEV: 6}  # stratum already has its 4


def test_assignments_for_cases_no_longer_present_are_kept() -> None:
    splits = assign_splits([negative("n1")], existing={"gone": Split.TEST}, seed=1)

    assert splits["gone"] == Split.TEST
