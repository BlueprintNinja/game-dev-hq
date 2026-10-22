# Game Dev HQ v0.1

This is a reconstructed foundation, not a recovered copy of the prior chat
artifact. The available conversation established Windows 11 hosting, private
Tailscale access, GitHub Actions, Codex as primary agent, and OpenCode only for
delegated tasks. Its implementation attachment was unavailable.

## What works

- Projects with an engine and a local workspace/repository reference.
- Tasks owned by Codex by default; OpenCode requires a Codex parent task in
  the same project. Completed parents cannot receive new delegations.
- Explicit queued → active → review → done transitions, blocked → queued
  recovery, evidence required at completion, and child completion before parent
  completion. Review may return to active; any unfinished state may be blocked.
- SQLite persistence and an audit event committed atomically with every write.
- The board is a Game Dev extension inside the upstream HQ application: it uses
  HQ's own database, permissions, capability gates and audit trail, and its
  projects are native HQ projects. It lives at `/game-dev/` of the live
  workspace.
- A read-only loopback preview of the same data, with escaped user content, no
  external assets or scripts, and restrictive browser headers.
- A loopback-only listener with an explicit host allowlist and operator login
  allowlist, using the identity supplied by Tailscale Serve.
- Windows/Linux tests on GitHub-hosted runners. No workflow reaches the UEFN
  workstation, receives tailnet credentials, or deploys unreviewed PR code.

This companion is a separate application, not a host domain or SDK extension.
The upstream host remains unaware of game-specific vocabulary. The Linux
controller, secret renderer, OIDC configuration, capability gates, signed image
deployment and upstream workflows have not been replaced. `LICENSE` and the
upstream security reporting policy remain authoritative for inherited code.

## Local CLI (Python 3.11 or newer)

Run from the repository root. No packages need to be installed. The default
database is `var/gamedev/hq.sqlite3` (gitignored). Keep private paths, plans,
operator logins and all runtime data out of Git.

```powershell
python -m gamedev init
python -m gamedev project "Island prototype" --repository "D:\Projects\Island" --engine UEFN
python -m gamedev task 1 "Build the first playable loop"
python -m gamedev task 1 "Review Verse API options" --agent opencode --parent 1
python -m gamedev move 2 active
python -m gamedev move 2 review
python -m gamedev move 2 done --evidence "Local review completed; findings recorded in the project"
python -m gamedev snapshot
```

IDs are printed by creation commands; substitute the returned IDs. Use
`--database PATH` **before** the subcommand to select another database. Back up
the SQLite file while the CLI and preview are stopped. Restore to a separate
path first and check `snapshot` before replacing a working database. Database
access is local-account access; protect its folder with Windows user ACLs.
The audit log is operational history, not tamper-proof against that account.

## Windows checkout

Upstream contains one systemd mount filename with literal backslashes, which
Windows cannot materialize. Exclude that one file with a sparse checkout
rather than renaming or deleting Linux deployment files:

```powershell
git clone --no-checkout https://github.com/BlueprintNinja/game-dev-hq.git
Set-Location game-dev-hq
git config core.protectNTFS false
git sparse-checkout init --no-cone
@('/*', '!/deploy/systemd/run-severino*') | git sparse-checkout set --no-cone --stdin
git checkout feature/gamedev-foundation
```

The setting allows the upstream filename in the index; the excluded file is
never checked out. Keep this exception scoped to this checkout.

Every other file under `deploy/systemd` must stay in the checkout: the
application reads the renderer's timer while it starts, and a checkout without
it cannot start at all.

## What runs on the workstation

Two services, both on loopback, both reachable only through Tailscale Serve:

| Service | Listener | Private URL | Holds |
| --- | --- | --- | --- |
| Live workspace | `127.0.0.1:18767` | `https://YOUR-MACHINE.YOUR-TAILNET.ts.net:9443/` | Real data: projects, tasks, audit history, the Game Dev board at `/game-dev/` |
| Sample showcase | `127.0.0.1:18766` | `https://YOUR-MACHINE.YOUR-TAILNET.ts.net:9444/` | The upstream demo export with its example data, built from source |

The showcase is a complete upstream build, so it can be explored without
touching the workspace. The workspace is the one that persists changes: a task
saved in the browser is a row in the workspace database and survives a restart.

Both are started by Task Scheduler tasks under the operator account:

