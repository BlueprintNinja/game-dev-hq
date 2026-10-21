# Supervised Game Dev work

The saved Game Dev task board can queue a Codex job, show its log while it
runs, and retain the starting revision, diff, check results and review decision.
The local worker is a separate process. Web requests never start commands.

## Local policy

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
its repository/check settings are unused for those two fixed actions.

Initialize/migrate the workspace, then start the worker using
`deploy/windows/Start-GameDevWorker.ps1 -PolicyPath <absolute-policy-path>`.
Pass the same explicit `DataDirectory`, `LogDirectory` and `UvPath` as the
workspace startup. Run under the signed-in operator with existing Codex login.
No API key is stored by HQ. For one queued job, the equivalent Python entry
point is `python -m gamedev.worker --policy <path> --once` with the workspace
environment configured. Only one worker may hold the workspace lock.

For startup at logon, register this script as `GameDevHQ-Worker`, using a
limited interactive principal, hidden PowerShell, explicit absolute paths,
one instance, and restart on failure. Start it after the workspace migration
has completed. Do not create a public listener or a Tailscale Funnel route.

## Execution and review

1. Create a task assigned to Codex, expand it, enter work instructions and
   select **Start work**. Select an editor action instead for a UEFN check.
2. Follow the run link. Its progress refreshes through HQ's shared page
   refresh mechanism. Canceling requests process termination.
3. Review the captured diff and every independent check. Accept requires
   passing checks, completed delegated tasks and a review note. A failed
   check cannot be accepted. Decisions reject stale versions and are audited.
4. Move the task through review to done after acceptance. Acceptance records
   approval of captured evidence; it does not apply, commit, push, merge or
   publish changes. Retained worktrees remain available for local inspection.

Codex receives a bounded structured response schema. It may request at most
two OpenCode children. The worker records the originating Codex run and creates
the children in the same project. A web-created OpenCode assignment cannot
launch itself. Each child gets its own worktree, check run and independent
review. Child changes are not automatically combined into the parent; review
and integrate them explicitly. Recursive OpenCode delegation is disabled.

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
  Tailscale identity boundary and CSRF protection. Take database backups.
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
