"""Reading the agent's output for the words that say it has no allowance.

This module takes only the standard library, so the companion's own tests can
read it without installing anything.
"""

import json


def message_in(line):
    """The message an agent event carries, if it carries one."""
    try:
        event = json.loads(line)
    except ValueError:
        return line.strip()
    if not isinstance(event, dict):
        return line.strip()
    message = event.get("message")
    if not isinstance(message, str):
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else None
    if isinstance(message, str):
        return message.strip()
    return line.strip()


def quota_reason(matches, output):
    """The message that says the agent is out of allowance, if it said one.

    Only the phrases the operator's policy names count. Any other failure is a
    failure, and the run says so.
    """
    if not output:
        return None
    for line in output.splitlines():
        for phrase in matches:
            if phrase and phrase in line:
                return message_in(line)[:2000]
    return None
