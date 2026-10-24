"""Reading the agent's output for the words that say it has no allowance.

This module takes only the standard library, so the companion's own tests can
read it without installing anything.
"""

import json

# The event types an agent reports a failure under. A line that carries none
# of them is the agent talking about its work, not the agent reporting that
# it could not do it.
FAILURE_TYPES = {
    "error",
    "failure",
    "item.failed",
    "response.failed",
    "stream_error",
    "turn.failed",
}

# A phrase only counts as the agent's own report when it starts the message
# or follows a sentence boundary. A phrase quoted inside another sentence is
# something the agent read, not something the agent said.
BOUNDARY = ".!?;:\n"


def event_in(line):
    """The event a JSON line carries, or None when the line is not an event."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def carries_failure(line):
    """Whether a line reports a failure rather than describing the work."""
    event = event_in(line)
    if event is None:
        # Raw output carries no structure, so the operator's phrase is the
        # only signal there is.
        return True
    if event.get("error") is not None:
        return True
    return str(event.get("type", "")) in FAILURE_TYPES


def messages_in(line):
    """Every message a line carries."""
    event = event_in(line)
    if event is None:
        return [line.strip()]
    found = []
    for key in ("message", "detail"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    error = event.get("error")
    if isinstance(error, dict):
        value = error.get("message")
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found or [line.strip()]


def message_in(line):
    """The message an agent event carries, if it carries one."""
    return messages_in(line)[0]


def states_quota(message, matches):
    """Whether a message says the agent has no allowance."""
    # Provider messages may spell the same apostrophe typographically.
    message = message.replace("\u2019", "'").replace("\u2018", "'")
    for phrase in matches:
        phrase = phrase.replace("\u2019", "'").replace("\u2018", "'")
        if not phrase:
            continue
        index = message.find(phrase)
        while index >= 0:
            start = index
            while start > 0 and message[start - 1].isspace():
                start -= 1
            if start == 0 or message[start - 1] in BOUNDARY:
                return True
            index = message.find(phrase, index + 1)
    return False


def quota_reason(matches, output):
    """The message that says the agent is out of allowance, if it said one.

    Only the phrases the operator's policy names count, and only where the
    agent reported a failure. A line that describes the work, or a failure
    that merely quotes the phrase, is not an exhausted allowance. Any other
    failure is a failure, and the run says so.
    """
    if not output:
        return None
    for line in output.splitlines():
        if not carries_failure(line):
            continue
        for message in messages_in(line):
            if states_quota(message, matches):
                return message[:2000]
    return None