| Task | Script | Runs |
| --- | --- | --- |
| `GameDevHQ-Workspace` | `deploy/windows/Start-GameDevWorkspace.ps1` | At logon, restarted on failure |
| `GameDevHQ-Showcase` | `deploy/windows/Start-GameDevShowcase.ps1` | At logon, restarted on failure |
| `GameDevHQ-Worker` | `deploy/windows/Start-GameDevWorker.ps1` | At logon, once the workspace answers; claims queued work |
| `GameDevHQ-Backup` | `deploy/windows/Backup-GameDevWorkspace.ps1` | Daily; one consistent copy of the workspace database |

The worker and the backup belong to supervised work; `docs/Game-Dev-Execution.md`
says how to install them and what they refuse to do.

Files the services own, all outside the repository:

| Path | Holds |
| --- | --- |
| `%LOCALAPPDATA%\GameDevHQ\workspace\hq.sqlite3` | The workspace database |
| `%LOCALAPPDATA%\GameDevHQ\workspace\secret.key` | The workspace's Django secret |
| `%LOCALAPPDATA%\GameDevHQ\workspace\static`, `media` | Collected assets and uploads |
| `%LOCALAPPDATA%\GameDevHQ\backups` | The workspace database's daily copies |
| `%LOCALAPPDATA%\GameDevHQ\logs\workspace.log` | Workspace start and request log |
| `%LOCALAPPDATA%\GameDevHQ\logs\showcase.log` | Showcase start log |
| `%LOCALAPPDATA%\GameDevHQ\logs\worker.log` | Worker log |
| `%LOCALAPPDATA%\GameDevHQ\logs\backup.log` | Backup log |

Nothing else is written outside the repository: the showcase is served from the
build under `var/demo`, and the companion's own database stays in `var/gamedev`.

## Runbook

### First install

1. Sign in to Tailscale on the workstation and on every device that should
   reach the workspace. Keep MagicDNS and HTTPS enabled for the tailnet; HTTPS
   setup may need tailnet-admin approval.
2. Install the pinned dependencies and build the sample showcase:

   ```powershell
   ./deploy/windows/Build-GameDevShowcase.ps1
   ```

   The script installs the pinned Python environment, exports the upstream demo
   and leaves it under `var/demo`. Add `-Check` to also verify the exported
   site in a browser.

3. Register the two startup tasks:

   ```powershell
   ./deploy/windows/Install-GameDevStartup.ps1
   ```

   The script resolves PowerShell, `uv`, the data directory and the log
   directory once, and pins them into the task actions. It prints both paths.
   **Install the tasks from a normal PowerShell window, not from a packaged
   application**: a packaged host (the Codex desktop app, for one) redirects
   `LOCALAPPDATA` into its own cache, and the workspace would then start
   against a different, empty directory on the next logon.

4. Start both tasks and check them:

   ```powershell
   Start-ScheduledTask -TaskName 'GameDevHQ-Workspace'
   Start-ScheduledTask -TaskName 'GameDevHQ-Showcase'
   ./deploy/windows/Get-GameDevStatus.ps1
   ```

5. Point the tailnet at the two listeners:

   ```powershell
   ./deploy/windows/Enable-GameDevWorkspace.ps1
   ```

   The script refuses to replace a 9443 or 9444 route it does not recognize,
   refuses to run while Funnel is enabled, and checks that both listeners
   answer before it changes anything. Existing routes on other ports stay
   untouched.

6. Open `https://YOUR-MACHINE.YOUR-TAILNET.ts.net:9443/` from another signed-in
   device. The board must load for the configured user and return 403 for a
   different user. Confirm access fails from a device outside the tailnet.

### Daily operation

- **Start after a reboot or a sign-out**: the tasks start at logon. If they
  did not run, `Start-ScheduledTask -TaskName 'GameDevHQ-Workspace'` and
  `Start-ScheduledTask -TaskName 'GameDevHQ-Showcase'` start them by hand.
- **Restart one service**: stop the task, then start it again:

  ```powershell
  Stop-ScheduledTask -TaskName 'GameDevHQ-Workspace'
  Start-ScheduledTask -TaskName 'GameDevHQ-Workspace'
  ```

  The workspace re-collects its static assets and re-runs its migrations on
  every start, so a restart is always a full, clean start.
- **Stop everything**: `./deploy/windows/Stop-GameDevServices.ps1`. It stops
  the two tasks and the loopback listeners they own, and leaves every other
  process and every tailnet route alone.
- **Backups**: `GameDevHQ-Backup` writes one copy of the workspace database
  into `%LOCALAPPDATA%\GameDevHQ\backups` every day and keeps the newest
  fourteen. The copy is made after the write-ahead log is folded into the
  database, so one file holds everything. Run
  `./deploy/windows/Backup-GameDevWorkspace.ps1` to take one now.
