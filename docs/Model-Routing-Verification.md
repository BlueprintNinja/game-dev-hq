# First model routing milestone: local verification

Baseline: `a43fbae`, run-27. No fetch was performed because external network
was prohibited; cached origin/main is 20 commits behind HEAD. No commit, push,
deploy, live agent, editor operation, island change or live database migration
was performed. All tracked changes are in gamedev/, tools/windows_hq/tests/
and docs/.

Implemented routing and durable pre-invocation selection, additive migration
0010, queue profile UI/capability input, bounded Sol orchestration with routine
Luna children and legacy OpenCode payload compatibility, explicit audited
single-attempt Sol escalation, run/mission visibility and sanitized trace export.
Actual provider model is explicitly unavailable, not inferred from selection.
No cost estimates are added. See Game-Dev-Execution.md for policy and rollout.

## Executed checks

Python executable used (existing installation, no dependencies installed):
`C:/Users/mrray/Documents/Codex/2026-10-21/referenced-chatgpt-conversation-this-is-an/work/source-apply/.venv/Scripts/python.exe`.
TEMP, TMP and TMPDIR were set per process to this checkout's ignored var/tmp.

- `python -m tools.windows_hq.check`: 98 tests, OK, no skips; final run
  8.805 seconds, exit 0. This is the unmodified full workspace check in a
  throwaway composition/database, not an import-only check. System checks
  reported no issues. Migrations were applied to its test database.
- `python -m unittest discover -s gamedev/tests -v`: 80 tests in 4.192 seconds,
  OK, seven existing environmental skips, exit 0. Two symlink tests could not
  create Windows links; five socket tests were refused by this sandbox.
  No test suppression or discovery changes were added.
- `python -m hq_sdk.validation gamedev/hq_plugin`: SDK imports and template
  boundaries passed, exit 0. Whole-gamedev validation is intentionally not the
  plugin boundary.
- `makemigrations gamedev_hq --check --dry-run`, through workspace manage in
  a throwaway environment: no changes detected, exit 0. The migration-state
  regression verifies old fields are absent and new historical defaults are
  blank, with no backfill inference.
- Cached Ruff 0.17.1 executable:
  `C:/Users/mrray/AppData/Local/uv/cache/archive-v0/Ce9kQwHd3Kpy5DsA/ruff-0.17.1.data/scripts/ruff.exe`.
  `check gamedev tools/windows_hq/tests`: all checks passed, exit 0.
  `format --check gamedev tools/windows_hq/tests`: 76 files already formatted,
  exit 0. No downloaded tooling or rule exceptions.
- Repository cognitive scorer applied to changed production Python functions:
  maximum 19; files below 1,000 lines. This targeted check is not a substitute
  for the full structural graph gate.
- `git diff --check`: passed.

New regression coverage includes exact model/effort flags and retained sandbox
flags; policy and queue invalid inputs; web default/selection; durable route
before process invocation; ordinary retry profile preservation; escalation
permissions, version, note, state, infrastructure failure and chain limits;
audit failure rollback; two-child Codex/OpenCode dispatch and recursion/apply
denials; parent acceptance waits; unknown history; noncoding dispatch; read
permission/fragment denial; no GET queue/retry mutations; export content
exclusion. Existing Mission Control query-budget and security tests also ran.

## Remaining verification limits

`mise run fast` and `mise run check` could not start: mise is not installed.
Consequently host aggregate gates, browser gate and preflight are not claimed.
No interactive browser was available for the queue-to-review workflow; HTML
and authorization were exercised by the full workspace suite. Browser QA at
phone/tablet/desktop widths remains necessary before deployment. External CI,
provider execution, live sandbox compatibility and production rollout are
unverified. The existing environmental skips above remain limitations,
including the symlink security cases; none were bypassed or marked passing.

A first test attempt without per-process TEMP settings failed with Windows
temporary-directory PermissionError. The final checks used the writable local
temporary directory. Earlier development failures are superseded by the full
successful runs, not treated as successful evidence.
