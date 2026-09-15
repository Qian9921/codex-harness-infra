---
name: grok-execution
description: Use only when local executor routing selected the Grok or Pi adapter. Do not load for native_only or a Codex-selected route.
---

# External Pi execution

Use this skill when local executor routing selected the Grok or Pi adapter (`paid_preferred`, `paid_strict`, or a config without `[routing]`). `native_only` does not use this skill. The persistent parent remains responsible for scope, decisions, verification, and the final answer. Pi is the generic external execution backend; do not invoke GrokCLI.

Hand Pi one bounded task matching the requested work. The bound prompt is always conditional on TASK intent: read-only work investigates, reports, and may run nonmutating checks; implementation, tests, and fixes apply only with authorized write work. Do not keyword-classify the TASK. Before commitment or delegation, inspect the current call path and owner helpers, then existing dependencies and neighbor APIs; use external sources only if needed. Identify claim-appropriate evidence before design, causal, or performance commitment; insufficient evidence stays a hypothesis. Delegation sends goal, constraints, inspected candidate evidence, the actual gap, and unknowns. Name or similarity is not fitness; copying is not reuse. New implementation is allowed only with an evidenced gap. Straightforward tasks need only a brief check. When writes occurred, require a concise return covering changed files, behavior, evidence, and unresolved items. Primary then performs targeted independent verification of those claims; do not duplicate the full implementation. Long Pi `run`/`resume` invocations default to no wall-clock timeout. Dedicated process-group cleanup is mandatory: spawn issues an internal cleanup-ownership token immediately (`start_new_session=True`; candidate PGID is proc.pid); public registry/signal cleanup still requires getpgid==pid; every normal return and BaseException path terminates remaining group members. Before run/resume/batch the main process blocks SIGTERM/SIGHUP/SIGINT. Internals: [grok-process-lifecycle.md](references/grok-process-lifecycle.md). Recursive content search MUST use the installed Harness helper `${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py` (not an OS sandbox), 15s timeout; on timeout or incomplete, narrow and retry, never treat as no-match. Known individual-file reads may stay direct. All Codex Pi `run`/`resume` invocations MUST be supervised by a separately spawned generic Luna-low native subagent, distinct from `v23_executor`. That supervisor watches lifecycle and receipt only and never edits. The parent waits for the supervisor completion event and does not directly narrate or poll the adapter. `v23_executor` remains the quota-exhaustion-only fallback and is not the supervisor.

## Required route

1. Freeze one task, one working directory, and exclusive writable paths. `run` and `resume` require a nonempty `--task-id` and at least one `--owned-path`.
2. Invoke the installed bridge through Python, never as a direct executable:

   `python "${CODEX_HOME:-$HOME/.codex}/bin/grok-execution.py" run --cwd <dir> --task-id <id> --owned-path <path> [--owned-path <path> ...] --prompt-file <file> --provider <provider> --model <model> --thinking <level> --session-dir <dir>`

   The installed file is mode `0600` and has no shebang. Pass the selected `--provider`, `--model`, `--thinking`, and `--session-dir`. Thinking must be valid for that identity: `xai/grok-4.6` accepts `xhigh` and rejects `max`; `qwen-token-plan-cn/deepseek-v4.1-flash` may use `max` only where valid. Do not put the task prompt on argv; the adapter writes a bound prompt to a mode-`0600` temporary file and passes it to Pi as `@file`. Never fall back to GrokCLI.
3. Accept execution only from a `codex-external-execution.v1` receipt with `status = SUCCESS`, the configured provider/model, actual provider/model/stopReason from JSONL `message_end`, the exact absolute working directory, exact task ID, exact owned paths, session directory, and a nonempty conversation ID. Zero assistant calls, `error`, `aborted`, or `length` are not success. Token accounting comes from `message_end` usage.
4. Inspect the changed paths and rerun only the decision-changing checks independently. Do not re-implement the same change.
5. Continue the same Pi session with `resume`, the exact conversation ID, the same `--session-dir`, and its bound receipt. Resume requires the same nonempty task ID, owned paths, provider, and model.

## Luna fallback boundary

Under legacy and `paid_strict` routing, the locally configured native V23 executor is a fallback, not a peer route. Use it only when the adapter returns a structured receipt with `status = QUOTA_EXHAUSTED` and `fallback_reason` `grok_quota_exhausted` or `pi_quota_exhausted`. `paid_preferred` may select a capability-fit native executor initially without a receipt; that is not a post-adapter fallback. After an attempted Pi run, fallback still requires the bound quota receipt. That receipt must bind `task_id`, absolute `working_directory`, exact `owned_paths`, `requested_model`, `actual_model` if known, and `fallback_reason`. A timeout, authentication failure, missing binary, malformed receipt, transient network failure, generic HTTP 429, model mismatch, or any other generic bridge error does not authorize fallback; repair Pi or report `GROK_EXECUTION_BLOCKED`.

When fallback is authorized, pass the failed task, working directory, owned paths, and quota receipt to `v23_executor`. Luna may run only when the receipt `task_id`, absolute working directory, and owned paths match the current task exactly. Report both the requested Pi identity and the actual fallback identity. Never claim Pi performed work completed by the fallback.

For two independent writers, use `batch --max-parallel 2` with nonoverlapping owned paths. Batch quota receipts use the same bindings. Keep one Git owner and never let the parent and Pi write the same path.
