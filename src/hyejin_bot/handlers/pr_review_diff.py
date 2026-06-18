"""Pure helpers for unified-diff hunk parsing (T022).

The handler uses `parse_hunk_ranges()` to extract the post-change line
ranges of every `@@ -X,Y +A,B @@` block in a file's `patch`, then
`is_anchor_in_hunk()` to filter the inline comments Claude produced down
to those whose `(line, start_line)` actually live inside one of those
hunks. Anchors that fall outside any hunk are folded into the Summary
as bullets — they were valid feedback but on a line GitHub's review API
won't accept.

Stdlib only; no I/O.
"""

from __future__ import annotations

import re

# `@@ -<old_start>[,<old_count>] +<new_start>[,<new_count>] @@`
_HUNK_HEADER_RE = re.compile(
    r"^@@\s*-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s*@@",
)


def parse_hunk_ranges(patch: str) -> list[tuple[int, int]]:
    """Return `(start_line, end_line)` (inclusive, post-change) for each hunk.

    A hunk header `@@ -X,Y +A,B @@` covers lines `A .. A + B - 1` on the
    post-change side. When `B` is omitted (single-line context), GitHub
    follows the standard unified-diff convention of treating it as `1`.
    Empty hunks (`B == 0`, the rare "delete-only" case) are returned as
    `(A, A - 1)` — i.e. `end < start` so `is_anchor_in_hunk()` rejects
    every line for that hunk.
    """
    ranges: list[tuple[int, int]] = []
    for line in patch.splitlines():
        match = _HUNK_HEADER_RE.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count_raw = match.group(2)
        count = 1 if count_raw is None else int(count_raw)
        if count == 0:
            # Empty post-change range — record an impossible interval.
            ranges.append((start, start - 1))
        else:
            ranges.append((start, start + count - 1))
    return ranges


def is_anchor_in_hunk(
    line: int,
    start_line: int | None,
    hunks: list[tuple[int, int]],
) -> bool:
    """Return True iff the anchor `[start_line .. line]` lies entirely in one hunk.

    A single-line anchor (start_line is None) is treated as the range
    `[line, line]`. A multi-line anchor's full range must be inside one
    contiguous hunk; ranges spanning hunks are rejected.
    """
    if start_line is None:
        anchor_start = line
        anchor_end = line
    else:
        if start_line > line:
            return False
        anchor_start = start_line
        anchor_end = line
    for hunk_start, hunk_end in hunks:
        if hunk_start <= anchor_start and anchor_end <= hunk_end:
            return True
    return False


__all__ = ["is_anchor_in_hunk", "parse_hunk_ranges"]
