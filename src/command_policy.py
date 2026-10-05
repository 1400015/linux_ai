"""Operand-aware policy for allowlisted diagnostics, separate from shell parsing."""

import ipaddress
import re


# An editable allowlist is not permission to introduce another execution engine.
# Reject paths as well: otherwise /usr/bin/ss could bypass the operand rules
# which apply to the ordinary command name ss.
UNSAFE_COMMANDS = {
    'man', 'neofetch', 'less', 'more', 'sh', 'bash', 'dash', 'ash', 'zsh',
    'ksh', 'ksh93', 'rksh', 'rbash', 'csh', 'tcsh', 'fish', 'busybox', 'toybox',
    'env', 'sudo', 'su', 'doas', 'pkexec', 'runuser', 'setpriv', 'chroot',
    'nsenter', 'unshare', 'setsid', 'nohup', 'systemd-run', 'at', 'batch',
    'timeout', 'stdbuf', 'nice', 'ionice', 'watch', 'xargs', 'parallel',
    'find', 'awk', 'gawk', 'mawk', 'nawk', 'sed', 'eval', 'exec', 'source',
    'python', 'pythonw', 'pypy', 'ipython', 'jython', 'graalpython', 'perl',
    'ruby', 'raku', 'node', 'nodejs', 'deno', 'bun', 'php', 'lua', 'luajit',
    'tclsh', 'wish', 'expect', 'R', 'Rscript', 'js', 'java', 'screen', 'tmux', 'gdb',
    'lldb', 'strace', 'ltrace', 'make', 'cmake', 'ninja', 'meson', 'git',
    'tar', 'rsync', 'vi', 'vim', 'nvim', 'emacs', 'ed', 'ex', 'xdg-open',
    'gio', 'ssh', 'rsh', 'bwrap', 'firejail', 'docker', 'podman', 'flatpak',
    'distrobox', 'toolbox', 'npm', 'npx', 'pip', 'pip3',
    'xterm', 'gnome-terminal', 'konsole', 'xfce4-terminal', 'alacritty',
    'kitty', 'foot', 'terminator', 'm4',
}
_VERSIONED_ENGINE = re.compile(
    r'(?:pythonw?|pypy|ipython|perl|ruby|raku|node|php|lua|tclsh|wish)'
    r'[0-9][0-9.]*(?:[a-z])?\Z')
BLOCKED_FLAGS = {
    'grep': {'-f', '--file', '--exclude-from', '-R', '--dereference-recursive'},
    'dig': {'-f'},
    'date': {'-f', '--file', '-s', '--set', '-r', '--reference'},
    'ip': {'-b', '-batch', '--batch', '-n', '-netns'},
    'ss': {'-F', '--filter', '-D', '--diag', '--destroy', '-K', '--kill', '-N', '--net'},
    'file': {'-f', '--files-from', '-m', '--magic-file', '-z', '--uncompress',
             '-Z', '--uncompress-noreport', '-s', '--special-files', '-S',
             '--no-sandbox', '-C', '--compile'},
    'traceroute': {'-f'},
    'du': {'-X', '--exclude-from', '--files0-from', '-L', '--dereference'},
    'ls': {'-L', '--dereference'},
}
FILE_COMMANDS = {'cat', 'head', 'tail', 'file', 'stat', 'ls', 'du', 'df', 'grep'}
OPTION_VALUES = {
    'grep': {'-e', '--regexp', '-m', '--max-count', '-A', '--after-context', '-B',
             '--before-context', '-C', '--context', '--include', '--exclude', '--exclude-dir',
             '--binary-files', '-d', '--directories', '-D', '--devices'},
    'head': {'-n', '--lines', '-c', '--bytes'},
    'tail': {'-n', '--lines', '-c', '--bytes', '-s', '--sleep-interval', '--pid'},
    'ls': {'-I', '--ignore', '--hide', '--sort', '--format', '--time', '--time-style',
           '--quoting-style', '--block-size', '-w', '--width', '-T', '--tabsize'},
    'du': {'-d', '--max-depth', '-B', '--block-size', '--exclude', '-t', '--threshold'},
    'df': {'-t', '--type', '-x', '--exclude-type', '-B', '--block-size'},
    'stat': {'-c', '--format', '--printf'},
    'file': {'-F', '--separator', '-e', '--exclude', '--exclude-quiet', '-P', '--parameter'},
}
OPTIONAL_VALUES = {
    'grep': {'--color', '--colour'},
    'ls': {'--color', '--hyperlink', '--classify'},
    'tail': {'--follow'},
    'df': {'--output'},
}
LONG_SWITCHES = {
    'cat': {'--show-all', '--number-nonblank', '--show-ends', '--number', '--squeeze-blank',
            '--show-tabs', '--show-nonprinting'},
    'head': {'--quiet', '--silent', '--verbose', '--zero-terminated'},
    'tail': {'--retry', '--quiet', '--silent', '--verbose', '--zero-terminated'},
    'grep': {'--extended-regexp', '--fixed-strings', '--basic-regexp', '--perl-regexp',
             '--ignore-case', '--no-ignore-case', '--word-regexp', '--line-regexp',
             '--null-data', '--no-messages', '--invert-match', '--version', '--help',
             '--line-buffered', '--byte-offset', '--line-number', '--with-filename',
             '--no-filename', '--label', '--only-matching', '--quiet', '--silent',
             '--binary', '--text', '--count', '--files-with-matches', '--files-without-match',
             '--null', '--initial-tab', '--recursive', '--no-group-separator'},
    'ls': {'--all', '--almost-all', '--author', '--escape', '--ignore-backups', '--directory',
           '--file-type', '--full-time', '--human-readable', '--si', '--inode', '--literal',
           '--numeric-uid-gid', '--quote-name', '--reverse', '--recursive', '--size', '--context',
           '--group-directories-first', '--dereference-command-line', '--dereference-command-line-symlink-to-dir'},
    'du': {'--all', '--apparent-size', '--bytes', '--total', '--human-readable', '--si',
           '--count-links', '--null', '--summarize', '--separate-dirs', '--one-file-system'},
    'df': {'--all', '--human-readable', '--si', '--inodes', '--local', '--no-sync',
           '--portability', '--sync', '--total', '--print-type'},
    'stat': {'--dereference', '--file-system', '--terse'},
    'file': {'--brief', '--mime', '--mime-type', '--mime-encoding', '--keep-going',
             '--dereference', '--no-dereference', '--raw', '--no-buffer',
             '--print0', '--extension', '--apple'},
}
# These values are formatting fields, never input paths.
OPTION_VALUES['grep'].update({'--label', '--group-separator'})
OPTION_VALUES['du'].update({'--time-style'})
OPTIONAL_VALUES['du'] = {'--time'}