- **Check what is running**: `./deploy/windows/Get-GameDevStatus.ps1` prints the
  task states, the listeners, both health checks, the private URLs and the last
  lines of each log.

### When something is wrong

1. Read the log for the service that is wrong:
   `%LOCALAPPDATA%\GameDevHQ\logs\workspace.log` or `showcase.log`. Every start
   records what it did, and every failure records the reason.
2. `Get-GameDevStatus.ps1` shows whether the listener is up at all. A listener
   that is up but a page that fails points at the data or the database; no
   listener at all points at the start script.
3. The workspace waits for Tailscale to sign in (up to two minutes) before it
   gives up, so a logon during a Tailscale reconnect is not a fault.
4. The workspace collects its static assets into a clean tree on every start.
   The upstream storage deletes the hashed copies it finds, so collecting twice
   over the same tree would empty it; the start script therefore clears the
   tree first. If a start fails with a missing file under `static`, check that
   the tree was collected rather than reused.
5. The showcase serves the export under `var/demo`. If it refuses to start with
   "the sample showcase is not built", run `Build-GameDevShowcase.ps1` again.

### Updating to a newer commit

```powershell
git fetch origin
git checkout main
git pull
./deploy/windows/Stop-GameDevServices.ps1
Start-ScheduledTask -TaskName 'GameDevHQ-Workspace'
Start-ScheduledTask -TaskName 'GameDevHQ-Showcase'
```

Stop the services before switching commits: the workspace holds a SQLite file
that a schema change may migrate. Back up the workspace database first
(`./deploy/windows/Backup-GameDevWorkspace.ps1`); the
v0.1 schema is additive and initialization is idempotent, but a future schema
change needs an explicit migration.

### Removing the workstation deployment

```powershell
./deploy/windows/Stop-GameDevServices.ps1
tailscale serve --https=9443 off
tailscale serve --https=9444 off
Unregister-ScheduledTask -TaskName 'GameDevHQ-Workspace' -Confirm:$false
Unregister-ScheduledTask -TaskName 'GameDevHQ-Showcase' -Confirm:$false
```

Disable only these two endpoints; never `tailscale serve reset`, which would
remove the other routes on this machine. The workspace data directory is left
in place so it can be restored.

## Private preview

The standalone read-only preview of the companion's own database
(`deploy/windows/Start-GameDevPreview.ps1`, port 18765) remains available for
use without the full workspace. It is not part of the installed deployment:
the live workspace replaces it, and the tailnet route on 9443 points at the
workspace.

The URL remains stable while the machine's MagicDNS name and port remain the
same. Renaming/removing the node changes it. No public DNS or public hosting is
required. A local `/healthz` endpoint returns only `ok`; all data and assets
require an allowed `Tailscale-User-Login`. Serve supplies that identity and
removes spoofed identity headers. Tagged devices without a user identity are
denied. Local processes can supply proxy headers, so the workstation and its
local accounts are inside the trust boundary. Do not put another proxy in
front of the listener or expose ports 18766 or 18767 on the LAN. UEFN uses
local port 8765; nothing in this deployment may take it.

References: [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve),
[Serve examples](https://tailscale.com/docs/reference/examples/serve),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use).

## Verification and updates

```powershell
python -m unittest discover -s gamedev/tests -v
python -m gamedev --help
uv run --locked --no-default-groups python -m tools.windows_hq.check
```

`mise run gamedev:check` runs the companion suite and `mise run
gamedev:workspace-check` the workspace suite in a throwaway composition that
never opens the operator's data. `./deploy/windows/Build-GameDevShowcase.ps1
-Check` builds the showcase and verifies it in a browser. Existing host checks
remain `mise run fast`, `mise run check`, and `mise run preflight`; they require
the upstream pinned toolchain, Linux/container support and configured host.
Passing companion tests does not establish that the inherited host deploys.

CI uses only GitHub-hosted Windows/Linux machines with a read-only token and
no persisted checkout credentials. Workstation deployment is a local pull of
a reviewed commit after CI; it is deliberately not a PR-triggered self-hosted
runner.

## Deliberate v0.1 limits

The task board records agent assignments; it does not invoke Codex/OpenCode,
spend API credits, accept remote writes, store credentials, launch UEFN, edit
islands, or run shell commands. UEFN assets/Verse integrations, model execution
with explicit approvals, GitHub synchronization, and an SDK extension for the
full host are future work. Refresh the page to see local CLI changes. No
claim of full upstream preflight or remote-device isolation is implied by a
successful local health check.
