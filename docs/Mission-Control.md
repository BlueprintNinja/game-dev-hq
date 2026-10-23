# Project Echo Mission Control: capability audit and implementation contract

Status: source audit and implementation contract, October 23, 2026. A tested candidate exists in HQ run 23; Mission Control is not deployed.
The existing task board and harness remain the supported interfaces. This document
sets the first implementation boundary; it does not claim dataset or model experiments exist.

## Release baseline

PR #8 is open, non-draft, mergeable, and unmerged at 87fab910f1115c56ee34d7dd8aaa26a2606b51cf.
The foundation workflow passed. Full CI run 39887385317 was canceled in the layout
browser job at the 15-minute job limit; its other execution, security, image,
navigation/demo and composition checks passed. The layout job was restarted during
this audit and subsequently passed, as did the final Ready check. That verifies
87fab91; the new Mission Control candidate has not been committed or run in remote CI.

The preview also contains a local, uncommitted Verse LF/CRLF apply compatibility
fix with 64 passing workspace tests. The remote head does not contain that fix.
Validation of the remote commit cannot certify this deployed working-tree change.
Do not merge, change required checks, increase timeouts, or call the release ready
merely to remove the dependency.

## Capability inventory

| Existing feature | Decision | Concrete reuse or change |
| --- | --- | --- |
| Native projects and resource API | Reuse | Resolve project names and selectors through `hq_sdk.resources`; do not create a second project registry. |
| Task ownership, Codex/OpenCode hierarchy | Reuse | `gamedev/hq_plugin/models.py` Task/Run IDs remain the identities for activity, decisions and evidence. |
| Run queue, review, retry, apply, rollback | Reuse | Link to existing command forms and capability handlers. No new action semantics in dashboard views. |
| Fingerprints, scoped destinations, snapshots | Reuse | Show readiness and destination; acceptance and apply remain separate audited operations. |
| Kpi, Insight, ListRow, timeline primitives | Reuse | `hq_sdk.ui` and shared partials render the overview; no dashboard framework, CSS bundle or new client state store. |
| Dashboard contribution and navigation | Adapt | `gamedev/hq_plugin/plugin.py` already contributes cards. Add a Mission Control destination and link from the existing board. Preserve old URLs. |
| Board and run pages | Adapt | Keep detailed creation/review workflows; overview routes readers to the exact run, not a duplicate decision form. |
| Metrics snapshot | Adapt | `gamedev/hq_plugin/metrics.py` supplies bounded cohorts, usage coverage and freshness; separate operations, checks and gameplay claims. |
| TraceSpan and trace view | Reuse | Read the local durable projection; do not synchronously query Phoenix on page requests. Keep trace/run identity links. |
| Phoenix exporter and credentials | Leave alone | Continue sanitized asynchronous loopback export. No keys or new Phoenix privileges in browser requests. |
| Worker and infrastructure heartbeats | Adapt | Show current/stale/unknown plus timestamps; freshness alone is not an overall healthy verdict. |
| CPU/GPU, memory, process, disk collectors | Leave alone | Existing readings support exception summaries. No new collector in this milestone. |
| Backup and scheduled-task samples | Adapt | Show backup age as evidence; current collector covers HQ backup files, not Phoenix backup completeness or restore success. |
| Host reports, website analytics, unrelated providers | Leave alone | Keep their original purpose. Do not inject game-specific vocabulary into host domains. |
| Evaluations, datasets, prompt replay | New later | Separate curated, reviewed data path with reproducibility and explicit export approval. No automatic conversion of sanitized traces. |

Source entry points: `gamedev/hq_plugin/views.py`, `metrics.py`, `metrics_views.py`,
`models.py`, `execution.py`, `apply_execution.py`, `apply_preview.py`, `plugin.py`,
`gamedev/tracing.py`, `otlp.py`, `collector.py`, `infrastructure.py`,
`platform_metrics.py`, `hq_sdk/ui.py`, and `docs/Design.md`.

## Accuracy work before the home screen

1. A successful apply/rollback may retain Run.passed=False while its status is
   accepted. `worker.process_next` currently exports that as
   `hq.verification.passed=False`, making the trace root look like failed testing.
   Distinguish successful file operations from check evidence. Do not silently
   rewrite exported history; show authoritative operation status and the known
   historical discrepancy until a versioned correction exists.
2. Empty-diff or explicitly incomplete agent outputs can have passing baseline
   checks. The board must not call that task success or changes ready solely from
   a passing check flag. The rejected empty result in run 14 is a concrete example.
3. Superseded failures are historical evidence. A failed apply followed by a
   successful apply of the same approved source must not remain a current alarm.
4. Editor inspection, compilation, source consistency, synthetic harness tests and
   gameplay are distinct evidence classes. No single blended success percentage
   may imply gameplay acceptance.
5. Totals must state the denominator, project, time window, observation time and
   coverage limit. Unknown cost or resource data stays unknown. Token coverage is
   not model quality. Cached and total tokens must not be summed as separate usage.

## First screen contract

A project-scoped read-only overview, using existing capability and project lookup
boundaries. Metrics access is currently workstation-wide; a project filter is not
an access-control or tenant boundary.

