"""File writing actions with confirmation and diff.

In expert mode, the AI can return ``` blocks with a file path on the
first line. These blocks are offered to the user with a diff/preview
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
    """Return the unified diff, or None if the file does not exist.

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
    """Copy temp file to a privileged destination via pkexec."""
    result = subprocess.run(
        ["pkexec", "cp", temp_path, dest_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise PermissionError(
            result.stderr.strip() or f"Failed to write {dest_path} (pkexec)"
        )


def confirm_and_write(parent, block):
    """Show confirmation dialog with diff and write the file.

    Returns ("written"|"cancelled"|"error", msg).
    """
    # Resolve symlinks once, up-front: is_privileged_path() compares the
    # real path, so the target passed to pkexec must be the same path -
    # otherwise a symlink inside $HOME could redirect the privileged copy.
    path = os.path.realpath(os.path.expanduser(block.path))
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
    header = Gtk.Label(label=f"<b>{path}</b>")
    # Without use_markup=True the label shows the literal <b> tags.
    header.set_use_markup(True)
    header.set_selectable(True)
    box.pack_start(header, False, False, 4)
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
    """Detect file blocks in the response and offer writing.

    Ignores replies with more than 3 blocks (probably just
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
            notify(f"File written: {msg}")
        elif status == "error":
            notify(f"Error writing file: {msg}")
