"""Render helpers for chat text.

`from __future__ import annotations` keeps the PEP 604 unions (`int | None`)
and builtin generics (`tuple[int, int]`) as strings at runtime, so this
module imports on Python 3.8 (without it, the annotations are evaluated at
import time and `X | None` raises TypeError before 3.10).
"""
from __future__ import annotations

import os
import re
import xml.sax.saxutils

CODE_RE = re.compile(r"```(\S*)\n(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def esc(text: str) -> str:
    """Escape text for safe inclusion in Pango/markup."""
    return xml.sax.saxutils.escape(text)


class FileBlock:
    """A ``` block with a file path on the first line."""

    def __init__(self, path, content):
        self.path = path
        self.content = content

    @classmethod
    def parse_all(cls, text):
        blocks = []
        for match in CODE_RE.finditer(text):
            header = match.group(1).strip()
            body = match.group(2)
            if header.startswith("/") or header.startswith("~/"):
                blocks.append(cls(os.path.expanduser(header), body))
        return blocks


def _inline_to_markup(text: str) -> str:
    parts = []
    pos = 0
    for m in INLINE_CODE_RE.finditer(text):
        parts.append(esc(text[pos:m.start()]))
        parts.append(
            "<span font_family='monospace' background='#2a2a30'>%s</span>"
            % esc(m.group(1)))
        pos = m.end()
    parts.append(esc(text[pos:]))
    return "".join(parts)


def render_text_markup(text: str) -> str:
    parts = []
    pos = 0
    for m in CODE_RE.finditer(text):
        parts.append(_inline_to_markup(text[pos:m.start()]))
        pos = m.end()
    parts.append(_inline_to_markup(text[pos:]))
    return "".join(parts)


# --- Chat buffer logic (GTK-free, so it can be tested headless) -----------
#
# The "Thinking..." placeholder and the streaming response are inserted into
# the same Gtk.TextBuffer. Searching for the "[AI]" line to delete was
# fragile: the response also starts with "[AI]", so the search deleted the
# entire response. The fix is to store the exact offset interval of the
# placeholder and delete only that interval.

def placeholder_span(char_count_before: int, text: str) -> tuple[int, int]:
    """Interval (start, end) occupied by `text` inserted starting at `char_count_before`."""
    start = max(0, int(char_count_before))
    return start, start + len(text)


def valid_span(start: int | None, end: int | None, char_count: int) -> tuple[int, int] | None:
    """Return (start, end) if the interval is usable in the buffer, or None.

    Guards against stale offsets: if the buffer shrank (or nothing was
    inserted), the operation is a no-op instead of corrupting the text.
    """
    if start is None or end is None:
        return None
    start, end = int(start), int(end)
    if start < 0 or start >= end or end > int(char_count):
        return None
    return start, end


def header_offset(char_count_before: int, label: str) -> int:
    """Offset of the end of the ``\\n[label]\\n`` header inside a message."""
    return max(0, int(char_count_before)) + len("\n[%s]\n" % label)

