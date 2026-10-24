# Supervised Game Dev work

The saved Game Dev task board can queue a Codex job, show its log while it
runs, and retain the starting revision, diff, check results and review decision.
The local worker is a separate process. Web requests never start commands.

## Local policy

Application has two separate destinations. `apply_destination` and `apply_scope`
govern island writes. Optional `source_apply_scope` names tooling files that may
instead be written into `repository`. Keep these scopes narrow and disjoint.
For example, explicitly list reviewed `tools/example.py` and
`workspace/SOURCE_CHECKS.md`; do not widen the island scope to admit Python.
One change must fit exactly one destination. Mixed or ambiguous changes are
refused. The worker publishes both scopes so the dashboard can show the target
and block unsupported changes before queueing an apply. The worker revalidates
the actual policy and destination contents before writing, with a rollback
snapshot. Source text imports may differ from their Git base only in LF/CRLF
line endings; the same exception applies to UTF-8 island `.verse` files only.
Content differences, NUL-containing data, and invalid UTF-8 still conflict.
Snapshots preserve the destination's exact original bytes, including CRLF;
installed files hold the approved worktree bytes. Other island files retain
byte-for-byte comparisons. A failed apply retains the original approval: return to the
approved run after correcting the policy, rather than rerunning the coding job.

Keep `worker-policy.json` outside every coding repository, alongside the
workspace database. It is a trusted operator file, never uploaded from the UI.
Use absolute executable and repository paths. Example (replace the paths):

```json
{
  "codex": "C:/Tools/Codex/codex.exe",
  "windows_sandbox": "mxc",
  "opencode": "C:/Tools/OpenCode/opencode.exe",
  "opencode_model": "opencode/minimax-m3.1-flash-free",
  "opencode_domains": ["opencode.ai", "api.opencode.ai", "models.opencode.ai"],
  "artifacts": "C:/GameDevHQ/runs",
  "quota_fallback": {
    "agent": "opencode",
    "matches": ["You've hit your usage limit"]
  },
  "projects": {
    "my-project": {
      "repository": "C:/Projects/MyProject",
      "checks": [["C:/Tools/Python/python.exe", "-m", "unittest", "discover"]],
      "timeout_seconds": 600,
      "uefn_project": "C:/Fortnite Projects/MyIsland/MyIsland.uefnproject"
    }
  }
}
```

The project key must match a saved HQ project slug. Coding requires a Git
repository and runs from its current committed HEAD. Uncommitted changes are
not copied. A UEFN project can be inspected or built without being in Git;
its repository/check settings are unused for those two fixed actions. Set
`coding_enabled` to `false` for an editor-only project.

## When the agent is out of allowance

A coding run needs the Codex CLI. When that account has no allowance left, the
run fails, and the operator is left with a board that cannot do its work.

`quota_fallback` says what to do about it. The worker reads the agent's output
for the phrases in `matches`. When one of them appears, the work is finished by
`opencode` instead, with the model and the provider domain policy the file
already names. The fallback runs in the same worktree, under the same sandbox
and the same checks, so the evidence the operator reviews is the same either
way.

Only those phrases count. Any other failure is a failure, and the run says so.
Without `quota_fallback` nothing changes: the run fails as it did.

The fallback is recorded in the run's execution log, above the output of the
engine that did the work, and the run's own result is not read as a Codex
result. A fallback cannot delegate, and it is not a delegation: the run keeps
the agent the operator chose, and the task keeps its place on the board.

The fallback engine runs under the Codex sandbox helper, so it needs that
helper to work. An account refused the CLI outright is refused there too, and
the run fails with both messages visible.

Initialize/migrate the workspace, then start the worker using
`deploy/windows/Start-GameDevWorker.ps1 -PolicyPath <absolute-policy-path>`.
Pass the same explicit `DataDirectory`, `LogDirectory` and `UvPath` as the
workspace startup. Run under the signed-in operator with existing Codex login.
No API key is stored by HQ. For one queued job, the equivalent Python entry
point is `python -m gamedev.worker --policy <path> --once` with the workspace
environment configured. Only one worker may hold the workspace lock.

The worker waits for the workspace before it opens the database. The workspace
migrates that database on every start, so a worker that started beside it would
read a half-migrated file. `Start-GameDevWorker.ps1` passes
`--workspace-url http://127.0.0.1:<port>/health/live/`, and the worker waits for
that endpoint to answer before it claims anything; it gives up with what the
endpoint answered instead, after `--workspace-timeout` seconds.

