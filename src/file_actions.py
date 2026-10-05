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
import tempfile
import time
import uuid
from xml.sax.saxutils import escape

from .process_output import run_bounded
from .privileged_write import FileOperationError, is_sensitive_path

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

    The AI file-block path is the only write entry point. It must honour
    `permissions.allowed_edit_dirs`; otherwise expert mode could write
    anywhere via pkexec. Confirmed writes go through the change journal.
    """
    real = os.path.realpath(os.path.expanduser(path))
    if is_sensitive_path(real):
        return False
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
PRIVILEGED_TIMEOUT = 120
# inspect_file decodes up to 1 MiB; json.dumps can escape one byte as six
# ASCII bytes (e.g. an invalid UTF-8 byte decoded as U+FFFD), plus its envelope.
MAX_PRIVILEGED_PREVIEW_BYTES = 6 * MAX_DIFF_BYTES + 4096

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


def _run_privileged(argv, output_limit=MAX_DIFF_BYTES):
    """Bound polkit authentication and helper execution together to 120 seconds."""
    try:
        code, stdout, stderr = run_bounded(argv, PRIVILEGED_TIMEOUT, output_limit)
    except subprocess.TimeoutExpired as error:
        raise FileOperationError(
            'Privileged operation timed out after 120 seconds, including polkit authentication and execution. '
            'This does not establish a file defect or cancellation. '
            'Check the target state and recovery journal before repeating it.', published=None
        ) from error
    except OSError as error:
        # An I/O or cleanup error can occur after the helper started. Its
        # publication state cannot be inferred from an exception alone.
        raise FileOperationError(str(error), published=None) from error
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def file_helper_command():
    """Resolve the installed, root-owned wrapper before opening polkit."""
    try:
        from .privileged_helpers import file_helper_command as installed_command
    except ImportError as error:
        raise PermissionError('The dedicated privileged file helper must be installed before '
                              'system files can be modified') from error
    return installed_command()


def _operation_result(result, message):
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if result.returncode:
        outcome = payload.get('outcome') if isinstance(payload, dict) else None
        if isinstance(outcome, dict):
            raise FileOperationError(result.stderr.strip() or message,
                                     published=outcome.get('published'), backup=outcome.get('backup'),
                                     cleanup=outcome.get('cleanup'))
        # polkit denial happens before the helper starts; other outcomes may
        # reflect a terminated helper after publication.
        raise FileOperationError(result.stderr.strip() or message,
                                 published=False if result.returncode in (126, 127) else None)
    if not isinstance(payload, dict) or payload.get('published') is not True:
        raise FileOperationError('Invalid privileged helper outcome; inspect the target before retrying',
                                 published=None)
    return payload.get('backup')


def _write_privileged(temp_path, dest_path, expected_digest=None, parent_identity=None,
                      source_digest=None, backup_path=None):
    """One elevation prompt; anchored, exclusive destination and backup files."""
    if parent_identity is None:
        parent = os.stat(os.path.dirname(dest_path))
        parent_identity = (parent.st_dev, parent.st_ino)
    result = _run_privileged(
        file_helper_command() + [temp_path, dest_path,
         expected_digest if expected_digest is not None else '-',
         str(parent_identity[0]), str(parent_identity[1])]
        + ([source_digest or '-', backup_path] if backup_path else ([source_digest] if source_digest else [])),
    )
    return _operation_result(result, f"Failed to write {dest_path} (pkexec)")


def _remove_privileged(path, expected_digest, parent_identity, backup_path=None):
    result = _run_privileged(
        file_helper_command() + ['--remove', path,
         expected_digest, str(parent_identity[0]), str(parent_identity[1])]
        + ([backup_path] if backup_path else []),
    )
    return _operation_result(result, 'Recovery denied')


def _inspect_privileged(path, expected_digest, parent_identity, backup=None, backup_digest=None):
    result = _run_privileged(
        file_helper_command() + ['--inspect', path, expected_digest,
         str(parent_identity[0]), str(parent_identity[1]), backup or '-', backup_digest or '-'],
        output_limit=MAX_PRIVILEGED_PREVIEW_BYTES,
    )
    if result.returncode:
        raise PermissionError(result.stderr.strip() or 'Recovery preview denied')
    return json.loads(result.stdout)


def confirm_and_write(parent, block, allowed_dirs=None, journal=None, session_id=''):
    """Show confirmation dialog with diff and write the file.

    `allowed_dirs` is `permissions.allowed_edit_dirs`: paths outside $HOME
    are refused unless they fall inside one of those directories.

    Returns ("written"|"cancelled"|"error", msg).
    """
    # Resolve symlinks once, up-front: is_privileged_path() compares the
    # real path, so the target passed to pkexec must be the same path -
    # otherwise a symlink inside $HOME could redirect the privileged copy.
    path = os.path.realpath(os.path.expanduser(block.path))
    current_dirs = allowed_dirs() if callable(allowed_dirs) else allowed_dirs
    if not is_allowed_path(path, current_dirs):
        return ("error", _("Path not allowed: {path}").format(path=path))

    # Hash ANTES do diálogo: se o ficheiro mudar entretanto, o diff
    # mostrado já não corresponde à realidade e a escrita é recusada
    # (TOCTOU: antes, o utilizador autorizava um diff e aplicava-se
    # outro — sem revalidação nem backup).
    from .change_journal import ChangeJournal, file_digest
    try:
        digest_before = file_digest(path)
    except (OSError, ValueError) as error:
        return ('error', str(error))
    parent_identity = None
    if os.path.isdir(os.path.dirname(path)):
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
    if file_digest(path) != digest_before:
        return (
            "error",
            _("File changed since the preview was shown; review it again: {path}")
            .format(path=path),
        )

    try:
        current_dirs = allowed_dirs() if callable(allowed_dirs) else allowed_dirs
        if not is_allowed_path(path, current_dirs):
            raise PermissionError('Path no longer allowed: ' + path)
        if not is_privileged_path(path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, temp_path = tempfile.mkstemp(text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(block.content)
            (journal or ChangeJournal()).apply(temp_path, path, digest_before,
                                               allowed_dirs, session_id, parent_identity)
        finally:
            os.unlink(temp_path)
        return ("written", path)
    except (PermissionError, ValueError) as e:
        return ("error", str(e))
    except OSError as e:
        return ("error", str(e))


def offer_file_blocks(parent, reply_text, notify, allowed_dirs=None, journal=None, session_id=''):
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
            status, msg = confirm_and_write(parent, block, allowed_dirs, journal, session_id)
        except Exception as e:
            status, msg = "error", str(e)
        if status == "written":
            notify(_("File written: {path}").format(path=msg))
        elif status == "error":
            notify(_("Error writing file: {detail}").format(detail=msg))
