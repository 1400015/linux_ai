#!/bin/bash
# Optional system installation. DESTDIR stages packaging files without elevation.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
staging_dir="${DESTDIR:-}"
if [[ -z "$staging_dir" && "$(id -u)" != 0 ]]; then
    echo 'Run: sudo bash scripts/install-privileged-helpers.sh' >&2
    exit 1
fi
if [[ -n "$staging_dir" && ( "$staging_dir" != /* || "$staging_dir" == / ) ]]; then
    echo 'DESTDIR must name an absolute staging directory, not /.' >&2
    exit 1
fi

# A real system install must not follow a writable or linked destination tree.
/usr/bin/python3 -I -S - "$staging_dir" <<'PY'
import os
from pathlib import Path
import stat
import sys

staging = sys.argv[1]
if staging and (os.path.normpath(staging) != staging or os.path.realpath(staging) != staging):
    raise SystemExit('DESTDIR must be an absolute normalized directory without links.')
for target in ('/usr/libexec/linux-ai-assistant', '/usr/share/polkit-1/actions'):
    path = Path(staging + target)
    for parent in reversed([path] + list(path.parents)):
        if staging and str(parent) != staging and not str(parent).startswith(staging + '/'):
            continue
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        owner = os.getuid() if staging else 0
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022):
            raise SystemExit('Unsafe privileged-helper installation directory: ' + str(parent))
for relative in ('/usr/libexec/linux-ai-assistant/files', '/usr/libexec/linux-ai-assistant/services',
                 '/usr/libexec/linux-ai-assistant/privileged_write.py',
                 '/usr/libexec/linux-ai-assistant/privileged_service.py',
                 '/usr/libexec/linux-ai-assistant/manifest.json',
                 '/usr/share/polkit-1/actions/org.linux_ai_assistant.policy'):
    path = Path(staging + relative)
    try:
        info = path.lstat()
    except FileNotFoundError:
        continue
    owner = os.getuid() if staging else 0
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022):
        raise SystemExit('Unsafe privileged-helper installation file: ' + str(path))
PY

helper_dir="$staging_dir/usr/libexec/linux-ai-assistant"
policy_dir="$staging_dir/usr/share/polkit-1/actions"
install -d -m 0755 "$helper_dir" "$policy_dir"
install -m 0755 "$project_dir/scripts/privileged-files" "$helper_dir/files"
install -m 0755 "$project_dir/scripts/privileged-services" "$helper_dir/services"
install -m 0644 "$project_dir/src/privileged_write.py" "$helper_dir/privileged_write.py"
install -m 0644 "$project_dir/src/privileged_service.py" "$helper_dir/privileged_service.py"
install -m 0644 "$project_dir/polkit/org.linux_ai_assistant.policy" "$policy_dir/"

/usr/bin/python3 -I -S - "$helper_dir" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

directory = Path(sys.argv[1])
names = ('files', 'services', 'privileged_write.py', 'privileged_service.py')
manifest = {'protocol': 2, 'sha256': {
    name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}}
descriptor, temporary = tempfile.mkstemp(prefix='.manifest-', dir=str(directory))
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(manifest, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, directory / 'manifest.json')
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

echo 'Dedicated Linux AI Assistant file and runit activation helpers installed.'
