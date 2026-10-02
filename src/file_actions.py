"""File writing actions with confirmation and diff.

In expert mode, the AI can return ``` blocks with a file path on the
first line. These blocks are offered to the user with a diff/preview
before writing. Paths outside the home require elevation (pkexec).
"""

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from xml.sax.saxutils import escape

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
    """True if the path is outside the user's home.

    O HOME é resolvido com realpath (e não só expanduser): com $HOME atrás
    de symlinks, a versão antiga mandava TODAS as escritas em casa por
    pkexec desnecessariamente.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(os.path.expanduser(path))
    return not (real == home or real.startswith(home + os.sep))


def is_allowed_path(path, allowed_dirs=None):
    """True if `path` is inside $HOME or one of `allowed_dirs`.

    The AI file-block path must honour the same sandbox as
    SystemUtils.write_file(); otherwise expert mode could write anywhere
    via pkexec regardless of `permissions.allowed_edit_dirs`.
    """
    real = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    if real == home or real.startswith(home + os.sep):
        return True
    for allowed_dir in allowed_dirs or ():
        allowed_real = os.path.realpath(os.path.expanduser(allowed_dir))
        prefix = allowed_real if allowed_real.endswith(os.sep) else allowed_real + os.sep
        if real == allowed_real or real.startswith(prefix):
            return True
    return False


MAX_DIFF_BYTES = 1024 * 1024

_DIFF_TRUNCATION_NOTE = _(
    "WARNING: preview truncated at 1 MB; the file is bigger and this diff "
    "is NOT complete."
)


def _file_digest(path):
    """SHA-256 do ficheiro actual (None se não existir) — para fechar a
    janela TOCTOU entre o diff mostrado ao utilizador e a escrita."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def preview_diff(path, new_content):
    """Return the unified diff, or None if the file does not exist.

    Reads at most 1 MB of the existing file to prevent loading
    huge files into memory. Ficheiros maiores levam uma NOTA explícita
    no diff: o utilizador estava a autorizar uma edição com base num
    diff que mostrava o resto do ficheiro como "removido".
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    try:
        truncated = os.path.getsize(path) > MAX_DIFF_BYTES
        with open(path, "rb") as f:
            old_lines = f.read(MAX_DIFF_BYTES).decode('utf-8', errors='replace').splitlines(keepends=True)
    except OSError:
        return None
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path, tofile=path
    )
    text = "".join(diff)
    if truncated:
        text += f"\n\n!!! {_DIFF_TRUNCATION_NOTE}\n"
    return text


def _backup_path(path):
    """Caminho do backup .bak com timestamp (nunca sobrepõe um anterior)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{path}.{stamp}.{uuid.uuid4().hex}.bak"


def _make_backup(path):
    """Backup do ficheiro original antes de qualquer edição (best effort).

    O modelo "autorização pelo utilizador" só é defensável se o erro for
    reversível: sem backup, um "OK" num diff errado era irreversível.
    """
    try:
        if os.path.isfile(path):
            backup = _backup_path(path)
            with open(path, 'rb') as source, open(backup, 'xb') as target:
                shutil.copyfileobj(source, target, 65536)
            shutil.copystat(path, backup)
            return backup
    except OSError as e:
        # Não bloquear a escrita por falha de backup, mas deixar registo.
        print(f"Warning: could not create backup of {path}: {e}")
    return None


def _write_privileged(temp_path, dest_path, expected_digest=None, parent_identity=None):
    """One elevation prompt; anchored, exclusive destination and backup files."""
    if parent_identity is None:
        parent = os.stat(os.path.dirname(dest_path))
        parent_identity = (parent.st_dev, parent.st_ino)
    helper = os.path.join(os.path.dirname(__file__), 'privileged_write.py')
    result = subprocess.run(
        ['pkexec', os.path.realpath(sys.executable), helper, temp_path, dest_path,
         expected_digest if expected_digest is not None else '-',
         str(parent_identity[0]), str(parent_identity[1])],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PermissionError(
            result.stderr.strip() or f"Failed to write {dest_path} (pkexec)"
        )
    return json.loads(result.stdout).get('backup')


def confirm_and_write(parent, block, allowed_dirs=None):
    """Show confirmation dialog with diff and write the file.

    `allowed_dirs` is `permissions.allowed_edit_dirs`: paths outside $HOME
    are refused unless they fall inside one of those directories.

    Returns ("written"|"cancelled"|"error", msg).
    """
    # Resolve symlinks once, up-front: is_privileged_path() compares the
    # real path, so the target passed to pkexec must be the same path -
    # otherwise a symlink inside $HOME could redirect the privileged copy.
    path = os.path.realpath(os.path.expanduser(block.path))
    if not is_allowed_path(path, allowed_dirs):
        return ("error", _("Path not allowed: {path}").format(path=path))

    # Hash ANTES do diálogo: se o ficheiro mudar entretanto, o diff
    # mostrado já não corresponde à realidade e a escrita é recusada
    # (TOCTOU: antes, o utilizador autorizava um diff e aplicava-se
    # outro — sem revalidação nem backup).
    digest_before = _file_digest(path)
    parent_identity = None
    if is_privileged_path(path):
        try:
            parent_stat = os.stat(os.path.dirname(path))
            parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        except OSError as error:
            return ('error', str(error))
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
    header = Gtk.Label(label=f"<b>{escape(path)}</b>")
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

    # Revalidar DEPOIS do diálogo: ficheiro mudou => recusar e pedir novo diff
    if _file_digest(path) != digest_before:
        return (
            "error",
            _("File changed since the preview was shown; review it again: {path}")
            .format(path=path),
        )

    try:
        if is_privileged_path(path):
            fd, temp_path = tempfile.mkstemp(text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(block.content)
                _write_privileged(temp_path, path, digest_before, parent_identity)
            finally:
                os.unlink(temp_path)
        else:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            _make_backup(path)
            fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(path) or ".", text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(block.content)
                # Preservar o mode do original: mkstemp cria 0600 e um
                # os.replace directo fazia um script editado perder +x e
                # configs de sistema ficarem 0600.
                try:
                    st = os.stat(path)
                    os.chmod(temp_path, st.st_mode & 0o7777)
                    os.chown(temp_path, st.st_uid, st.st_gid)
                except OSError:
                    pass  # ficheiro novo ou sem permissão para chown
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


def offer_file_blocks(parent, reply_text, notify, allowed_dirs=None):
    """Detect file blocks in the response and offer writing.

    Ignores replies with more than 3 blocks (probably just
    code examples). `notify(msg)` is called in the UI context.
    `allowed_dirs` is forwarded to confirm_and_write() as the sandbox.
    """
    blocks = FileBlock.parse_all(reply_text)
    if not blocks:
        return
    if len(blocks) > 3:
        return
    for block in blocks:
        try:
            status, msg = confirm_and_write(parent, block, allowed_dirs)
        except Exception as e:
            status, msg = "error", str(e)
        if status == "written":
            notify(_("File written: {path}").format(path=msg))
        elif status == "error":
            notify(_("Error writing file: {detail}").format(detail=msg))
