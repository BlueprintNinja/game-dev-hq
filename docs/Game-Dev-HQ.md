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
- A responsive, server-rendered read-only board and JSON snapshot, with escaped
  user content, no external assets or scripts, and restrictive browser headers.
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

Upstream contains a systemd mount filename with literal backslashes, which
Windows cannot materialize. Preserve it with a sparse checkout rather than
renaming or deleting Linux deployment files:

```powershell
git clone --no-checkout https://github.com/BlueprintNinja/game-dev-hq.git
Set-Location game-dev-hq
git config core.protectNTFS false
git sparse-checkout init --no-cone
@('/*', '!/deploy/systemd/') | git sparse-checkout set --no-cone --stdin
git checkout feature/gamedev-foundation
```

The setting allows the upstream filename in the index; the excluded systemd
directory is never checked out. Keep this exception scoped to this checkout.

## Private preview

1. Install Python 3.11+, sign in to Tailscale on the Windows workstation and
   the viewing device, and enable MagicDNS/HTTPS for the tailnet. Keep the
   workstation awake. HTTPS setup may require tailnet-admin approval.
2. Obtain `Self.DNSName` and your login from `tailscale status --json`. These
   are runtime values; do not commit them. In an ordinary PowerShell window:

   ```powershell
   .\deploy\windows\Start-GameDevPreview.ps1 `
     -OperatorLogin 'YOUR-TAILSCALE-LOGIN' `
     -DnsName 'YOUR-MACHINE.YOUR-TAILNET.ts.net'
   ```

   The script uses `%LOCALAPPDATA%\GameDevHQ\hq.sqlite3`. To use the same data
   from the CLI, pass that path with `--database`. It stays in the foreground;
   Ctrl+C stops it. Pin the repository revision you reviewed before starting.
3. In an administrator PowerShell window, run:

   ```powershell
   .\deploy\windows\Enable-GameDevTailnet.ps1
   ```

   This refuses an existing 9443 Serve route or enabled Funnel. It configures
   **only** HTTPS 9443 → `http://127.0.0.1:18765`. Existing routes remain intact.
   The script prints `https://YOUR-MACHINE.YOUR-TAILNET.ts.net:9443/`.
   Serve's background configuration persists across reboots; the Python
   process must also be restarted. For unattended use, create a Task Scheduler
   task under the dedicated operator account, triggered at logon, to run the
   start script with absolute paths, with restart-on-failure enabled. Do not
   run it as SYSTEM or use a repository Actions runner on the workstation.
4. Restrict tailnet ACLs/grants to the operator devices and TCP 9443. Open the
   printed URL from another signed-in device. The board must load for the
   configured user and return 403 for a different user. Confirm access fails
   from a device outside the tailnet. Do not enable Funnel, router forwarding,
   a public tunnel, or a listener on `0.0.0.0`.

The URL remains stable while the machine's MagicDNS name and port remain the
same. Renaming/removing the node changes it. No public DNS or public hosting is
required. A local `/healthz` endpoint returns only `ok`; all data and assets
require an allowed `Tailscale-User-Login`. Serve supplies that identity and
removes spoofed identity headers. Tagged devices without a user identity are
denied. Local processes can supply proxy headers, so the workstation and its
local accounts are inside the trust boundary. Do not put another proxy in
front of the listener or expose port 18765 on the LAN.

References: [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve),
[Serve examples](https://tailscale.com/docs/reference/examples/serve),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use).

## Verification and updates

```powershell
python -m unittest discover -s gamedev/tests -v
python -m gamedev --help
```

`mise run gamedev:check` runs the same companion suite. Existing host checks
remain `mise run fast`, `mise run check`, and `mise run preflight`; they require
the upstream pinned toolchain, Linux/container support and configured host.
Passing companion tests does not establish that the inherited host deploys.

CI uses only GitHub-hosted Windows/Linux machines with a read-only token and
no persisted checkout credentials. Workstation deployment is a local pull of
a reviewed commit after CI; it is deliberately not a PR-triggered self-hosted
runner. Stop the preview, back up the database, switch to the reviewed commit,
run the suite, then restart the start script. The v0.1 schema is additive and
initialization is idempotent; future schema changes need explicit migrations.

To roll back the route, disable only this endpoint (not `serve reset`):

```powershell
tailscale serve --https=9443 off
```

Stop the preview with Ctrl+C (or stop its specific scheduled task). Preserve
the database, switch to the previous reviewed commit, and restart. This
companion does not install a service, modify Windows Firewall, or schedule
itself automatically.

## Deliberate v0.1 limits

The task board records agent assignments; it does not invoke Codex/OpenCode,
spend API credits, accept remote writes, store credentials, launch UEFN, edit
islands, or run shell commands. UEFN assets/Verse integrations, model execution
with explicit approvals, GitHub synchronization, and an SDK extension for the
full host are future work. Refresh the page to see local CLI changes. No
claim of full upstream preflight or remote-device isolation is implied by a
successful local health check.