For startup at logon, use `deploy/windows/Install-GameDevWorker.ps1` with the
same policy/data/log/runtime arguments. It registers `GameDevHQ-Worker`, using a
limited interactive principal, hidden PowerShell, explicit absolute paths,
one instance, and restart on failure. The worker it starts waits for the
workspace, so the two may start at the same logon. Do not create a public
listener or a Tailscale Funnel route.

## Backups

The workspace database holds the audit history and, once work has run, the
captured agent logs, diffs and check results. It is operator-private data, so
it is backed up by a task of its own.

`deploy/windows/Backup-GameDevWorkspace.ps1` folds the write-ahead log into the
database, copies that one file into `%LOCALAPPDATA%\GameDevHQ\backups`, and
prunes the copies older than the newest fourteen. Run it by hand for a copy
now, or `deploy/windows/Install-GameDevBackup.ps1` to register
`GameDevHQ-Backup`, which runs it daily and keeps the same fourteen.

The copy is made after the checkpoint, so one file holds everything; a copy of
the raw database file alone would miss whatever still sat in the log. The
backups live outside the repository and are never uploaded anywhere.

## Execution and review

1. Create a task assigned to Codex, expand it, enter work instructions and
   select **Start work**. Select an editor action instead for a UEFN check.
2. Follow the run link. Its progress refreshes through HQ's shared page
   refresh mechanism. Canceling requests process termination.
3. Review the captured diff and every independent check. Accept requires
   passing checks, completed delegated tasks and a review note. A failed
   check cannot be accepted. Decisions reject stale versions and are audited.
4. Acceptance marks the task done; rejection sends it back for changes. It records
   approval of captured evidence; it does not apply, commit, push, merge or
   publish changes. Retained worktrees remain available for local inspection.

Only an orchestration profile receives delegation instructions. Codex receives
a bounded structured response schema and may request at most two routine
Codex/Luna or OpenCode children. The worker records the originating Codex run and creates
the children in the same project. A web-created OpenCode assignment cannot
launch itself. Each child gets its own worktree, check run and independent
review. Child changes are not automatically combined into the parent; review
and integrate them explicitly. Children cannot delegate further or apply changes into either destination.
Recursive OpenCode delegation is disabled.

## Boundaries and operational limits

- Codex runs without the shared CLI daemon, with user configuration ignored,
  workspace writes, no approvals and no command network access. No unsandboxed
  fallback is attempted. Windows uses the explicitly configured MXC or elevated
  sandbox. Verify sandbox compatibility before unattended use.
- Locally configured checks run under the Codex OS sandbox with network off.
  Checks execute changed code: their isolation is required, even for tests.
- OpenCode runs under a workspace sandbox and a provider domain allowlist.
  Its commands, network tools, external directories, MCP tools and recursive
  delegation are denied by default. Project/global configuration is isolated;
  external plugins, formatters and language servers are disabled. Existing
  provider credentials are copied only into a temporary private runtime and
  removed when the child exits. The model provider still receives the task
  and relevant source text. Use only trusted configured providers.
- Windows Job Objects kill child processes if the worker exits; Unix process
  groups are terminated on exit. Jobs have configured time limits (30–1800
  seconds per agent/check), a 1 MiB output limit, and a 1 MiB captured diff limit.
  Worktrees consume disk until the operator removes reviewed ones. Worker
  restart marks interrupted jobs failed and never automatically replays them.
- Captured logs, diffs and checks are operator-private data in the persistent
  workspace database. They are escaped in HTML and covered by the existing
  Tailscale identity boundary and CSRF protection. The daily backup keeps
  copies of that database outside the repository.
- UEFN exposes only identity inspection and native `BuildAll`. It checks the
  loaded plugin directory and current world against the configured project
  before requesting a build. No arbitrary prompt becomes editor code or a
  tool name. Builds report actual diagnostics; a clean build is not gameplay
  acceptance. Canceling cannot undo an editor operation already received.
  Scene edits, device changes, launching sessions and publishing are excluded.

The existing loopback editor bridges on ports 8000 and 8765 remain local.
They are not forwarded through HQ. The adapter uses fixed requests with
timeouts, rejects redirects, and ignores system HTTP proxy settings.

Sandbox reference: [official OpenAI Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox).


## Coding profiles and routing

