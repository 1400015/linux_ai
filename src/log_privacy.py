"""Pure, best-effort log redaction without application or provider imports.

Structured argv retains argument boundaries. Legacy text has already lost
those boundaries, so an unquoted credential hides the ambiguous line tail.
"""

import ipaddress
import json
import re


_SECRET_LABEL = (r'(?:password|passwd|pwd|passphrase|token|secret|api[_-]?key|'
                 r'access[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
                 r'(?:http|ftp|proxy)[_-]?password|authorization|proxy[_-]?authorization|'
                 r'cookie|set[_-]?cookie|oauth2[_-]?bearer|session[_-]?key|'
                 r'secret[_-]?key|encryption[_-]?key|pgpassword)')
# Match the whole label, including environment-variable or option prefixes.
# Starting at the suffix alone loses DB_PASSWORD at the word boundary. Keep
# each prefix segment separate from its delimiter to avoid ambiguous nesting.
_SECRET_NAME = r'_*(?:[a-z0-9]+[_-]+)*' + _SECRET_LABEL
_SECRET_OPTION = re.compile(r'(?i)^' + _SECRET_NAME + r'$')
_SECRET_ASSIGNMENT = re.compile(r'(?i)^' + _SECRET_NAME + r'$')
_QUOTED = r'''(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*')'''
_CREDENTIAL = re.compile(
    r'(?i)(?P<prefix>(?<![\w-])(?:(?:--?)?' + _SECRET_NAME
    + r'''["']?[ \t]*[:=,][ \t]*|--?''' + _SECRET_NAME + r'[ \t]+))'
    + r'(?P<value>' + _QUOTED + r'|[^\r\n]*)')
_COMMAND_CREDENTIAL = re.compile(
    r'(?i)(?P<prefix>(?<![\w-])(?:--?)?' + _SECRET_NAME
    + r'''["']?[ \t]*(?:[:=,][ \t]*|[ \t]+))'''
    + r'(?P<value>' + _QUOTED + r'|[^\r\n]*)')
_COMMAND_CONTEXT = re.compile(r'(?i)\b(?:(?:running|executing)\s+)?command\s*:')
_JWT = re.compile(
    r'(?<![\w.-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.'
    r'[A-Za-z0-9_-]{8,}(?![\w-]|\.[A-Za-z0-9_-])')
_SLACK_TOKEN = re.compile(
    r'(?<![\w-])xox(?:[baprs]-|e[.-]xox[baprs]-)[A-Za-z0-9-]{8,}(?![\w-])')
_AWS_ACCESS_KEY_ID = re.compile(r'(?<![\w])(?:AKIA|ASIA)[A-Z0-9]{16}(?![\w])')


def secret_option(value):
    """Whether an argv item names a credential whose next item is its value."""
    return isinstance(value, str) and bool(_SECRET_OPTION.fullmatch(value.lower().lstrip('-')))


def _credential(match):
    value = match['value']
    if value[:1] in ('"', "'") and value[-1:] == value[:1]:
        return match['prefix'] + value[:1] + '[redacted]' + value[-1:]
    return match['prefix'] + '[redacted]'


def redact_text(text):
    """Remove recognizable secrets; unknown data still requires human review."""
    if not isinstance(text, str):
        raise ValueError('Log text must be a string')
    text = re.sub(r'-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\n]*PRIVATE KEY-----|\Z)',
                  '[private key removed]', text)
    text = re.sub(r'(?im)^(.*?\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*).*$',
                  r'\1[redacted]', text)
    text = '\n'.join(
        (_COMMAND_CREDENTIAL if _COMMAND_CONTEXT.search(line) else _CREDENTIAL).sub(_credential, line)
        for line in text.split('\n'))
    text = re.sub(r'(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)\b',
                  '[token]', text)
    text = _JWT.sub('[JWT removed]', text)
    text = _SLACK_TOKEN.sub('[Slack token removed]', text)
    # An AWS access-key ID identifies the account credential; it is not the
    # corresponding secret access key, but should not travel in public reports.
    text = _AWS_ACCESS_KEY_ID.sub('[AWS access key ID removed]', text)
    text = re.sub(r'(?i)\b(Bearer\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@', r'\1[credentials]@', text)
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[email]', text)
    text = re.sub(r'/(?:home|Users)/[^/\s]+', '/home/[user]', text)
    text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IPv4]', text)
    text = re.sub(r'(?i)(?<!\w)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?!\w)', '[MAC]', text)
    def hide_ipv6(match):
        try:
            ipaddress.IPv6Address(match[0].split('%')[0])
        except ValueError:
            return match[0]
        return '[IPv6]'
    text = re.sub(r'(?i)(?<![\w:])(?:[0-9a-f]*:){2,}[0-9a-f]*(?:%[\w.-]+)?(?![\w:])', hide_ipv6, text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return ''.join(char for char in text if char in '\n\t' or ord(char) >= 32)


def redact_argv(argv):
    """Preserve argv boundaries while removing recognized credential values."""
    if not isinstance(argv, (list, tuple)) or any(not isinstance(item, str) for item in argv):
        raise ValueError('Command arguments must be a sequence of strings')
    values, hide_next = [], False
    command = argv[0].rsplit('/', 1)[-1] if argv else ''
    for item in argv:
        if hide_next:
            values.append('[redacted]')
            hide_next = False
            continue
        hide_next = secret_option(item) or (item in ('-u', '--user', '--proxy-user')
                                            and command in ('curl', 'wget')) or (
            item == '-b' and command == 'curl') or (
            item == '-p' and command in ('sshpass', 'mysql', 'mysqldump'))
        name, separator, _ = item.partition('=')
        if separator and _SECRET_ASSIGNMENT.search(name.lstrip('-')):
            value = name + '=[redacted]'
        elif separator and name in ('-u', '--user', '--proxy-user') and command in ('curl', 'wget'):
            value = name + '=[redacted]'
        elif command == 'curl' and item.startswith(('-u', '-b')) and len(item) > 2:
            value = item[:2] + '[redacted]'
        elif command in ('mysql', 'mysqldump') and item.startswith('-p') and len(item) > 2:
            value = '-p[redacted]'
        else:
            value = redact_text(item)
        values.append(value)
    return values


def redact_command(argv):
    """Return a bounded JSON argv for logging, with credential values removed."""
    if not isinstance(argv, (list, tuple)) or any(not isinstance(item, str) for item in argv):
        raise ValueError('Command arguments must be a sequence of strings')
    values = [item if len(item) <= 4096 else item[:4096] + '[truncated]'
              for item in redact_argv(argv[:256])]
    if len(argv) > 256:
        values.append('[argument list truncated]')
    return json.dumps(values, ensure_ascii=False)
