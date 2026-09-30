"""File writing actions with confirmation and diff.

Em mode especialista, a IA pode devolver blocos ``` with a path de
file. These blocks are offered to the user with a diff/preview
before writing. Paths outside the home require elevation (pkexec).
"""

import difflib
import os
import subprocess
import tempfile

try:
    from .render_core import FileBlock
    from .i18n import _
except ImportError:
    from render_core import FileBlock
    from i18n import _

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    HAS_GTK = True
except (ImportError, ValueError):
    HAS_GTK = False


def is_privileged_path(path):
    """True if the path is outside the user's home."""
    home = os.path.expanduser("~")
    real = os.path.realpath(os.path.expanduser(path))
    return not (real == home or real.startswith(home + os.sep))


MAX_DIFF_BYTES = 1024 * 1024


def preview_diff(path, new_content):
    """Devolve a diff unificado, ou None se o file no existe.

    Reads at most 1 MB of the existing file to prevent loading
    huge files into memory.
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            old_lines = f.readlines(MAX_DIFF_BYTES)
    except OSError:
        return None
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path, tofile=path
    )
    return "".join(diff)


def _write_privileged(temp_path, dest_path):
    """Copia tempfile for destino privilegiado via pkexec."""
    result = subprocess.run(
        ["pkexec", "cp", temp_path, dest_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise PermissionError(
            result.stderr.strip() or f"Falha ao write {dest_path} (pkexec)"
        )


def confirm_and_write(parent, block):
    """Show confirmation dialog with diff and write the file.

    Retorna ("written"|"cancelled"|"error", msg).
    """
    path = os.path.expanduser(block.path)
    diff = preview_diff(path, block.content)

    if not HAS_GTK:
        return ("error", "GTK unavailable")

    content = diff if diff is not None else block.content
    title = _("New file") if diff is None else _("Proposed changes")

    dialog = Gtk.Dialog(
        title=title, transient_for=parent, modal=True
    )
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(_("Write file"), Gtk.ResponseType.OK)
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
    """Deteta blocos de file na response e oferece escrita.

    Ignora respostas with mais de 3 blocos (provavelmente apenas
    code examples). `notify(msg)` is called in the UI context.
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
            notify(f"Erro ao write ficheiro: {msg}")
