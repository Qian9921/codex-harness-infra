---
name: grok-execution
description: Run the preferred Grok 4.6 Build execution route at low effort, with the locally configured native executor allowed only after a verified Grok quota-exhaustion receipt.
---

# Grok-first execution

Use this skill for implementation, tests, data runs, recovery, and authorized Git work. The persistent parent remains responsible for scope, decisions, verification, and the final answer. Grok is an external execution backend, not a native Codex subagent. Long Grok `run`/`resume` invocations default to no wall-clock timeout. Once a dedicated Grok process group is spawned, cleanup ownership is taken immediately: spawn issues an internal cleanup-ownership token immediately (candidate PGID is proc.pid because start_new_session=True); public registry/signal cleanup still requires getpgid==pid; local validation/recording failure kills only via that spawn-issued candidate token then bounded reap; dedicated-PGID validation failure, registry-full errors, boundary-hook exceptions, and later setup failures kill/reap the spawned group and unregister only if registered. After that group is validated, it is registered in the process-global PGID table immediately; validation failure fails closed. Before run/resume/batch the main process blocks SIGTERM/SIGHUP/SIGINT and starts a sigwait coordinator. The registry lock spans termination check, spawn, PGID validation, and publication so concurrent batch registrations cannot overwrite slots. The coordinator uses the same lock, marks terminating, snapshots validated dedicated PGIDs, SIGKILLs those groups with bounded nonblocking killpg, then restores default disposition and re-raises the signal. A dedicated post-exec Python launcher, started with Popen/start_new_session and no preexec_fn, unblocks and resets termination-signal dispositions, restores SIGPIPE and SIGXFSZ to SIG_DFL when getattr finds them on the platform (matching Python subprocess restore_signals), then execvpe's the real Grok command so the inherited blocked-signal mask is cleared after exec. The launcher interval stays inside the validated dedicated PGID that cleanup SIGKILLs. Then every normal return and BaseException path terminates remaining group members with a bounded drain/reap and preserves the direct child's stdout, stderr, and returncode on normal completion. All Codex Grok `run`/`resume` invocations MUST be supervised by a separately spawned generic Luna-low native subagent, distinct from `v23_executor`. That supervisor watches lifecycle and receipt only and never edits. The parent waits for the supervisor completion event and does not directly narrate or poll Grok. `v23_executor` remains the quota-exhaustion-only fallback and is not the supervisor.

## Required route

1. Freeze one task, one working directory, and exclusive writable paths. `run` and `resume` require a nonempty `--task-id` and at least one `--owned-path`.
2. Invoke the installed bridge through Python, never as a direct executable:

   `python "${CODEX_HOME:-$HOME/.codex}/bin/grok-execution.py" run --cwd <dir> --task-id <id> --owned-path <path> [--owned-path <path> ...] --prompt-file <file>`

   The installed file is mode `0600` and has no shebang. The bridge hard-locks reasoning effort to `low`; do not substitute another effort. Do not put the task prompt on argv; the bridge writes a bound prompt to a mode-`0600` temporary file and passes it to Grok as `--prompt-file`.
3. Accept execution only from a `codex-external-execution.v1` receipt with `status = SUCCESS`, requested model `grok-4.6`, actual model `grok-4.6-build`, the exact absolute working directory, exact task ID, exact owned paths, and a nonempty conversation ID.
4. Inspect the changed paths and rerun the decision-changing checks independently.
5. Continue the same Grok conversation with `resume`, the exact conversation ID, and its bound receipt. Resume requires the same nonempty task ID and owned paths.

## Luna fallback boundary

The locally configured native V23 executor is a fallback, not a peer route. Use it only when the bridge returns a structured receipt with `status = QUOTA_EXHAUSTED` and `fallback_reason = grok_quota_exhausted`. That receipt must bind `task_id`, absolute `working_directory`, exact `owned_paths`, `requested_model`, `actual_model` if known, and `fallback_reason`. A timeout, authentication failure, missing binary, malformed receipt, transient network failure, generic HTTP 429, model mismatch, or any other generic bridge error does not authorize fallback; repair Grok or report `GROK_EXECUTION_BLOCKED`.

When fallback is authorized, pass the failed task, working directory, owned paths, and quota receipt to `v23_executor`. Luna may run only when the receipt `task_id`, absolute working directory, and owned paths match the current task exactly. Report both the requested Grok identity and the actual fallback identity. Never claim Grok performed work completed by the fallback.

For two independent writers, use `batch --max-parallel 2` with nonoverlapping owned paths. Batch quota receipts use the same bindings. Keep one Git owner and never let the parent and Grok write the same path.
