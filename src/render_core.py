import os
import re
import xml.sax.saxutils

CODE_RE = re.compile(r"```(\S*)\n(.*?)(?:```|\Z)", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def esc(text):
    return xml.sax.saxutils.escape(text)


class FileBlock:
    """Bloco ``` com caminho de ficheiro na primeira linha."""

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


def _inline_to_markup(text):
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


def render_text_markup(text):
    parts = []
    pos = 0
    for m in CODE_RE.finditer(text):
        parts.append(_inline_to_markup(text[pos:m.start()]))
        pos = m.end()
    parts.append(_inline_to_markup(text[pos:]))
    return "".join(parts)