_IP_SWITCHES = {
    '-4', '-6', '-0', '-B', '-M', '-s', '-statistics', '-stats', '-d',
    '-details', '-o', '-oneline', '-j', '-json', '-p', '-pretty', '-br',
    '-brief', '-h', '-human', '-human-readable', '-iec', '-t', '-timestamp',
    '-ts', '-tshort', '-N', '-Numeric', '-r', '-resolve',
}
_IP_OBJECTS = {
    'link': 'link', 'l': 'link', 'address': 'address', 'addr': 'address',
    'a': 'address', 'route': 'route', 'r': 'route', 'ro': 'route',
    'neighbour': 'neighbor', 'neighbor': 'neighbor', 'neigh': 'neighbor',
    'n': 'neighbor', 'rule': 'rule', 'ru': 'rule', 'netconf': 'netconf',
    'maddress': 'maddress', 'maddr': 'maddress', 'mroute': 'mroute',
}
_IP_SHOW = {'show', 'sh', 'list', 'lst', 'ls'}
_IP_FILTERS = {
    'link': {'dev', 'group', 'master', 'vrf'},
    'address': {'dev', 'scope', 'to', 'label', 'master', 'vrf', 'proto'},
    'route': {'table', 'protocol', 'proto', 'scope', 'type', 'dev', 'iif',
              'oif', 'via', 'from', 'to', 'src', 'realm', 'root', 'match',
              'exact', 'vrf', 'metric', 'tos'},
    'neighbor': {'dev', 'to', 'nud', 'vrf'},
    'rule': {'from', 'to', 'pref', 'priority', 'table', 'protocol', 'iif',
             'oif', 'fwmark', 'tos', 'uidrange', 'ipproto', 'sport', 'dport'},
    'netconf': {'dev'}, 'maddress': {'dev'},
    'mroute': {'table', 'from', 'to', 'iif'},
}
_IP_FILTER_SWITCHES = {
    'link': {'up'},
    'address': {'up', 'dynamic', 'permanent', 'tentative', 'deprecated',
                'secondary', 'primary', 'dadfailed', 'temporary', 'mngtmpaddr'},
    'route': {'cached', 'cloned'}, 'neighbor': {'proxy', 'router', 'unused'},
}
_IP_ADDRESS_FIELDS = {'to', 'from', 'via', 'src', 'root', 'match', 'exact'}
_IP_VALUE = re.compile(r'[A-Za-z0-9_.:@,+*]+(?:-[A-Za-z0-9_.:@,+*]+)*\Z')
_SS_SHORT_SWITCHES = set('hVnralBoempTisbEZz460tMSudwxHQO')
_SS_LONG_SWITCHES = {
    '--help', '--version', '--numeric', '--resolve', '--all', '--listening',
    '--bound-inactive', '--options', '--extended', '--memory', '--processes',
    '--threads', '--info', '--tipcinfo', '--summary', '--tos', '--cgroup',
    '--bpf', '--bpf-maps', '--events', '--context', '--contexts', '--ipv4',
    '--ipv6', '--packet', '--tcp', '--mptcp', '--sctp', '--udp', '--dccp',
    '--raw', '--unix', '--tipc', '--vsock', '--xdp', '--no-header',
    '--no-queues', '--oneline', '--inet-sockopt',
}
_SS_FAMILIES = {'inet', 'inet6', 'link', 'unix', 'netlink', 'vsock', 'tipc', 'xdp', 'help'}
_SS_QUERIES = {'all', 'inet', 'tcp', 'mptcp', 'udp', 'raw', 'unix',
               'unix_dgram', 'unix_stream', 'unix_seqpacket', 'packet',
               'packet_raw', 'packet_dgram', 'netlink', 'dccp', 'sctp',
               'vsock_stream', 'vsock_dgram', 'tipc', 'xdp'}