Suggested route: `/game-dev/mission/?project=<slug>`. Empty selection presents an
explicit project choice; unknown slugs return a useful invalid-selection result.
No default data, hard-coded counts or invented progress percentages.

Top row:

- Agent activity: queued/running coding jobs, with engine and delegation ownership
  shown when reported. Editor/file operations appear separately.
- Changes to review: coding runs in review with actual captured changes; distinguish
  failed checks or incomplete output. Editor/build evidence has its own queue.
- Verification: counts by check class and outcome in a declared recent cohort.
- Workstation: current/stale/unavailable readings, with actionable exceptions and
  a link to details. A listening port is connectivity, not application health.

Needs you, in order:

- Latest unresolved failures and canceled work needing a decision. Collapse
  recovered attempts using explicit applies/retried_from/delegated_by links;
  where explicit lineage is missing, label history rather than guess recovery.
- Coding diffs awaiting review, including exact run, changed-file count, check
  summary, age, destination preview and review link.
- Editor/build results needing acknowledgement, labeled as evidence rather than
  source changes ready for application.
- Stale worker/collector, unavailable required services, failed trace delivery or
  backup warnings, with missing evidence explicitly called out.

What to do next:

A deterministic recommendation with supporting run/evidence links and a named
reason. Start with review, inspect, retry proposal, or resolve deployment conflict.
No retry, apply, service restart or editor action occurs from GET, polling, a
recommendation, or a model-generated explanation. Existing capabilities own every
mutation. A recommendation does not count as a new approved plan.

Activity and evidence:

A bounded recent project timeline linking task, run, decision, apply and build
records. Keep raw logs and source in the existing protected run detail. Only show
Phoenix links to readers with trace capability. Hide metrics for readers without
metrics capability; a `gamedev.read` permission is not a metrics grant.

Use the host's server-rendered page, shared Kpi/Insight/record-list partials and
fragment polling. Reuse the partial response mechanism, keyboard behavior, focus,
empty-state conventions and phone layouts. No new request-time network/process
calls, model invocation, parsing of raw agent logs, or writes.

## Acceptance gates for the implementation

- Read-only projection tests cover empty data, project isolation, pending work,
  stale/missing readings, recovered failure, empty diff, failed checks, distinct
  compile/gameplay outcomes and unavailable costs.
- Authorization tests cover denied read/metrics/trace capabilities and verify that
  view/polling never invokes a write capability or external service.
- Query budgets stay stable as projects/tasks/runs grow; cap recent detail lists,
  select only needed fields, prefetch related evidence and avoid per-row queries.
- Fixture and browser tests cover real action links, full/fragment responses,
  phone/desktop layout, long task names, keyboard use and no-script output.
- Existing workspace, companion, SDK boundaries, structural and full browser/CI
  gates pass. Changes remain in the extension; host remains extension-agnostic.
- Phone preview walkthrough confirms the user can identify the top unresolved
  decision, open its evidence, and take the existing authorized action.

## Curated evaluation milestone

Phoenix supports versioned datasets, experiments and annotations. Verify the
installed server/client API and privileges with a small local compatibility probe
before implementing writes; availability in current documentation does not prove
compatibility with the pinned runtime.

Build 10-20 independent, reviewed cases across multiple task families. Candidate
source records include runs 10-12 (offline check and apply recovery), 14-16 (empty
output and coverage reporter), 17/19/20 (deployment packaging and marker correction)
and 21 (real build). These are candidates, not an approved dataset, and multiple
attempts in one family are not independent benchmark examples. They do not yet
provide broad Verse gameplay or Codex-versus-OpenCode quality coverage.

Each case records sanitized input, permitted source fixture/context, expected
outcome/rubric, verification commands and exact versions, baseline revision,
provenance IDs, reviewer approval, and a reproducibility fingerprint. Preview the
complete exported example; redact local identities/paths/credentials and excluded
source. Never upload raw run logs or prompts implicitly. Dataset creation is a
separate audited capability, not a side effect of browsing traces.

Split holdouts by task family to prevent retries/near-duplicates leaking between
cohorts. Start with deterministic checks (build/source verdict, scope compliance,
empty-diff detection, honest evidence labels). Add human quality annotations;
LLM judging, if later used, is advisory and independently calibrated. Unknown
resource/cost data is not zero. Compare like-for-like task and verification
versions, with equal execution boundaries and explicit quota/budget limits.
Experiments run in isolated workers with no apply/editor privileges by default.
A winning experiment proposes a workflow change; it does not change production
policy or agent selection automatically.

Primary references:
- https://arize.com/docs/phoenix/learn/datasets-and-experiments/datasets-concepts
- https://arize.com/docs/phoenix/tracing/concepts-tracing/concepts-annotations

## Milestones and exit conditions

1. Inventory and project overview design: this audit, real record/sample inventory,
   reuse/adapt/leave-alone map, and explicit release blockers.
2. Mission Control: accuracy prerequisites plus the tested decision-oriented screen.
3. Evaluation foundation: approved versioned cases and reproducible deterministic
   baseline, with no unapproved data export.
4. Failure analysis: permitted evidence packet, cited explanation and suggested next
   action; approval required for execution through existing capabilities.
5. Model/delegation comparison: versioned experiments, supported conclusions and
   human-approved policy changes. No rankings inferred from raw usage totals.
