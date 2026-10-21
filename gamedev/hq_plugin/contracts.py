"""Task lifecycle shared by local planning and the persistent HQ extension."""

TRANSITIONS = {
    "queued": {"active", "blocked"},
    "active": {"review", "blocked"},
    "blocked": {"queued"},
    "review": {"active", "done", "blocked"},
    "done": set(),
}
