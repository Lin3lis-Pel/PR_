"""load_cases: reads benchmark/cases/<id>/{pr.diff,label.yaml} and rejects bad labels."""

from pathlib import Path

import pytest

from benchmark.cases import CaseError, Kind, load_cases
from reviewer.schemas import Category, Side

DIFF = """\
diff --git a/app/views.py b/app/views.py
index 1111111..2222222 100644
--- a/app/views.py
+++ b/app/views.py
@@ -10,5 +10,6 @@ def signup(request):
     user = create_user(form)
-    anonymise_later(user)
+    logger.info("signup %s", user.email)
+    analytics.track("signup", {"email": user.email})
     return redirect("home")
 # end
 # end 2
"""

POSITIVE_LABEL = """\
id: pos-logging-001
kind: positive
stack: django
source: synthetic
issues:
  - category: PII_LOGGING
    file: app/views.py
    line: 11
"""


def write_case(root: Path, folder: str, label: str, diff: str | None = DIFF) -> Path:
    case_dir = root / folder
    case_dir.mkdir(parents=True)
    (case_dir / "label.yaml").write_text(label)
    if diff is not None:
        (case_dir / "pr.diff").write_text(diff)
    return case_dir


def label_with_issue(issue_yaml: str, kind: str = "positive") -> str:
    return f"id: case-1\nkind: {kind}\nstack: django\nsource: synthetic\nissues:\n{issue_yaml}"


def test_loads_a_valid_case_with_defaults(tmp_path: Path) -> None:
    write_case(tmp_path, "pos-logging-001", POSITIVE_LABEL)

    [case] = load_cases(tmp_path)

    assert case.id == "pos-logging-001"
    assert case.label.kind == Kind.POSITIVE
    assert case.label.reviewed is False
    [issue] = case.label.issues
    assert issue.category == Category.PII_LOGGING
    assert issue.side == Side.NEW
    assert issue.tolerance == 3
    assert issue.required is True
    assert issue.end_line is None
    assert case.diff.changed_lines("app/views.py", Side.NEW) == {11, 12}
    assert case.context_dir is None


def test_context_folder_is_picked_up(tmp_path: Path) -> None:
    case_dir = write_case(tmp_path, "pos-logging-001", POSITIVE_LABEL)
    (case_dir / "context").mkdir()

    [case] = load_cases(tmp_path)

    assert case.context_dir == case_dir / "context"


def test_cases_come_back_sorted_by_id(tmp_path: Path) -> None:
    for case_id in ["neg-b", "neg-a"]:
        label = f"id: {case_id}\nkind: negative\nstack: go\nsource: synthetic\n"
        write_case(tmp_path, case_id, label)

    assert [case.id for case in load_cases(tmp_path)] == ["neg-a", "neg-b"]


def test_files_next_to_case_folders_are_ignored(tmp_path: Path) -> None:
    (tmp_path / ".gitkeep").write_text("")

    assert load_cases(tmp_path) == []


def test_folder_name_must_match_label_id(tmp_path: Path) -> None:
    write_case(tmp_path, "pos-logging-999", POSITIVE_LABEL)

    with pytest.raises(CaseError, match="pos-logging-999"):
        load_cases(tmp_path)


def test_unknown_label_field_is_rejected(tmp_path: Path) -> None:
    write_case(tmp_path, "pos-logging-001", POSITIVE_LABEL + "reviewd: true\n")

    with pytest.raises(CaseError, match="reviewd"):
        load_cases(tmp_path)


def test_missing_diff_is_rejected(tmp_path: Path) -> None:
    write_case(tmp_path, "pos-logging-001", POSITIVE_LABEL, diff=None)

    with pytest.raises(CaseError, match=r"pr\.diff"):
        load_cases(tmp_path)


def test_issue_on_an_unchanged_line_is_rejected(tmp_path: Path) -> None:
    label = label_with_issue("  - {category: PII_LOGGING, file: app/views.py, line: 10}")
    write_case(tmp_path, "case-1", label)

    with pytest.raises(CaseError, match=r"case-1.*app/views.py.*10"):
        load_cases(tmp_path)


def test_issue_on_a_file_outside_the_diff_is_rejected(tmp_path: Path) -> None:
    label = label_with_issue("  - {category: PII_LOGGING, file: app/other.py, line: 11}")
    write_case(tmp_path, "case-1", label)

    with pytest.raises(CaseError, match=r"app/other\.py"):
        load_cases(tmp_path)


