"""Readable, length-framed Markdown for lossless conversation exports.

Message bodies remain ordinary Markdown. Their roles and boundaries come only
from the versioned framing, never from headings or fenced blocks in the body.
The format carries conversation text and timestamps, not executable state.
"""

import json


FORMAT = "linux-ai-conversation-markdown"
VERSION = 1
PREFIX = "<!-- " + FORMAT + " "
MESSAGE_PREFIX = "<!-- linux-ai-message "
MESSAGE_END = "\n<!-- /linux-ai-message -->\n\n"
DOCUMENT_END = "<!-- /linux-ai-conversation-markdown -->\n"


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def export_markdown(title, messages):
    header = {"format": FORMAT, "version": VERSION, "title": title,
              "message_count": len(messages)}
    parts = [PREFIX + _json(header) + " -->\n", "# " + title.replace("\n", " ") + "\n\n"]
    for entry in messages:
        metadata = {"role": entry["role"], "characters": len(entry["content"])}
        if "timestamp" in entry:
            metadata["timestamp"] = entry["timestamp"]
        role = "User" if entry["role"] == "user" else "Assistant"
        parts.extend([MESSAGE_PREFIX + _json(metadata) + " -->\n", "## " + role + "\n\n",
                      entry["content"], MESSAGE_END])
    parts.append(DOCUMENT_END)
    return "".join(parts)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate conversation Markdown metadata")
        result[key] = value
    return result


def _metadata(text, position, prefix):
    end = text.find("\n", position)
    if end < 0 or end - position > 4096:
        raise ValueError("Invalid conversation Markdown framing")
    line = text[position:end]
    if not line.startswith(prefix) or not line.endswith(" -->"):
        raise ValueError("Invalid conversation Markdown framing")
    try:
        value = json.loads(line[len(prefix):-4], object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid conversation Markdown metadata") from error
    if not isinstance(value, dict):
        raise ValueError("Invalid conversation Markdown metadata")
    return value, end + 1


def import_markdown(text, max_messages, max_message_chars):
    """Parse the entire frame strictly; common message validation is caller-owned."""
    header, position = _metadata(text, 0, PREFIX)
    if (set(header) != {"format", "version", "title", "message_count"}
            or header.get("format") != FORMAT or type(header.get("version")) is not int
            or header["version"] != VERSION):
        raise ValueError("Unsupported conversation Markdown version or format")
    title = header.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Invalid conversation Markdown title")
    count = header.get("message_count")
    if type(count) is not int or not 0 <= count <= max_messages:
        raise ValueError("Conversation import has too many or invalid messages")
    heading = "# " + title.replace("\n", " ") + "\n\n"
    if not text.startswith(heading, position):
        raise ValueError("Conversation Markdown title does not match its metadata")
    position += len(heading)
    messages = []
    for unused in range(count):
        metadata, position = _metadata(text, position, MESSAGE_PREFIX)
        if set(metadata) not in ({"role", "characters"}, {"role", "characters", "timestamp"}):
            raise ValueError("Invalid conversation Markdown message metadata")
        role = metadata.get("role")
        length = metadata.get("characters")
        if role not in ("user", "assistant") or type(length) is not int or not 0 <= length <= max_message_chars:
            raise ValueError("Conversation import contains an invalid or oversized message")
        heading = "## " + ("User" if role == "user" else "Assistant") + "\n\n"
        if not text.startswith(heading, position):
            raise ValueError("Conversation Markdown role does not match its metadata")
        position += len(heading)
        content = text[position:position + length]
        position += length
        if len(content) != length or not text.startswith(MESSAGE_END, position):
            raise ValueError("Conversation Markdown message length does not match its framing")
        position += len(MESSAGE_END)
        entry = {"role": role, "content": content}
        if "timestamp" in metadata:
            entry["timestamp"] = metadata["timestamp"]
        messages.append(entry)
    if text[position:] != DOCUMENT_END:
        raise ValueError("Unexpected or missing conversation Markdown content")
    return {"title": title, "messages": messages}
