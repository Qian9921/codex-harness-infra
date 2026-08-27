# Delivery workflow

This file defines how the portable roles cooperate with Codex and GitHub. It is a small operating agreement, not a second agent runtime.

## Work kind and capability

Classify the task on two independent axes:

| Work kind | Capability | Meaning |
| --- | --- | --- |
| `discuss` | `read_only` | Explain, investigate, or review without repository or GitHub writes. |
| `repo_change` | `local_write` | Explicitly local-only change and relevant local checks. |
| `repo_change` | `github_write` | Default delivery of a normal repository change through a Pull Request. |
| any | `consequential_external` | Affect production, accounts, data, releases, or another irreversible external system. |

`repo_change + github_write` is the default under V23 standing authorization; use `local_write` only when the user explicitly requests local-only work. Neither authorizes unrelated external actions.

## Participatory questioning

This contract applies to both `discuss` and `repo_change`. Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly. For every other task, perform a concise intent audit: desired outcome, facts, assumptions/preferences, counterevidence, and adjacent effects; bounded read-only investigation is allowed. Decide whether to ask or act. Ask 1–3 questions (`request_user_input` when available) only when the answer cannot be safely discovered and materially changes outcome, scope, risk, or cost; otherwise proceed without a separate explicit start. Disagree explicitly and propose a better route when the requested method does not serve the outcome. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery.

## DISCUSS

For `discuss`, stay read-only. Only the simple/direct exceptions in Participatory questioning may answer directly; every other `discuss` task follows that shared questioning contract. Do not create a branch, commit, Pull Request, review, comment, or merge. If the discussion identifies a later change, stop at the boundary and wait for that change to be requested or otherwise authorized.

## REPO_CHANGE

For every repository change unless the user explicitly requests local-only work:

```text
understand → implement → verify → commit → push → Pull Request
          → fresh independent review → fix if needed → approval → merge
```

The primary role owns the request, scope, decisions, verification, and final result. The preferred external Grok execution route performs bounded implementation and verification at low effort; `run`/`resume` wait without a wall-clock timeout while the dedicated Grok process group is genuinely alive and not a zombie. Once that dedicated group is spawned, cleanup ownership is taken immediately: spawn issues an internal cleanup-ownership token immediately (candidate PGID is proc.pid because start_new_session=True); public registry/signal cleanup still requires getpgid==pid; local validation/recording failure kills only via that spawn-issued candidate token then bounded reap; dedicated-PGID validation failure, registry-full errors, boundary-hook exceptions, and later setup failures kill/reap the spawned group and unregister only if registered. After that group is validated, it is registered in the process-global PGID table immediately; validation failure fails closed. Before run/resume/batch the main process blocks SIGTERM/SIGHUP/SIGINT and starts a sigwait coordinator. The registry lock spans termination check, spawn, PGID validation, and publication so concurrent batch registrations cannot overwrite slots. The coordinator uses the same lock, marks terminating, snapshots validated dedicated PGIDs, SIGKILLs those groups with bounded nonblocking killpg, then restores default disposition and re-raises the signal. A dedicated post-exec Python launcher, started with Popen/start_new_session and no preexec_fn, unblocks and resets termination-signal dispositions, restores SIGPIPE and SIGXFSZ to SIG_DFL when getattr finds them on the platform (matching Python subprocess restore_signals), then execvpe's the real Grok command so the inherited blocked-signal mask is cleared after exec. The launcher interval stays inside the validated dedicated PGID that cleanup SIGKILLs. Then every normal return and BaseException path terminates remaining group members with a bounded drain/reap, preserving the direct child's stdout, stderr, and returncode on normal completion. All Codex Grok `run`/`resume` invocations MUST be supervised by a separately spawned generic Luna-low native subagent, distinct from `v23_executor`. That supervisor watches lifecycle and receipt only and never edits. The parent waits for the supervisor completion event and does not directly narrate or poll Grok. `v23_executor` remains the quota-exhaustion-only fallback and is not the supervisor. The reviewer receives the request, current diff, relevant evidence, and current head SHA in fresh read-only context.

The author and reviewer are different GitHub identities. The author must be the GitHub actor that pushes the branch; on a shared machine, explicitly select the author's isolated Git credential helper instead of inheriting the default credential. The reviewer model's verdict, the GitHub approval, and GitHub's branch rules are separate facts. A review is valid only for the head SHA it inspected. Any later commit requires a new review.

Use the V23 delivery adapter for branch push, PR creation, GitHub review, and
merge checks. Its push operation requires an explicit worktree and refspec and
reads one raw local credential-free HTTPS `github.com` URL, then starts an
otherwise config-isolated Git push with the configured author's GH_CONFIG_DIR
credential helper. That excludes ambient Git credential/header configuration,
environment injection variables, and URL rewrites.

Merge only after the current head has the required checks, no blocking unresolved feedback, and a valid approval from the configured reviewer identity. Let GitHub enforce repository rules; do not imitate them with a local process.

Commits should be small, complete, and understandable in one sitting. Keep related tests with the behavior they protect. Prefer delete, merge, reuse, or fix; retire superseded code, docs, and tools in the same change unless active compatibility requires them. Do not create noisy commits merely to increase the count.

## CONSEQUENTIAL_EXTERNAL

Production releases, deletion of user data, credential changes, account operations, and other irreversible external actions require separate explicit confirmation. Ordinary Pull Request delivery does not silently expand into these actions.

## Delegation

Use subagents for independent read-heavy investigation, testing, or review when that reduces context noise. Use one writer per worktree. Do not parallelize tightly coupled edits merely for appearance. A delegated task is complete only when it returns a concrete result, evidence, diff, or blocker.

## Interruption and recovery

The Pull Request and its current branch head are the durable delivery record. After an interruption, query GitHub and the checkout, then continue only with the next operation still needed. Do not create a second task database or duplicate workflow history.

## Communication

Keep progress updates short and factual. Internal process polls are not narrated. User-facing updates are only start, a meaningful state change, and completion or failure, subject to platform constraints. Empty structured `request_user_input` answers are unanswered: the task stays paused and the same questions are re-presented on resume, with no mutation and no inferred defaults. The final report contains the result, the checks actually run, and any unresolved item. Do not include credentials, private local paths, or hidden model reasoning in commits, Pull Requests, or comments.
