"""Parse a unified diff into the line numbers it changes and shows, per file and side.

Files are keyed the way GitHub review comments key them: by the new path, or by the old
path when the file is deleted. Line numbers on the NEW side are new-file numbers; on the
OLD side, old-file numbers.
"""

from collections import defaultdict

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from reviewer.schemas import Side


class DiffError(ValueError):
    """The text is not a diff we can parse."""


_LineIndex = dict[tuple[str, Side], frozenset[int]]
_LineTexts = dict[tuple[str, Side, int], str]


class ParsedDiff:
    def __init__(
        self,
        files: frozenset[str],
        changed: _LineIndex,
        visible: _LineIndex,
        texts: _LineTexts | None = None,
    ) -> None:
        self.files = files
        self._changed = changed
        self._visible = visible
        self._texts = texts or {}

    def changed_lines(self, file: str, side: Side) -> frozenset[int]:
        """Lines added (NEW side) or removed (OLD side) in this file."""
        return self._changed.get((file, side), frozenset())

    def visible_lines(self, file: str, side: Side) -> frozenset[int]:
        """Changed lines plus the context lines shown around them."""
        return self._visible.get((file, side), frozenset())

    def line_text(self, file: str, side: Side, line: int) -> str | None:
        """The content of a visible line, without its newline; None if the diff doesn't show it."""
        return self._texts.get((file, side, line))


def parse_diff(text: str) -> ParsedDiff:
    try:
        patch = PatchSet.from_string(text)
    except UnidiffParseError as error:
        raise DiffError(str(error)) from error

    changed: defaultdict[tuple[str, Side], set[int]] = defaultdict(set)
    visible: defaultdict[tuple[str, Side], set[int]] = defaultdict(set)
    texts: _LineTexts = {}
    files: set[str] = set()

    for patched_file in patch:
        path = _comment_path(patched_file.source_file, patched_file.target_file)
        files.add(path)
        for hunk in patched_file:
            for line in hunk:
                text = line.value.rstrip("\r\n")
                if line.is_added:
                    changed[path, Side.NEW].add(line.target_line_no)
                if line.is_removed:
                    changed[path, Side.OLD].add(line.source_line_no)
                if line.target_line_no is not None:
                    visible[path, Side.NEW].add(line.target_line_no)
                    texts[path, Side.NEW, line.target_line_no] = text
                if line.source_line_no is not None:
                    visible[path, Side.OLD].add(line.source_line_no)
                    texts[path, Side.OLD, line.source_line_no] = text

    return ParsedDiff(
        files=frozenset(files),
        changed={key: frozenset(lines) for key, lines in changed.items()},
        visible={key: frozenset(lines) for key, lines in visible.items()},
        texts=texts,
    )


def _comment_path(source_file: str, target_file: str) -> str:
    if target_file == "/dev/null":
        return source_file.removeprefix("a/")
    return target_file.removeprefix("b/")


def number_diff(text: str) -> str:
    """The diff as the model sees it: every line with its old and new line number.

        === app/views.py (modified) ===
        @@ -10,4 +10,4 @@ def signup(request):
             10    10 |     user = create_user(form)
        -    11       |     user.save()
        +          11 |     user.save(update_fields=["name"])

    Columns are old number, new number. Removed lines have only an old number, added lines
    only a new one. Files are keyed by the same path parse_diff uses.
    """
    try:
        patch = PatchSet.from_string(text)
    except UnidiffParseError as error:
        raise DiffError(str(error)) from error

    blocks: list[str] = []
    for patched_file in patch:
        path = _comment_path(patched_file.source_file, patched_file.target_file)
        out = [f"=== {path} ({_status(patched_file)}) ==="]
        for hunk in patched_file:
            header = (
                f"@@ -{hunk.source_start},{hunk.source_length} "
                f"+{hunk.target_start},{hunk.target_length} @@ {hunk.section_header}"
            )
            out.append(header.rstrip())
            for line in hunk:
                if line.line_type not in ("+", "-", " "):
                    continue  # "\ No newline at end of file"
                old = "" if line.source_line_no is None else str(line.source_line_no)
                new = "" if line.target_line_no is None else str(line.target_line_no)
                content = line.value.rstrip("\r\n")
                out.append(f"{line.line_type} {old:>5} {new:>5} | {content}")
        blocks.append("\n".join(out))
    return "\n\n".join(blocks) + "\n"


def numbered_file(text: str, path: str) -> str | None:
    """number_diff's block for one file, or None if the diff doesn't touch that path."""
    for block in number_diff(text).split("\n\n"):
        if block.startswith(f"=== {path} ("):
            return block.rstrip("\n") + "\n"
    return None


def _status(patched_file) -> str:
    if patched_file.is_added_file:
        return "new file"
    if patched_file.is_removed_file:
        return "deleted"
    if patched_file.is_rename:
        return f"renamed from {patched_file.source_file.removeprefix('a/')}"
    return "modified"