def _ip_address(value):
    if value in {'all', 'default'}:
        return True
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return False


def _ip_query(argv):
    """Accept known query verbs; never guess at ip's abbreviated mutators."""
    index = 1
    while index < len(argv) and argv[index].startswith('-'):
        argument = argv[index]
        index += 1
        if argument in {'-V', '-Version', '-version', '-help', '--help'}:
            return index == len(argv)
        if argument in {'-f', '-family'}:
            if index == len(argv) or argv[index] not in {'inet', 'inet6', 'mpls', 'bridge', 'link'}:
                return False
            index += 1
        elif argument not in _IP_SWITCHES:
            return False
    if index == len(argv):
        return True  # ip alone prints usage.
    if argv[index] == 'help':
        return index + 1 == len(argv)
    family = _IP_OBJECTS.get(argv[index])
    if family is None:
        return False
    index += 1
    explicit_show = index < len(argv) and argv[index] in _IP_SHOW
    if explicit_show:
        index += 1
    elif index < len(argv) and argv[index] == 'help':
        return index + 1 == len(argv)
    elif index < len(argv) and argv[index] == 'get':
        if family not in {'route', 'neighbor'}:
            return False
        index += 1
        if index == len(argv) or not _ip_address(argv[index]) or argv[index] in {'all', 'default'}:
            return False
        index += 1
        fields = ({'from', 'iif', 'oif', 'mark', 'tos', 'uid', 'ipproto',
                   'sport', 'dport', 'vrf'} if family == 'route' else {'dev'})
        switches = {'connected', 'fibmatch'} if family == 'route' else set()
        return _ip_filters(argv[index:], fields, switches)
    # With no verb, ip uses show. Permit only its known filter grammar, never
    # arbitrary first operands (which can be abbreviated add/set/delete/flush).
    fields = _IP_FILTERS[family]
    switches = _IP_FILTER_SWITCHES.get(family, set())
    return _ip_filters(argv[index:], fields, switches,
                       bare_interface=explicit_show and family in {'link', 'address'},
                       bare_address=family in {'route', 'neighbor', 'mroute'})


def _ip_filters(arguments, fields, switches, bare_interface=False, bare_address=False):
    index = 0
    bare_used = False
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        if argument in switches:
            continue
        if argument in fields:
            if index == len(arguments):
                return False
            value = arguments[index]
            index += 1
            if argument in _IP_ADDRESS_FIELDS:
                if not _ip_address(value):
                    return False
            elif not _IP_VALUE.fullmatch(value):
                return False
        elif not bare_used and bare_address and _ip_address(argument):
            bare_used = True
        elif not bare_used and bare_interface and _IP_VALUE.fullmatch(argument):
            bare_used = True
        else:
            return False
    return True


def _ss_query(argv, validate_path):
    """Parse query switches positively, including getopt short clusters."""
    index = 1
    options = True
    while index < len(argv):
        argument = argv[index]
        index += 1
        if options and argument == '--':
            options = False
            continue
        if options and argument.startswith('-') and argument != '-':
            option, separator, value = argument.partition('=')
            if argument.startswith('--'):
                if option in _SS_LONG_SWITCHES:
                    if separator:
                        return False
                    continue
                if option not in {'--family', '--query', '--socket'}:
                    return False
                kind = 'f' if option == '--family' else 'A'
                if not separator:
                    if index == len(argv):
                        return False
                    value = argv[index]
                    index += 1
            else:
                for offset, flag in enumerate(argument[1:], 1):
                    if flag in {'f', 'A'}:
                        kind = flag
                        value = argument[offset + 1:]
                        if not value:
                            if index == len(argv):
                                return False
                            value = argv[index]
                            index += 1
                        break
                    if flag not in _SS_SHORT_SWITCHES:
                        return False
                else:
                    continue
            valid = value in _SS_FAMILIES if kind == 'f' else bool(value) and all(
                query in _SS_QUERIES for query in value.split(','))
            if not valid:
                return False
        elif ('/' in argument or argument.startswith('~')) and not validate_path(argument):
            return False
    return True


