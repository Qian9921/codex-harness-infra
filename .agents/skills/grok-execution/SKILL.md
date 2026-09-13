---
name: grok-execution
description: Run the preferred Grok 4.6 Build execution route at low effort, with the locally configured native executor allowed only after a verified Grok quota-exhaustion receipt.
---

# Grok-first execution

Use this skill when local executor routing selected the Grok adapter (`paid_preferred`, `paid_strict`, or a config without `[routing]`). `native_only` does not use this skill. The persistent parent remains responsible for scope, decisions, verification, and the final answer. Grok is an external execution backend, not a native Codex subagent.

Hand Grok one bounded task that includes implementation, tests, and fix work. Require a concise return covering changed files, behavior, evidence, and unresolved items. Primary then performs targeted independent verification of those claims; do not duplicate the full implementation. Long Grok `run`/`resume` invocations default to no wall-clock timeout. Dedicated process-group cleanup is mandatory: spawn issues an internal cleanup-ownership token immediately (`start_new_session=True`; candidate PGID is proc.pid); public registry/signal cleanup still requires getpgid==pid; every normal return and BaseException path terminates remaining group members. Before run/resume/batch the main process blocks SIGTERM/SIGHUP/SIGINT. Internals: [grok-process-lifecycle.md](references/grok-process-lifecycle.md). Recursive content search MUST use the installed Harness helper `${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py` (not an OS sandbox), 15s timeout; on timeout or incomplete, narrow and retry, never treat as no-match. Known individual-file reads may stay direct. All Codex Grok `run`/`resume` invocations MUST be supervised by a separately spawned generic Luna-low native subagent, distinct from `v23_executor`. That supervisor watches lifecycle and receipt only and never edits. The parent waits for the supervisor completion event and does not directly narrate or poll Grok. `v23_executor` remains the quota-exhaustion-only fallback and is not the supervisor.

## Required route

1. Freeze one task, one working directory, and exclusive writable paths. `run` and `resume` require a nonempty `--task-id` and at least one `--owned-path`.
2. Invoke the installed bridge through Python, never as a direct executable:

   `python "${CODEX_HOME:-$HOME/.codex}/bin/grok-execution.py" run --cwd <dir> --task-id <id> --owned-path <path> [--owned-path <path> ...] --prompt-file <file>`

   The installed file is mode `0600` and has no shebang. The bridge hard-locks reasoning effort to `low`; do not substitute another effort. Do not put the task prompt on argv; the bridge writes a bound prompt to a mode-`0600` temporary file and passes it to Grok as `--prompt-file`.
3. Accept execution only from a `codex-external-execution.v1` receipt with `status = SUCCESS`, requested model `grok-4.6`, actual model `grok-4.6-build`, the exact absolute working directory, exact task ID, exact owned paths, and a nonempty conversation ID.
4. Inspect the changed paths and rerun only the decision-changing checks independently. Do not re-implement the same change.
5. Continue the same Grok conversation with `resume`, the exact conversation ID, and its bound receipt. Resume requires the same nonempty task ID and owned paths.

## Luna fallback boundary

The locally configured native V23 executor is a fallback, not a peer route. Use it only when the bridge returns a structured receipt with `status = QUOTA_EXHAUSTED` and `fallback_reason = grok_quota_exhausted`. That receipt must bind `task_id`, absolute `working_directory`, exact `owned_paths`, `requested_model`, `actual_model` if known, and `fallback_reason`. A timeout, authentication failure, missing binary, malformed receipt, transient network failure, generic HTTP 429, model mismatch, or any other generic bridge error does not authorize fallback; repair Grok or report `GROK_EXECUTION_BLOCKED`.

When fallback is authorized, pass the failed task, working directory, owned paths, and quota receipt to `v23_executor`. Luna may run only when the receipt `task_id`, absolute working directory, and owned paths match the current task exactly. Report both the requested Grok identity and the actual fallback identity. Never claim Grok performed work completed by the fallback.

For two independent writers, use `batch --max-parallel 2` with nonoverlapping owned paths. Batch quota receipts use the same bindings. Keep one Git owner and never let the parent and Grok write the same path.
