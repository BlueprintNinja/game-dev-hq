# Mission Control implementation (milestone 2)

`/game-dev/mission/?project=<slug>` reads the existing extension database. The
board and its run actions remain in place; the board and task dashboard card
link to Mission Control. Projects come from `hq_sdk.resources`; an empty
selection prompts a choice and a missing project returns 404. Selection is a
filter, not tenant authorization. Resource permissions still apply.

The page requires both `gamedev.read` and the host `read` grant. Existing
capability decorators deny a missing grant with 403 before project resources
are queried, on empty/selected pages and fragment requests. Infrastructure freshness and the selected
project's worker heartbeat additionally require `gamedev.metrics.read`.
Execution trace links require `gamedev.traces.read`. No Phoenix address,
credentials, browser storage, prompt, source, output, or raw logs are exported
by this page. Cost and project resource totals remain unavailable.

The page uses SDK Kpi, Insight and ListRow objects and the host's shared KPI,
insight and record list partials. Its `mission` partial answers `X-Fragment`
requests and `fragment=1`; the same definition renders the full page. Polling
uses the public `data-fragment` URL hook rather than `data-fragment-name`,
which this checkout's SDK validator does not admit for extensions. A selected project polls
every 15 seconds using the host data-fragment mechanism. GET never executes a
capability, writes a record, starts a process, or makes a network request.
Responses are private and not cached. Actions are links to existing run pages,
where the review, retry, apply and restore forms retain their existing rules.

Current work includes queued/running runs of every age, plus unfinished tasks
without an attempt. Current attention uses the latest work attempt per task
(coding, editor inspection or compilation), not the worst historical outcome.
Rejected and superseded coding runs do not raise current alarms. File operations
use their latest attempt per kind and approved source; they remain relevant
only while that source is the current task work. A later accepted apply retires
the earlier failed apply for that source. A successful un-restored apply removes
the source from changes awaiting apply. Run status is authoritative; trace
failure flags are never used to establish Mission Control attention.

Coding changes require passing checks AND a non-whitespace saved diff. Passing
checks with no changes appear as results to acknowledge, alongside editor and
compilation results. Check outcomes use the stored compact check summary, with
source, compilation, work process and gameplay kinds kept separate. Unknown
kinds are labeled other checks; no game quality score or completion inference
is made. Reported engines and delegation run IDs come from actual run records.
The first three suggestions are deterministic: latest problems first, then
coding changes, then results, newest run first within each group. Each links
to the evidence-bearing run page.

Recent history is the last seven days by run update time, newest first, capped
at 20 with a truncation notice. Working, pending and current attention queries
are capped at 100 each, with a notice and board link when additional records
exist. Counters explicitly count shown records. Latest-attempt/recovery checks
run in SQL before the display limit, across all ages. Joined task fields and
SQL diff-presence/compact-check annotations avoid N+1 queries and avoid fetching
prompt, output, raw diff or verification logs. No file count is claimed.

Freshness uses a 90-second timestamp threshold. Missing and out-of-date readings
are stated explicitly. A current timestamp establishes freshness alone, never
healthy operation. Workstation metrics are global, including on a project page;
this milestone does not claim project-attributed infrastructure consumption.

## Worker trace accuracy

New apply and rollback roots emit `hq.operation.succeeded` from accepted run
status and omit `hq.verification.passed`. Coding/editor roots keep the check
flag. A false operation outcome now classifies a stage as failed, as do false
verification and actual exceptions/process failures. Regression cases cover
accepted apply and rollback with the default false verification flag, a failed
file operation and coding false verification.

**Historical roots are not rewritten.** Previously exported successful file
operations may still carry a false verification flag and appear failed in old
traces. Consult authoritative run status; Mission Control does so already.

## Verification

Workspace tests cover selection, project isolation, old active work, distinct
coding/editor review, empty diffs, apply recovery, superseded work, genuine
latest failures, missing/stale readings, permission gating, full/fragment
identity, HTML escaping, existing run links, stable bounded query count,
truncation, and GET mutation/network/process guards. Worker root tests exercise
the real stage recorder with only execution and claim mocked. Tests use the
check command's temporary composition, never operator data.

Verification commands and final results are reported with the handoff. TEMP,
TMP and TMPDIR are set to ignored `var/tmp`. Browser workflow QA is an
orchestrator followup and is not claimed here. Full host gates, composed
preflight and browser QA remain necessary before calling this ready.

### Workspace verification evidence (run23)

The recovery patch was verified with SHA256
`639f37cbcd4ef20cadf48ca80840f5423934204b8a4b4ae51371c2e818cc20b2`
and applied with `git apply` to the initially clean isolated worktree. The
preserved implementation was used directly. No credential access or remote
fetch was performed; local HEAD is `87fab91` (cached origin/main is 17 commits
behind). Remote freshness and CI were not independently verified.

Using the supplied `source-apply/.venv/Scripts/python.exe`, with TEMP/TMP/TMPDIR
under this checkout's ignored `var/tmp`:

- `python -m unittest discover -s gamedev/tests -v`: 80 tests in 4.493s,
  OK (7 existing environment skips). These are separate from the workspace
  gate, which has no skips.
- Unmodified `python -m tools.windows_hq.check`: **77 tests in 7.465s, OK,
  no skips**, exit 0; system check found no issues. No runtime test suppression,
  test-runner monkeypatch or discovery changes were used.
- The existing ASGI unit test retains all four missing/duplicate/nonloopback/
  valid identity assertions and runs in inherited cases. Its explicit inline
  coroutine driver advances once, returns StopIteration.value, rejects a
  suspension and closes in finally. Two added helper tests verify returned
  values and suspension rejection with cleanup. TailnetBoundary is unchanged.
- Mission denial regression covers missing host read and missing gamedev.read,
  with empty/selected pages and X-Fragment requests. Unauthorized resource
  access is guarded by a failing mock. Full HTML, header fragments and polling
  URL fragments produce the same region.
- `python -m hq_sdk.validation gamedev/hq_plugin`: passed SDK import and
  template boundaries, exit 0.
- Projection query-budget regression passes: four queries with one task and
  with 31 tasks; no metric/heartbeat queries without the metrics grant.
- Reviewed all eight changed Python files for imports and structural limits:
  no unused imported names found by AST inspection; each is under 1,000 lines;
  all function cognitive scores are at most 20 using the repository scorer
  (maximum 17 in worker.py). This is targeted source evidence, not a full
  structural gate or a substitute for Ruff.
- `mise run fast` and `mise run check`: unavailable (`mise` not found).
- Ruff: unavailable (`No module named ruff`; no executable found).
- `git diff --check`: passed.

The canceled run22 diagnostic skip is not used as verification evidence.
Browser QA on the final candidate remains an orchestrator followup and was not
run here. Ruff, full host/composition gates, browser QA and preflight remain
outstanding. No editor interaction, commit, push, publish, deployment or remote
CI execution was performed. No ASGI production code, check command, apply
runner, destination tests or other repository was changed.
