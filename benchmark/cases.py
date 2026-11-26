"""Benchmark cases: one folder per case holding pr.diff, label.yaml and an optional context/.

load_cases validates every label against its diff, so a labelling mistake fails loudly
instead of quietly distorting the numbers.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from reviewer.diff import DiffError, ParsedDiff, parse_diff
from reviewer.schemas import Category, Side


class CaseError(ValueError):
    """A case folder is malformed or its label disagrees with its diff."""


class Kind(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    TRICKY = "tricky"


class Source(StrEnum):
    SYNTHETIC = "synthetic"
    ADAPTED = "adapted"


class Difficulty(StrEnum):
    EASY = "easy"
    HARD = "hard"


class Pattern(StrEnum):
    """What makes a case what it is. The report shows recall per pattern."""

    DIRECT_NAME = "direct_name"  # the data is named for what it is (email, phone)
    INDIRECT_NAME = "indirect_name"  # under a non-obvious name (contact, recipient, dob)
    VIA_HELPER = "via_helper"  # reaches the risky line through a helper, serializer or DTO
    WHOLE_OBJECT = "whole_object"  # a whole user/customer/request object is logged or sent
    EXCEPTION_MESSAGE = "exception_message"  # personal data in an exception or error text
    ERROR_TRACKER = "error_tracker"  # personal data in error-tracker context (Sentry, ...)
    SDK_CLIENT = "sdk_client"  # the third party is reached through an SDK client
    QUIET_ERASURE = "quiet_erasure"  # erasure weakened, not removed (retention, filter, flag)
    REMOVED_CODE = "removed_code"  # the risk is in deleted lines
    LARGE_PR = "large_pr"  # 5+ files or 150+ changed lines
    MULTI_FILE = "multi_file"  # the risk needs two files to see (defined here, used there)
    SPECIAL_CATEGORY = "special_category"  # Art. 9/10 data
    HASHED = "hashed"  # plain or keyed hash of an identifier
    MASKED = "masked"  # the value is masked before use
    TEST_DATA = "test_data"  # fixtures, factories, seed data
    MOVED_CODE = "moved_code"  # code moved between files
    ALREADY_PROTECTED = "already_protected"  # encrypted, retained or otherwise handled
    NON_PERSONAL = "non_personal"  # looks like personal data, isn't (company data, ids)
    EXISTING_PROCESSOR = "existing_processor"  # sent to a service that already receives it


# Adapted cases copy code from other projects, so only permissive licenses are allowed.
PERMISSIVE_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})


class Issue(BaseModel):
    """One labelled risk. A finding matches it within [line - tolerance, end_line + tolerance]."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Category
    file: str
    line: int = Field(ge=1)
    end_line: int | None = None
    side: Side = Side.NEW
    tolerance: int = Field(default=3, ge=0)
    required: bool = True

    @model_validator(mode="after")
    def _end_line_not_before_line(self) -> Self:
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError(f"end_line {self.end_line} is before line {self.line}")
        return self

    @property
    def last_line(self) -> int:
        return self.end_line if self.end_line is not None else self.line


class Label(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: Kind
    stack: str
    source: Source
    reviewed: bool = False
    url: str | None = None
    license: str | None = None
    difficulty: Difficulty | None = None
    patterns: tuple[Pattern, ...] = ()
    issues: tuple[Issue, ...] = ()

    @model_validator(mode="after")
    def _issues_fit_kind(self) -> Self:
        has_required = any(issue.required for issue in self.issues)
        if self.kind == Kind.POSITIVE and not has_required:
            raise ValueError("a positive case needs at least one required issue")
        if self.kind != Kind.POSITIVE and has_required:
            raise ValueError(f"a {self.kind} case cannot have required issues")
        return self

    @model_validator(mode="after")
    def _adapted_cases_are_credited(self) -> Self:
        if self.source == Source.ADAPTED and not (self.url and self.license):
            raise ValueError("an adapted case needs url and license")
        if self.license is not None and self.license not in PERMISSIVE_LICENSES:
            allowed = ", ".join(sorted(PERMISSIVE_LICENSES))
            raise ValueError(f"license {self.license} is not allowed; use one of {allowed}")
        return self


@dataclass(frozen=True)
class Case:
    id: str
    label: Label
    diff: ParsedDiff
    context_dir: Path | None
    diff_text: str = ""  # pr.diff as written, for the reviewer


def load_cases(cases_dir: Path) -> list[Case]:
    folders = sorted(path for path in cases_dir.iterdir() if path.is_dir())
    return [_load_case(folder) for folder in folders]


def _load_case(folder: Path) -> Case:
    name = folder.name
    label = _read_label(folder)
    if label.id != name:
        raise CaseError(f"{name}: folder name does not match label id {label.id!r}")

    diff_path = folder / "pr.diff"
    if not diff_path.is_file():
        raise CaseError(f"{name}: missing pr.diff")
    diff_text = diff_path.read_text(encoding="utf-8")
    try:
        diff = parse_diff(diff_text)
    except DiffError as error:
        raise CaseError(f"{name}: pr.diff does not parse: {error}") from error

    for issue in label.issues:
        _check_issue_location(name, issue, diff)

    context_dir = folder / "context"
    return Case(
        id=name,
        label=label,
        diff=diff,
        context_dir=context_dir if context_dir.is_dir() else None,
        diff_text=diff_text,
    )


def _read_label(folder: Path) -> Label:
    label_path = folder / "label.yaml"
    if not label_path.is_file():
        raise CaseError(f"{folder.name}: missing label.yaml")
    try:
        return Label.model_validate(yaml.safe_load(label_path.read_text()))
    except (yaml.YAMLError, ValidationError) as error:
        raise CaseError(f"{folder.name}: invalid label.yaml: {error}") from error


def _check_issue_location(name: str, issue: Issue, diff: ParsedDiff) -> None:
    if issue.file not in diff.files:
        raise CaseError(f"{name}: issue file {issue.file} is not in the diff")
    changed = diff.changed_lines(issue.file, issue.side)
    if not any(line in changed for line in range(issue.line, issue.last_line + 1)):
        raise CaseError(
            f"{name}: issue at {issue.file}:{issue.line}-{issue.last_line} ({issue.side} side)"
            " covers no changed line"
        )


def case_set_hash(cases: Sequence[Case]) -> str:
    """Identifies the exact cases a row was measured on: ids, labels and diffs.

    Ignores order and the `reviewed` flag, so approving a batch doesn't change the hash.
    """
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda c: c.id):
        label = case.label.model_dump(mode="json", exclude={"reviewed"})
        digest.update(json.dumps(label, sort_keys=True).encode())
        digest.update(b"\0")
        digest.update(case.diff_text.encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]