New coding queues default to `routine`: `gpt-6-luna`, reasoning effort `high`.
Choose `complex` for `gpt-6.1-sol` / `high`, or `orchestration` for the same Sol
route with optional bounded children. Routine and complex runs cannot dispatch
children. There is no Sol planning or review call around routine runs.
The queue form and the `gamedev.run.queue` capability (including the host CLI
adapter) accept only the profile, not a model, executable or argument array.
Editor inspections, builds, apply and rollback never invoke a coding model.

Older operator policies work unchanged. An optional trusted local `routes`
object can set each profile's effort to `low`, `medium` or `high`; unknown
keys, efforts and models fail validation. Routine must select Luna and the
other profiles must select Sol. For example:

```json
"routes": {
  "routine": {"model": "gpt-6-luna", "effort": "high"},
  "complex": {"model": "gpt-6.1-sol", "effort": "high"},
  "orchestration": {"model": "gpt-6.1-sol", "effort": "high"}
}
```

The worker records requested profile, selected model, effort and a fixed local
policy reason durably before invocation. It passes `--model` and
`-c model_reasoning_effort=...` explicitly while retaining ignored user config,
workspace-write, the existing Windows sandbox policy, disabled command network
and never approvals. Selected means requested from Codex; it is not evidence
of the provider's actual served model. Actual model remains unavailable until
a supported provider report can establish it. Historical selections are blank
and displayed as unknown; they are never reconstructed from current defaults.
Historical retries preserve that unknown profile and fail closed: queue fresh
work with an explicit profile rather than guessing what the old run requested.

Child payloads accept optional `agent` (`codex` or `opencode`) and `profile`
(`routine` only). Old title/prompt payloads default to OpenCode/routine. New
Codex children require a Sol orchestration parent; old historical OpenCode
provenance remains compatible. The maximum is two children total, not two per
engine. Each runs in a separate retained worktree with the configured timeout,
output/diff limits and checks. Children have no application or delegation
privileges. Independently inspect and accept every child; parent acceptance
still waits for all child tasks to finish. No changes are silently merged.

## Explicit escalation

For a failed or rejected routine coding run, the retry form can request one
complex/Sol attempt, or a caller may send `escalate: true` to
`gamedev.run.retry`. This requires `gamedev.write`, the current run version,
a nonblank review note and no queued/running/review work for the task. The new
run links both its retry source and escalation root and requires its own review.
Ordinary retries preserve the requested profile and delegation lineage.

Only one escalation is permitted per task, including across ordinary retries.
The escalation attempt itself cannot be retried. Editor, apply, rollback,
delegated, canceled, non-routine and previously escalated jobs are ineligible.
Cancellation requests, recorded quota fallbacks and failure output indicating
quota, authentication, credentials or sandbox problems also block escalation;
this conservative text guard can require manual diagnosis. No failure triggers
a Sol retry automatically, and escalation never accepts or applies a result.
The existing explicitly configured quota/OpenCode fallback remains a separate
mechanism and is shown distinctly from escalation and delegation.

## Usage, privacy and migration order

Run details show selected routing, reported usage and linked retry, escalation
and delegation history. Mission Control uses bounded projections with the route
fields selected in the existing queries; it does not load prompt/log/diff data.
Token counts are provider-reported tokens, not subscription credits or USD.
Missing usage and costs remain unavailable. No percentage savings are promised.
Trace routing metadata contains fixed route values and numeric run IDs; export
uses an explicit attribute allowlist, excluding prompts, source, commands,
logs and secrets. Existing provider-reported usage is retained, never estimated.

Deployment is a separate operator operation: stop the worker, back up the
workspace using its supported backup procedure, install the coherent code,
then migrate the workspace through `gamedev_hq.0010_run_routing` before starting
the new web process or worker. The startup health wait remains required. Do not
copy a subset of Python files into an unmigrated deployment. Validate policy
and sandbox compatibility before enabling queues.

For rollback, stop workers and web writers first. Retain the backup and reviewed
worktrees. Restore the previous code while retaining the additive columns if
possible; the older model ignores them. Do not run an old worker against queued
profile-routed work: it ignores profiles and model selection. Drain or cancel
such queues under the new code first. Reversing migration 0010 deletes routing
and escalation history, so prefer retaining it; a full database restore also
loses all runs and audit events written since that backup. No migration or
rollback of a live database is part of this change.
