# Native Game Dev attention candidate

Status: run 31 code for review only, pending review and local application. No
commit, push, deployment, editor operation or model-routing change is included.

The plugin supplies `PluginIntegration(attention=attention_items)` with a tuple
of frozen SDK Insights. HQ derives it once per observed database change and
keeps it for at most one minute. It reads local rows and the clock only: no
principal, CLI identity, files, environment, Phoenix queries, raw logs, network
calls or side effects. GET links lead to existing guarded forms and evidence
pages; it adds no direct retry, approval, apply or rollback action.

## Shared-summary privacy boundary

The host's home and Needs you queue are global read surfaces. Their attention
callback has no principal and its answer is shared and cached globally, like
existing dashboard counts. These summaries intentionally reveal category,
counts, run IDs and timestamps across all projects. They are not per-plugin
authorization or a new tenancy model. No task titles, project names/slugs,
prompts, source, diffs, outputs, command text, machine paths, resource payloads
or credentials enter these Insights. SQL computes only the nonempty-diff
boolean; raw diff content is not selected. Heartbeat counts never expose a
project identifier, and collector readings and trace attributes are not read.

Run and Mission Control pages retain `gamedev.read` (Mission Control also
requires host `read`); metrics retains `gamedev.metrics.read`; trace retains
`gamedev.traces.read`. A user without those grants can see sanitized shared
summaries but receives 403 at those destinations. Project selection remains a
filter, not tenant isolation.

## Decisions and bounds

Mission Control and native attention share `attention_query`, `current_query`
and `attention_kind`. Current task/run relationships across all history decide
what matters: latest work per task and latest apply/rollback per source and
kind, only while their source remains current. Successful later apply clears
its failed predecessor and removes the accepted source's unapplied decision.
Rejected, completed and superseded work does not raise a current decision.
Completed validation run 30 is not identified by a special-case ID exclusion.

Each current matter has `run:<id>` as its stable key, magnitude 1 and a generic
family. Coding review with saved changes, accepted changes awaiting apply,
verification/attempt failures and results needing acknowledgement remain distinct.
Empty/whitespace diff with passing checks is a result to acknowledge, never
changes ready. Passing checks never assert completed gameplay. `observed_at`
is the row's last update; `since` is omitted because an update timestamp does
not prove when a status transition happened.

One SQL current-decision query returns at most 101 rows; only 100 are emitted.
An overflow decision (magnitude 1: one coverage-review matter) explicitly says more work exists and links to
the board. Twenty trace-export run groups are shown, using one sentinel group
to detect overflow and a metrics link for further evidence. Export magnitude
counts qualifying spans, not runs; worker magnitude counts affected projects.
Missing collector magnitude 1 represents one missing local collector stream.
The callback uses five queries independent of project/run/span history size.
Aggregates and correlated latest-run predicates operate across full history;
row transfer and payload are bounded, not database scan time. Failures are
ordered before coding changes and acknowledgements before applying the limit. Existing board
and metrics navigation provides the complete workflow; no new list or endpoint.

## Fixed operational thresholds

- Worker heartbeat: stale strictly beyond 90 seconds. Projects with tasks
  older than 90 seconds and no profile have a missing heartbeat. New tasks get
  90 seconds for first publication. No profiles and no overdue work produces
  a neutral unknown observation, not a health assertion or startup alarm.
- Collector: no local sample means missing/unknown. Latest observation is
  stale strictly beyond 90 seconds. Only its timestamp is selected.
- Trace delivery: an unexported finished span qualifies strictly beyond five
  minutes with at least three attempts, or strictly beyond ten minutes of
  backlog regardless of attempts. A normal brief backlog, an unfinished span
  or a first failure at six minutes does not qualify. Exported rows clear the
  issue regardless of historical attempts. Export summaries link to existing
  per-run traces; other operational summaries link to metrics.

Fresh worker/sample rows emit no alarm and do not imply overall health.
Freshness `since` is the last observation plus 90 seconds; the oldest affected
heartbeat supplies the aggregate start/observation. Missing rows have unknown
start and observation. Export summaries omit `since`: there is no durable
export-failure transition time. Their observation is the latest finished time
among qualifying local spans. Clock-driven state can lag by the host's minute
TTL. No plugin cache, timer or collector is introduced.

## Verification evidence

`tools/windows_hq/tests/test_attention.py` exercises the real composed SDK
`plugin_attention_items`, live home, provider reuse and write invalidation,
pickling, category distinctions, all projects, recovery, bounded full-history
queries, sanitization with private sentinels, capability-denied destinations,
worker/sample freshness and exporter grace/repeated failure/recovery. Home
GET checks forbid SQL writes, processes and socket connections after the
normal authentication session and host derivation cache have been warmed.
The host may persist a cold derived answer in its own cache table; the callback
never writes domain state, and its five direct SQL queries are read-only.

Execution results and limitations are recorded below after the requested
workspace composition, SDK validation and cached Ruff checks. Logs are under
ignored `var/attention-*.log`; no dependencies are installed. `mise` is
unavailable on this machine, so its fast/check/preflight gates are not claimed.

Executed in this checkout with the requested absolute Python executable and
TEMP/TMP/TMPDIR set to checkout `var/tmp`:

- `python -m tools.windows_hq.check`: 109 tests, OK, no skips; 9.828 seconds,
  exit 0. Full workspace composition; system checks report no issues.
- `python -m hq_sdk.validation gamedev/hq_plugin`: import/template boundaries
  passed, exit 0.
- Cached Ruff 0.17.1 `check gamedev/hq_plugin` plus the native-attention tests:
  all checks passed, exit 0; `format --check` reports 32 files formatted,
  exit 0. No tool installation or network access was used.

The final passing composition includes the overflow/priority regressions in
`test_attention_limits.py`. Ordinary attention contributes to home's Needs you
count and is rendered on `/action-items/`; the existing host shows only serious
items inline on home. Both actual surfaces are exercised without changing host
queue policy. The native attention candidate remains pending review.