def test_old_side_issue_is_checked_against_removed_lines(tmp_path: Path) -> None:
    label = label_with_issue(
        "  - {category: ERASURE_REGRESSION, file: app/views.py, line: 11, side: old}"
    )
    write_case(tmp_path, "case-1", label)

    [case] = load_cases(tmp_path)

    assert case.label.issues[0].side == Side.OLD


def test_range_is_valid_when_any_line_in_it_changed(tmp_path: Path) -> None:
    label = label_with_issue(
        "  - {category: PII_LOGGING, file: app/views.py, line: 10, end_line: 12}"
    )
    write_case(tmp_path, "case-1", label)

    [case] = load_cases(tmp_path)

    assert case.label.issues[0].end_line == 12


def test_end_line_before_line_is_rejected(tmp_path: Path) -> None:
    label = label_with_issue(
        "  - {category: PII_LOGGING, file: app/views.py, line: 12, end_line: 11}"
    )
    write_case(tmp_path, "case-1", label)

    with pytest.raises(CaseError, match="end_line"):
        load_cases(tmp_path)


def test_positive_case_needs_a_required_issue(tmp_path: Path) -> None:
    label = label_with_issue(
        "  - {category: PII_LOGGING, file: app/views.py, line: 11, required: false}"
    )
    write_case(tmp_path, "case-1", label)

    with pytest.raises(CaseError, match="positive"):
        load_cases(tmp_path)


@pytest.mark.parametrize("kind", ["negative", "tricky"])
def test_clean_case_cannot_have_a_required_issue(tmp_path: Path, kind: str) -> None:
    label = label_with_issue("  - {category: PII_LOGGING, file: app/views.py, line: 11}", kind)
    write_case(tmp_path, "case-1", label)

    with pytest.raises(CaseError, match=kind):
        load_cases(tmp_path)


def test_tricky_case_may_have_an_optional_issue(tmp_path: Path) -> None:
    label = label_with_issue(
        "  - {category: PII_LOGGING, file: app/views.py, line: 11, required: false}", "tricky"
    )
    write_case(tmp_path, "case-1", label)

    [case] = load_cases(tmp_path)

    assert case.label.issues[0].required is False


def test_adapted_case_needs_url_and_license(tmp_path: Path) -> None:
    label = POSITIVE_LABEL.replace("source: synthetic", "source: adapted")
    write_case(tmp_path, "pos-logging-001", label)

    with pytest.raises(CaseError, match="url"):
        load_cases(tmp_path)


def test_case_set_hash_ignores_order_and_review_status_but_not_content(tmp_path: Path) -> None:
    from benchmark.cases import case_set_hash

    write_case(tmp_path, "pos-a", POSITIVE_LABEL.replace("pos-logging-001", "pos-a"))
    write_case(tmp_path, "pos-b", POSITIVE_LABEL.replace("pos-logging-001", "pos-b"))
    cases = load_cases(tmp_path)
    original = case_set_hash(cases)

    assert case_set_hash(list(reversed(cases))) == original

    label = tmp_path / "pos-a" / "label.yaml"
    label.write_text(label.read_text() + "reviewed: true" + chr(10))
    assert case_set_hash(load_cases(tmp_path)) == original

    diff = tmp_path / "pos-a" / "pr.diff"
    diff.write_text(diff.read_text().replace("user.email", "user.phone"))
    assert case_set_hash(load_cases(tmp_path)) != original


def test_difficulty_and_patterns_are_optional_and_validated(tmp_path: Path) -> None:
    write_case(
        tmp_path,
        "pos-logging-001",
        POSITIVE_LABEL + "difficulty: hard\npatterns: [indirect_name, via_helper]\n",
    )

    [case] = load_cases(tmp_path)

    assert case.label.difficulty == "hard"
    assert [str(p) for p in case.label.patterns] == ["indirect_name", "via_helper"]


def test_unknown_pattern_is_rejected(tmp_path: Path) -> None:
    write_case(tmp_path, "pos-logging-001", POSITIVE_LABEL + "patterns: [sneaky]\n")

    with pytest.raises(CaseError, match="sneaky"):
        load_cases(tmp_path)


def test_adapted_case_needs_a_permissive_license(tmp_path: Path) -> None:
    adapted = POSITIVE_LABEL.replace("source: synthetic", "source: adapted")
    write_case(
        tmp_path,
        "pos-logging-001",
        adapted + "url: https://github.com/example/app/commit/abc\nlicense: GPL-3.0\n",
    )

    with pytest.raises(CaseError, match=r"GPL-3\.0"):
        load_cases(tmp_path)
