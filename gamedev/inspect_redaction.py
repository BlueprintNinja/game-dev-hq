"""Remove common credential forms before transcript content reaches HTML.

Redaction is defense in depth, not proof that arbitrary text is secret-free.
"""

import re

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL)
_AUTH = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*)"
    r"(?:[\"']?(?:bearer|basic)\s+[^\s\"']+[\"']?)"
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)"
    r"[\"']?\s*[=:]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s\"',;}&]+)"
)
_TOKENS = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)


def redact(value):
    """Preserve ordinary text but suppress recognizable credential material."""
    text = _PRIVATE_KEY.sub("[private key removed]", value)
    text = _AUTH.sub(r"\1[credential removed]", text)
    text = _ASSIGNMENT.sub(r"\1[credential removed]", text)
    return _TOKENS.sub("[credential removed]", text)
