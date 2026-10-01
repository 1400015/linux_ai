import os
import re
import xml.sax.saxutils

CODE_RE = re.compile(r"```(\S*)\n(.*?)```", re.DOTALL)
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


# --- Logica do buffer de chat (sem GTK, para ser testavel headless) -----------
#
# O placeholder "Thinking..." e a resposta em streaming sao inseridos no mesmo
# Gtk.TextBuffer. Procurar a linha "[AI]" para apagar era fragil: a resposta
# tambem comeca por "[AI]", pelo que a busca eliminava a resposta inteira.
# A solucao e guardar o intervalo exato de offsets do placeholder e apagar
# apenas esse intervalo.

def placeholder_span(char_count_before, text):
    """Intervalo (inicio, fim) ocupado por `text` inserido a partir de `char_count_before`."""
    start = max(0, int(char_count_before))
    return start, start + len(text)


def valid_span(start, end, char_count):
    """Devolve (start, end) se o intervalo for utilizavel no buffer, ou None.

    Protege contra offsets obsoletos: se o buffer encolheu (ou nada foi
    inserido), a operacao e um no-op em vez de corromper o texto.
    """
    if start is None or end is None:
        return None
    start, end = int(start), int(end)
    if start < 0 or start >= end or end > int(char_count):
        return None
    return start, end


def header_offset(char_count_before, label):
    """Offset do fim do cabecalho ``\\n[label]\\n`` dentro de uma mensagem."""
    return max(0, int(char_count_before)) + len("\n[%s]\n" % label)