def _ifconfig_query(argv):
    """ifconfig is also a default diagnostic, but accepts mutating operands."""
    interface = False
    for argument in argv[1:]:
        if argument in {'-a', '-s', '-v', '-h', '--help', '--version'}:
            continue
        if interface or not _IP_VALUE.fullmatch(argument):
            return False
        interface = True
    return True


def validate_arguments(argv, validate_path):
    if not argv or not isinstance(argv[0], str) or not argv[0]:
        return False
    command = argv[0]
    if ('/' in command or '\\' in command or command in UNSAFE_COMMANDS
            or _VERSIONED_ENGINE.fullmatch(command)):
        return False
    if command == 'ip':
        return _ip_query(argv)
    if command == 'ss':
        return _ss_query(argv, validate_path)
    if command == 'ifconfig':
        return _ifconfig_query(argv)
    blocked = BLOCKED_FLAGS.get(command, set())
    values = OPTION_VALUES.get(command, set())
    optional_values = OPTIONAL_VALUES.get(command, set())
    operands = []
    options = True
    grep_pattern = False
    index = 1
    recursive = False
    while index < len(argv):
        argument = argv[index]
        index += 1
        if options and argument == '--':
            options = False
            continue
        if options and argument.startswith('-') and argument != '-':
            option, separator, value = argument.partition('=')
            short_blocked = {flag[1:] for flag in blocked if len(flag) == 2}
            # Short flags can be clustered or carry their value: -rfFILE.
            short_flags = []
            if not argument.startswith('--'):
                for flag in argument[1:]:
                    short_flags.append(flag)
                    if '-' + flag in values:
                        break
            # GNU tools accept unambiguous long-option abbreviations. Treat
            # abbreviations of a forbidden option as forbidden too.
            forbidden_long = option.startswith('--') and any(
                flag.startswith(option) for flag in blocked if flag.startswith('--'))
            if option in blocked or forbidden_long or any(flag in short_blocked for flag in short_flags):
                return False
            if command in FILE_COMMANDS and argument.startswith('--'):
                known = values | optional_values | LONG_SWITCHES.get(command, set()) | {'--help', '--version'}
                # Reject aliases/unknown switches: a long-option abbreviation
                # can otherwise consume a regex, making a filename look like it.
                if option not in known:
                    return False
            if command == 'du' and option in {'--files0-from', '-X', '--exclude-from'}:
                return False
            if command == 'df' and option == '--output':
                continue
            if command == 'grep' and (option in {'-r', '-R', '--recursive', '--dereference-recursive'}
                                      or (not argument.startswith('--') and 'r' in argument[1:])):
                recursive = True
            if option in values:
                if not separator:
                    if index == len(argv):
                        return False
                    value = argv[index]
                    index += 1
                if command == 'grep' and option in {'-e', '--regexp'}:
                    grep_pattern = True
                if command == 'grep' and option in {'-d', '--directories'} and value == 'recurse':
                    recursive = True
            elif option in optional_values:
                pass  # Without '=', the next argument remains a file operand.
            elif not argument.startswith('--'):
                # Locate an option carrying a value within a short cluster.
                for offset, flag in enumerate(argument[1:], 1):
                    if '-' + flag in values:
                        value = argument[offset + 1:]
                        if not value:
                            if index == len(argv):
                                return False
                            value = argv[index]
                            index += 1
                        if command == 'grep' and flag == 'e':
                            grep_pattern = True
                        if command == 'grep' and flag == 'd' and value == 'recurse':
                            recursive = True
                        break
            elif separator and ('/' in value or value.startswith('~')):
                if not validate_path(value):
                    return False
            continue
        if command == 'grep' and not grep_pattern:
            grep_pattern = True
            continue  # A regex is not a filename, even if it contains '/'.
        if command in FILE_COMMANDS:
            if argument not in {'-', '/dev/stdin'}:
                operands.append(argument)
        elif ('/' in argument or argument.startswith('~')) and not validate_path(argument):
            return False
    if command in {'ls', 'du'} and not operands:
        operands = ['.']
    if command == 'grep' and recursive and not operands:
        operands = ['.']
    return all(validate_path(path) for path in operands)
