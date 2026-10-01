"""ChatView: o widget de chat (buffer, tags, offsets, placeholder) isolado.

Extrai de main_window.py a lógica de inserção/tagging que produziu os bugs
de offsets. Invariante único: QUEM insere texto é quem calcula offsets —
todas as inserções passam por aqui e devolvem/registam os offsets reais.

O MainWindow mantém as decisões (o que mostrar, quando persistir) e delega
a mecânica do buffer a esta classe.
"""

import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from .i18n import _  # noqa: E402
from .render_core import placeholder_span, valid_span, code_ranges  # noqa: E402

logger = logging.getLogger(__name__)


class ChatView:
    """Buffer + TextView + ScrolledWindow do chat, com tags e offsets."""

    def __init__(self, font_family="Monospace", font_size=12):
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_justification(Gtk.Justification.LEFT)
        self.textview.set_left_margin(10)
        self.textview.set_right_margin(10)
        self.textview.set_top_margin(10)
        self.textview.set_bottom_margin(10)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_shadow_type(Gtk.ShadowType.NONE)
        self.scrolled.add(self.textview)

        self._create_tags(font_family, font_size)

        # Intervalo (start, end) do placeholder "Thinking..." no buffer
        self.loading_span = None
        # Offset onde o corpo da resposta streaming em curso começa
        self.body_start = None
        # Coalescing de scroll: um idle pendente no máximo
        self._scroll_pending = False

    # ---- Setup ----

    def _font(self, family, size):
        return f"{family} {size}"

    def _create_tags(self, font_family, font_size):
        buffer = self.textview.get_buffer()
        buffer.create_tag("user-message",
                          foreground="#e0e0e0",
                          font=self._font(font_family, font_size))
        buffer.create_tag("ai-message",
                          foreground="#a0d0a0",
                          font=self._font(font_family, font_size))
        buffer.create_tag("system-message",
                          foreground="#808080",
                          font=self._font(font_family, font_size - 1))
        buffer.create_tag("loading",
                          foreground="#808080",
                          font=self._font(font_family, font_size),
                          style=Pango.Style.ITALIC)
        # Applied on top of the message tags so code stands out without
        # changing the text (see apply_code_tags).
        buffer.create_tag("code-block", family="Monospace",
                          background="#2a2a30")
        buffer.create_tag("inline-code", family="Monospace",
                          background="#2a2a30")
        self.textview.set_buffer(buffer)

    @property
    def buffer(self):
        return self.textview.get_buffer()

    # ---- Inserção / tagging ----

    def _tag_region(self, buffer, tag_name, start_iter, end_iter):
        """apply_tag_by_name que falha de forma audível se a tag não existir.

        Em PyGObject, um nome de tag desconhecido produz um g_warning no C
        e NÃO uma exceção Python — o antigo `except TypeError` era código
        morto e a tag falhava em silêncio.
        """
        if buffer.get_tag_table().lookup(tag_name) is None:
            logger.warning("Chat tag not available: %s", tag_name)
            return
        buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

    def append_message(self, label: str, message: str, tag_name: str):
        """Insere `\\n[label]\\n<message>\\n\\n` e aplica `tag_name` a tudo.

        Os offsets são capturados ANTES da inserção. Devolve
        `(start, end, body_start)`.
        """
        buffer = self.buffer
        start_offset = buffer.get_char_count()
        header = f"\n[{label}]\n"
        text = f"{header}{message}\n\n"
        buffer.insert(buffer.get_end_iter(), text)
        end_offset = start_offset + len(text)
        self._tag_region(
            buffer, tag_name,
            buffer.get_iter_at_offset(start_offset),
            buffer.get_iter_at_offset(end_offset),
        )
        body_start = start_offset + len(header)
        self.apply_code_tags(body_start, message)
        return start_offset, end_offset, body_start

    def append_stream_header(self):
        """Insere o cabeçalho `[AI]` de uma resposta streaming (com a tag).

        Insere APENAS o cabeçalho `\\n[AI]\\n`: os chunks chegam depois via
        `insert_stream_chunk`, pelo que `body_start` tem de apontar
        exactamente para o fim do cabeçalho. Com `append_message(_('AI'),
        "", ...)` o texto inserido era "\\n[AI]\\n\\n\\n" e o body_start
        ficava 2 caracteres atrás do primeiro chunk — o corpo inteiro
        ficava sem a tag `ai-message` e o realce de código deslocado.
        """
        buffer = self.buffer
        start_offset = buffer.get_char_count()
        header = f"\n[{_('AI')}]\n"
        buffer.insert(buffer.get_end_iter(), header)
        end_offset = start_offset + len(header)
        self._tag_region(
            buffer, "ai-message",
            buffer.get_iter_at_offset(start_offset),
            buffer.get_iter_at_offset(end_offset),
        )
        return start_offset, end_offset, end_offset

    def insert_stream_chunk(self, chunk: str):
        """Insere um chunk streaming no fim do buffer (sem formatação)."""
        self.buffer.insert(self.buffer.get_end_iter(), chunk)

    def close_streamed_message(self, response_text: str):
        """Taggeia os code spans do corpo streaming e fecha com "\\n\\n".

        O corpo streaming não termina em "\\n\\n" (o cabeçalho é inserido
        sem o recheio); sem o separador, a mensagem seguinte colava-se ao
        fim da resposta.
        """
        body_start = self.body_start
        self.body_start = None
        if body_start is None:
            return
        self.apply_code_tags(body_start, response_text)
        self.buffer.insert(self.buffer.get_end_iter(), "\n\n")

    def apply_code_tags(self, body_start: int, message: str):
        """Tag fenced blocks and inline code in an already-inserted region.

        Only tags are applied (the text is never re-inserted), so this is
        safe to run after a streamed response has been written to the buffer.
        """
        if not message or ("`" not in message):
            return
        buffer = self.buffer
        char_count = buffer.get_char_count()
        for start, end, tag in code_ranges(message):
            span = valid_span(body_start + start, body_start + end, char_count)
            if span is None:
                continue
            self._tag_region(
                buffer, tag,
                buffer.get_iter_at_offset(span[0]),
                buffer.get_iter_at_offset(span[1]),
            )

    # ---- Placeholder de loading ----

    def show_loading(self, request_id=None, is_active=None, cancelled=None):
        """Insere o placeholder "Thinking..." com guarda de pedido.

        Guarda de pedido: um idle agendado por um worker cancelado podia
        correr depois de o estado ter sido reiniciado e inserir um
        "Thinking..." que ninguém remove (placeholder fantasma).
        """
        if request_id is not None and is_active is not None:
            if not is_active(request_id) or (cancelled is not None and cancelled()):
                return

        message = _("Thinking...")
        buffer = self.buffer
        text = f"\n[{_('AI')}]\n{message}"
        start_offset = buffer.get_char_count()
        buffer.insert(buffer.get_end_iter(), text)
        self.loading_span = placeholder_span(start_offset, text)

        self._tag_region(
            buffer, "loading",
            buffer.get_iter_at_offset(start_offset),
            buffer.get_iter_at_offset(start_offset + len(text)),
        )
        self.scroll_to_bottom()

    def clear_loading(self):
        """Remove APENAS o placeholder de loading.

        A versão antiga procurava a última linha `[AI]` e apagava até ao
        fim do buffer; como a resposta streaming também começa por `[AI]`,
        isso apagava a resposta inteira do ecrã.
        """
        span = self.loading_span
        self.loading_span = None
        if not span:
            return

        buffer = self.buffer
        valid = valid_span(span[0], span[1], buffer.get_char_count())
        if valid is None:
            logger.debug("Loading placeholder is no longer in the buffer; nothing to remove")
            return

        start_offset, end_offset = valid
        buffer.delete(
            buffer.get_iter_at_offset(start_offset),
            buffer.get_iter_at_offset(end_offset),
        )

    def has_loading(self) -> bool:
        return self.loading_span is not None

    # ---- Scroll ----

    def scroll_to_bottom(self):
        """Auto-scroll para o fundo (um idle pendente no máximo)."""
        if self._scroll_pending:
            return
        self._scroll_pending = True
        GLib.idle_add(self._do_scroll_to_bottom)

    def _do_scroll_to_bottom(self):
        self._scroll_pending = False
        adjustment = self.scrolled.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return False
