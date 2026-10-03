"""Bounded local provenance for the source module actually being exercised.

Only a tracked module in a checkout is associated with HEAD. An installed wheel
inside a checkout's virtualenv must not inherit that checkout's identity.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess

from .process_output import run_bounded


_COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
_TIMEOUT = 3
_OUTPUT_LIMIT = 65536


@dataclass(frozen=True)
class BuildObservation:
    reference: str = 'unknown'
    source: str = 'unknown'
    commit: str = ''
    worktree: str = 'unknown'
    warning: str = ''

    def metadata(self):
        return {key: getattr(self, key) for key in ('reference', 'source', 'commit', 'worktree')}


def observe_checkout_build(module_path, *, runner=None):
    """Observe local Git once at trial start; never run operator-supplied text."""
    module = Path(module_path).resolve()
    root = next((parent for parent in module.parents if (parent / '.git').exists()), None)
    if root is None:
        return BuildObservation()
    executable = shutil.which('git')
    if not executable:
        return BuildObservation(warning='Build reference unavailable: Git is not installed')

    # Do not inherit GIT_DIR, alternate indexes, credential bindings, or global
    # helpers. These commands are local reads; fsmonitor/hooks and lazy fetches
    # are disabled, and status must not take optional write locks.
    environment = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
                   'GIT_TERMINAL_PROMPT': '0'}
    prefix = [executable, '--no-pager', '-c', 'core.fsmonitor=false',
              '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.untrackedCache=false',
              '-C', str(root)]
    run = runner or run_bounded

    def git(arguments):
        return run(prefix + arguments, _TIMEOUT, _OUTPUT_LIMIT, env=environment)

    def uncertain(warning):
        return BuildObservation(commit + '-worktree-unknown', 'checkout', commit, 'unknown', warning)

    try:
        code, _, _ = git(['ls-files', '--error-unmatch', '--', module.relative_to(root).as_posix()])
        if code != 0:
            return BuildObservation()
        code, output, _ = git(['rev-parse', '--verify', 'HEAD'])
        commit = output.strip()
        if code != 0 or not _COMMIT.fullmatch(commit):
            return BuildObservation(warning='Build reference unavailable: checkout HEAD could not be read')
    except (OSError, ValueError, subprocess.SubprocessError):
        return BuildObservation(warning='Build reference unavailable: local Git observation failed')

    try:
        # Reading worktree contents can invoke clean/process filters or fetch
        # missing partial-clone objects on older Git. Inspect names only, never
        # their values; do not launch status when either is configured.
        code, names, _ = git(['config', '--null', '--name-only', '--get-regexp',
                             r'^(filter\.|extensions\.partialclone$|remote\..*\.promisor$)'])
        if code == 0 and names:
            return uncertain('Checkout changes were not inspected because Git filters or partial-clone configuration are present')
        if code != 1 or names:
            return uncertain('Checkout changes could not be inspected safely: Git helper/fetch configuration is unavailable')
        # Status can also recurse into a gitlink whose repository has its own
        # filters/configuration. The index can be read without those helpers.
        code, entries, _ = git(['ls-files', '--stage'])
        if code != 0:
            return uncertain('Checkout changes could not be inspected safely: Git index is unavailable')
        if any(line.startswith('160000 ') for line in entries.splitlines()):
            return uncertain('Checkout changes were not inspected because Git submodules are present')
        code, output, _ = git(['status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'])
        if code == 0:
            state = 'dirty' if output else 'clean'
            return BuildObservation(commit + ('-dirty' if state == 'dirty' else ''),
                                    'checkout', commit, state)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return uncertain('Checkout changes could not be determined; HEAD alone does not identify the build')
