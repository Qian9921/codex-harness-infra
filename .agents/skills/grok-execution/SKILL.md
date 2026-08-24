---
name: grok-execution
description: Run the preferred Grok 4.6 Build execution route at low effort, with the locally configured native executor allowed only after a verified Grok quota-exhaustion receipt.
---

# Grok-first execution

Use this skill for implementation, tests, data runs, recovery, and authorized Git work. The persistent parent remains responsible for scope, decisions, verification, and the final answer. Grok is an external execution backend, not a native Codex subagent.

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
