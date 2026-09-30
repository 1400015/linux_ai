"""Ações de escrita de ficheiros com confirmação e diff.

Em modo especialista, a IA pode devolver blocos ``` com um caminho de
ficheiro. Estes blocos são oferecidos ao utilizador com um diff/preview
antes de escrita. Caminhos fora da home requerem elevação (pkexec).
"""

import difflib
import os
import subprocess
import tempfile

try:
    from .render_core import FileBlock
except ImportError:
    from render_core import FileBlock

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    HAS_GTK = True
except (ImportError, ValueError):
    HAS_GTK = False


def is_privileged_path(path):
    """True se o caminho está fora da home do utilizador."""
    home = os.path.expanduser("~")
    real = os.path.realpath(os.path.expanduser(path))
    return not (real == home or real.startswith(home + os.sep))


def preview_diff(path, new_content):
    """Devolve um diff unificado, ou None se o ficheiro não existe."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            old_lines = f.readlines()
    except OSError:
        return None
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path, tofile=path
    )
    return "".join(diff)


def _write_privileged(temp_path, dest_path):
    """Copia tempfile para destino privilegiado via pkexec."""
    result = subprocess.run(
        ["pkexec", "cp", temp_path, dest_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise PermissionError(
            result.stderr.strip() or f"Falha ao escrever {dest_path} (pkexec)"
        )


def confirm_and_write(parent, block):
    """Mostra diálogo de confirmação com diff e escreve o ficheiro.

    Retorna ("written"|"cancelled"|"error", msg).
    """
    path = os.path.expanduser(block.path)
    diff = preview_diff(path, block.content)

    if not HAS_GTK:
        return ("error", "GTK indisponível")

    content = diff if diff is not None else block.content
    title = "Novo ficheiro" if diff is None else "Alterações propostas"

    dialog = Gtk.Dialog(
        title=title, transient_for=parent, modal=True
    )
    dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
    dialog.add_button("Escrever ficheiro", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)

    box = dialog.get_content_area()
    scroll = Gtk.ScrolledWindow()
    scroll.set_min_content_height(240)
    scroll.set_min_content_width(560)
    box.pack_start(Gtk.Label(label=f"<b>{path}</b>"), False, False, 4)
    box.pack_start(scroll, True, True, 4)

    textview = Gtk.TextView()
    textview.get_buffer().set_text(content)
    textview.set_editable(False)
    textview.set_monospace(True)
    scroll.add(textview)

    dialog.show_all()
    response = dialog.run()
    dialog.destroy()

    if response != Gtk.ResponseType.OK:
        return ("cancelled", path)

    try:
        if is_privileged_path(path):
            fd, temp_path = tempfile.mkstemp(text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(block.content)
                _write_privileged(temp_path, path)
            finally:
                os.unlink(temp_path)
        else:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(path) or ".", text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(block.content)
                os.replace(temp_path, path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
        return ("written", path)
    except PermissionError as e:
        return ("error", str(e))
    except OSError as e:
        return ("error", str(e))


def offer_file_blocks(parent, reply_text, notify):
    """Deteta blocos de ficheiro na resposta e oferece escrita.

    Ignora respostas com mais de 3 blocos (provavelmente apenas
    exemplos de código). `notify(msg)` é chamado no contexto da UI.
    """
    blocks = FileBlock.parse_all(reply_text)
    if not blocks:
        return
    if len(blocks) > 3:
        return
    for block in blocks:
        try:
            status, msg = confirm_and_write(parent, block)
        except Exception as e:
            status, msg = "error", str(e)
        if status == "written":
            notify(f"Ficheiro escrito: {msg}")
        elif status == "error":
            notify(f"Erro ao escrever ficheiro: {msg}")
