# Private Phoenix harness

The workspace exposes `/game-dev/harness/` and a metadata trace page for each
run. A separate collector samples the workstation every 30 seconds and exports
completed spans to Phoenix over authenticated loopback OTLP/HTTP. Export failure
retains the local spans for retry and never blocks a dashboard request.

## Available readings

| Source | Readings | Limits |
| --- | --- | --- |
| HQ runs | Status, agent engine, retries, delegations, fallbacks, apply and rollback counts | Project and 1-hour/day/week/30-day filters |
| Timing | Execution median/p95, queue p95, approval median | Only runs with recorded timestamps; latest 1,000 detail records |
| Verification | Check kinds and pass/fail counts | Compilation does not prove gameplay |
| Agent output | Reported prompt/output/cache/reasoning tokens, tool events, reported cost | Unknown values remain unavailable; cost is not an invoice |
| OpenCode sessions | Per-provider/model reported tokens, cost, session/message counts | Read-only usage columns for enrolled directories; last 24 hours, newest 10,000 assistant messages globally |
| Workstation | CPU, memory, disk, network totals/rates, uptime, process count | CPU temperatures depend on available sensors |
| NVIDIA GPU | Utilization, memory, temperature, power | Requires `nvidia-smi`; unsupported values remain unavailable |
| Local services | Fixed port connectivity; PID, memory, threads, cumulative CPU | Listener connectivity is not application health |
| Operations | Fixed scheduled-task states, backup age/size, worker heartbeat | Stale readings are explicitly labeled |
| Trace delivery | Recorded/pending spans, attempts, export errors | Error class only; no remote response bodies |

Infrastructure is workstation-wide even when the run filter selects one project.
OpenCode session totals are separate from HQ run totals and must not be added to
them. Historical sessions do not acquire synthetic tool traces. No billing quota,
unreported model internals, gameplay results, or arbitrary external service metrics
are invented.

## Access and privacy

HQ retains its Tailscale identity allowlist and CSRF protections. Metrics require
`gamedev.metrics.read`; traces require `gamedev.traces.read`. Reading metrics grants
no execution or file-apply authority. Existing approval fingerprints and apply
scope restrictions remain in force. Metrics access is currently workstation-wide,
not a per-project tenancy boundary.

Phoenix uses its own authentication and ADMIN/MEMBER/VIEWER roles. A Phoenix role
does not grant HQ capabilities. Native Phoenix project visibility must be treated
as shared within that instance; use separate instances for strict project isolation.
The ingestion key is kept in a private local file and is never sent to browsers.
Exports contain identifiers, timings, usage and outcomes, excluding prompts,
command text, file content, raw logs, review notes and credentials. HQ's original
run logs and evidence remain protected by their existing access controls.

## Windows deployment

1. Keep Phoenix in a separate Python 3.13 environment. Install the hashed
   `deploy/phoenix/requirements.lock` with `--require-hashes`; do not add Phoenix's
   dependency stack to the HQ Python 3.15 environment.
2. Store `server.json`, `collector.json` and keys outside Git. Configure native
   authentication, a strong secret/admin password, secure cookies, disabled
   telemetry and no sandbox/agent providers. Restrict these files to the operator
   and SYSTEM using Windows file permissions.
3. Run `deploy/phoenix/server.py --config <server.json>` with that environment.
   This version-pinned adapter disables Phoenix's wildcard gRPC listener and
   serves HTTP only on `127.0.0.1:6006`. Revalidate it before upgrading Phoenix.
4. Back up the HQ SQLite database, deploy migrations and initialize the workspace
   before starting the collector or worker. Never point an old worker at new
   schema or migrate while a worker is running.
5. Register the current operator's limited, at-logon scheduled tasks with
   `Install-GameDevObservability.ps1`, specifying the config directory, Phoenix
   Python path and pinned uv path. Start Phoenix first, then Telemetry after HQ
   is ready. The tasks require the operator's logged-in session.
6. `Enable-GameDevPhoenix.ps1` checks native authentication before routing
   tailnet-only HTTPS 9445 to loopback 6006. It refuses Funnel and unfamiliar
   routes and leaves existing service routes intact. Confirm no wildcard 6006
   or 4317 listener exists. Tailscale ACLs govern who can reach that port;
   Phoenix login is an additional boundary.

Collector config includes `endpoint`, `api_key_file` and optionally
`opencode_database` plus explicit `session_roots`. Only loopback HTTP export
targets are accepted; redirects and proxy environment variables are ignored.

Infrastructure samples retain seven days in HQ. Spans follow run retention and
Phoenix's configured retention separately; monitor disk usage. Back up both HQ
and Phoenix databases with SQLite's online backup API, not a live file copy.
Keep private configuration/key backups outside the repository. The existing HQ
backup task does not automatically back up Phoenix. For rollback, stop collector
and worker, restore the matching code/database backup, then restart the workspace
before its worker. Preserve Phoenix data independently.
