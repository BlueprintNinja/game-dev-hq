# Inspect: read-only agent-session navigation

Inspect is an independent Game Dev HQ implementation inspired by Joseph
Severino's internal QA navigator. No internal HQ code or proprietary
implementation is reused.

## What is included

- Inspect page: at most 30 recent HQ-supervised runs with links to the
  existing run and trace pages; records are not copied or re-attributed.
- Explicitly enrolled local Codex JSONL files appear in a separate,
  unlinked list. Each session detail page includes only selected structural
  event categories, timestamps, recognized tool names and a bounded timeline.
- No prompts, reasoning text, commands, arguments, outputs, raw logs,
  working directories or secrets appear in this session UI.
- Inspect never executes commands or edits source control, Lore, UEFN or
  project files. It cannot establish gameplay correctness or completion.
- OpenCode local session IDs, timestamps, and message-role timelines appear
  when both its database and workspace root are enrolled. No message bodies,
  tool arguments, or output are selected.

## Local enrollment

Inspect initially has no local session directory enrolled. Set
GAMEDEV_INSPECT_CODEX_ROOT to the absolute path of the workstation's Codex
sessions directory in the Game Dev HQ web process environment, then restart
the workspace. No other directory is scanned.

The Codex scanner examines YYYY/MM/DD/rollout-*.jsonl in the latest 14 UTC date
folders; rejects symlinks; lists at most 80 recent files; and reads at most
the last 3 MiB and 160 structural events per session. Older or larger
histories are explicitly incomplete. A file's last modification time is
not proof its agent is running. Session detail URLs contain opaque
fingerprints resolved from the bounded file list, never arbitrary file paths.

For OpenCode enrollment, also configure GAMEDEV_INSPECT_OPENCODE_DB as an
absolute path to its local SQLite session database, and
GAMEDEV_INSPECT_OPENCODE_ROOT as an absolute path to the allowed workspace.
Both are required: session directory paths outside the enrolled root are
excluded. The OpenCode reader is SQLite query-only, fetches up to 10,000
recent message-metadata rows, and lists up to 80 session IDs. It never
SELECTs message.data itself; SQL extracts only roles and timestamps.

Access requires the existing gamedev.traces.read capability and the normal
authenticated workstation session. Responses use private, no-store.
Enroll only on a private single-operator workspace. On a shared host
this metadata could disclose the existence and timing of unrelated work.

## Future work

- Expand OpenCode event classification beyond message-role summaries once
  the event schema has been verified without ingesting content.
- Correlate Codex/OpenCode sessions to HQ tasks only when an explicit stable
  session identifier was recorded by the run supervisor, not via timestamps
  or similar-looking prompts.
- Investigate searchable event categories and failure navigation without
  capturing secrets or confidential contents.

The Game Dev HQ plugin stays on API 4. Upstream plugin API 5 migration is a
separate change requiring full compatibility testing.
