# Delivery workflow

This file defines how the portable roles cooperate with Codex and GitHub. It is a small operating agreement, not a second agent runtime. Process-group internals live in `.agents/skills/grok-execution/references/grok-process-lifecycle.md`.

## Work kind and capability

Classify the task on two independent axes:

| Work kind | Capability | Meaning |
| --- | --- | --- |
| `discuss` | `read_only` | Explain, investigate, or review without repository or GitHub writes. |
| `repo_change` | `local_write` | Local change and relevant local checks. |
| `repo_change` | `github_write` | Pull Request delivery for an explicitly authorized repository. |
| any | `consequential_external` | Affect production, accounts, data, releases, or another irreversible external system. |

Capability follows the installed `[delivery]` table, not an implied standing grant. Omitted `[delivery]` is `local_only`. `pull_request` and `merge_if_ready` require nonempty `repositories`. Installing the Harness or configuring GitHub credentials is not publication permission. Current user instructions override those defaults. Neither authorizes unrelated external actions.

## Participatory questioning

This contract applies to both `discuss` and `repo_change`. Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly. Those exceptions never authorize implementation or publication by themselves. For every other task, perform a concise intent audit: desired outcome, facts, assumptions/preferences, counterevidence, and adjacent effects; bounded read-only investigation is allowed. Decide whether to ask or act. Ask 1–3 questions (`request_user_input` when available) only when the answer cannot be safely discovered and materially changes outcome, scope, risk, or cost; otherwise proceed without a separate explicit start. Disagree explicitly and propose a better route when the requested method does not serve the outcome. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery. Preserve intent across turns; separate facts from preferences; show evidence when a conclusion changes; state material assumptions only when they would change the decision.

## DISCUSS

For `discuss`, stay read-only. Only the simple/direct exceptions in Participatory questioning may answer directly; every other `discuss` task follows that shared questioning contract. Do not create a branch, commit, Pull Request, review, comment, or merge. If the discussion identifies a later change, stop at the boundary and wait for that change to be requested or otherwise authorized.

## REPO_CHANGE

For `local_only` (the portable default):

```text
understand → implement → verify → commit (when Git work is in scope)
```

Do not push, open a Pull Request, or merge.

For `pull_request` on an authorized repository:

```text
understand → implement → verify → commit → push → Pull Request
          → fresh independent review → fix if needed → approval
```

For `merge_if_ready` on an authorized repository, continue from a valid current-head approval through merge when required checks pass.

The primary role owns the request, scope, decisions, verification, and final result. The locally selected execution route performs one bounded implementation, test, and fix pass and returns concise files, behavior, evidence, and unresolved items. When that route is Grok, effort is low and Luna-low supervises lifecycle and receipt only. Primary then runs targeted independent verification of those claims instead of duplicating the full implementation. `run`/`resume` wait without a wall-clock timeout while the dedicated Grok process group is genuinely alive and not a zombie. Dedicated-PGID cleanup, spawn-issued cleanup token (`start_new_session=True`), registry lock spanning SIGTERM/SIGHUP/SIGINT coordination, SIGKILL of the process group, and the post-exec launcher are required; every normal return and BaseException path drains remaining members. See `.agents/skills/grok-execution/references/grok-process-lifecycle.md`. All Codex Grok `run`/`resume` invocations MUST be supervised by a separately spawned generic Luna-low native subagent, distinct from `v23_executor`. That supervisor watches lifecycle and receipt only and never edits. The parent waits for the supervisor completion event and does not directly narrate or poll Grok. When Grok is selected, `v23_executor` remains the quota-exhaustion-only fallback and is not the supervisor; `native_only` uses that same agent as the complete Codex executor. The reviewer receives the request, current diff, relevant evidence, and current head SHA in fresh read-only context.

Executor selection is local and capability-based. Run
`python scripts/executor_routing.py select --local-config <file> --capability implementation`
(or the installed `bin/executor-routing.py`). `native_only` is a complete Codex
path. `paid_preferred` and `paid_strict` prefer a configured paid/included
executor; configs without `[routing]` keep Grok-preferred execution with
quota-only native fallback. Do not treat unknown quota as zero, do not migrate
just because a new native slug exists, and do not label network/auth/timeout/
bridge errors as quota. Primary remains decision-only. Model names are frozen
at install, update, or actual use of the local mapping; do not auto-upgrade
shared policy to a new native version string.

The author and reviewer are different GitHub identities. The author must be the GitHub actor that pushes the branch; on a shared machine, explicitly select the author's isolated Git credential helper instead of inheriting the default credential. The reviewer model's verdict, the GitHub approval, and GitHub's branch rules are separate facts. A review is valid only for the head SHA it inspected. Any later commit requires a new review.

Use the V23 delivery adapter for branch push, PR creation, GitHub review, and
merge checks. Publication commands require `--local-config`. Its push operation requires an explicit worktree and refspec and
reads one raw local credential-free HTTPS `github.com` URL, then starts an
otherwise config-isolated Git push with the configured author's GH_CONFIG_DIR
credential helper.

Merge only after `delivery.mode=merge_if_ready`, the repository is listed, the current head has the required checks, no blocking unresolved feedback, and a valid approval from the configured reviewer identity.

Commits should be small, complete, and understandable in one sitting. Keep related tests with the behavior they protect. Prefer delete, merge, reuse, or fix; retire superseded code, docs, and tools in the same change unless active compatibility requires them.

## CONSEQUENTIAL_EXTERNAL

Production releases, deletion of user data, credential changes, account operations, and other irreversible external actions require separate explicit confirmation. Ordinary Pull Request delivery does not silently expand into these actions.

## Delegation

Use subagents for independent read-heavy investigation, testing, or review when that reduces context noise. Use one writer per worktree. Do not parallelize tightly coupled edits merely for appearance. A delegated task is complete only when it returns a concrete result, evidence, diff, or blocker.

## Interruption and recovery

The local commits and, when authorized, the Pull Request and its current branch head are the durable delivery record. After an interruption, query GitHub and the checkout, then continue only with the next operation still needed. Diagnose stalled progress (same finding, no observable change) versus an actual external block (CI, permissions, missing approval, network). Do not stop because a review-round counter expired.

## Communication

Keep progress updates short and factual. Internal process polls are not narrated. User-facing updates are only start, a meaningful state change, and completion or failure, subject to platform constraints. Empty structured `request_user_input` answers are unanswered: the task stays paused and the same questions are re-presented on resume, with no mutation and no inferred defaults. The final report contains the result, the checks actually run, and any unresolved item. Do not include credentials, private local paths, or hidden model reasoning in commits, Pull Requests, or comments.
