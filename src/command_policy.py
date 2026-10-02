"""Operand-aware policy for allowlisted diagnostics, separate from shell parsing."""

UNSAFE_COMMANDS = {'man', 'neofetch', 'less', 'more'}
BLOCKED_FLAGS = {
    'grep': {'-f', '--file', '--exclude-from', '-R', '--dereference-recursive'},
    'dig': {'-f'},
    'date': {'-f', '--file', '-s', '--set', '-r', '--reference'},
    'ip': {'-b', '-batch', '--batch', '-n', '-netns'},
    'ss': {'-F', '--filter', '-D', '--destroy'},
    'file': {'-f', '--files-from', '-m', '--magic-file'},
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
             '--dereference', '--no-dereference', '--uncompress', '--uncompress-noreport',
             '--raw', '--special-files', '--no-buffer', '--print0', '--extension', '--apple'},
}
# These values are formatting fields, never input paths.
OPTION_VALUES['grep'].update({'--label', '--group-separator'})
OPTION_VALUES['du'].update({'--time-style'})
OPTIONAL_VALUES['du'] = {'--time'}


def validate_arguments(argv, validate_path):
    command = argv[0]
    if command in UNSAFE_COMMANDS:
        return False
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
