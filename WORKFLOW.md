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

This contract applies to both `discuss` and `repo_change`. Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly. For every other task, before implementation, mutation, external action, or a committed plan, reason carefully, test assumptions, consider adjacent consequences and reusable implications, and identify missing decisions. Do not expose hidden chain-of-thought; show a concise synthesis of current understanding, assumptions, risks, and decision points. Ask a first structured batch of 2–3 high-value, mutually exclusive questions with tradeoffs and a recommendation (`request_user_input` when available). Ask further rounds when answers expose new decision-changing questions. Exhaust useful questions about outcome, scope, constraints, priorities, evidence, tradeoffs, acceptance, and rollout; do not pad, repeat, or ask what can be discovered safely. Bounded read-only investigation is allowed. Do not mutate repositories or external state until questioning has enough information. Once information is sufficient, proceed without a separate explicit start. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery.

## DISCUSS

For `discuss`, stay read-only. Only the simple/direct exceptions in Participatory questioning may answer directly; every other `discuss` task follows that shared questioning contract. Do not create a branch, commit, Pull Request, review, comment, or merge. If the discussion identifies a later change, stop at the boundary and wait for that change to be requested or otherwise authorized.

## REPO_CHANGE

For every repository change unless the user explicitly requests local-only work:

```text
understand → implement → verify → commit → push → Pull Request
          → fresh independent review → fix if needed → approval → merge
```

The primary role owns the request, scope, decisions, and final result. The preferred external Grok execution route performs bounded implementation and verification at low effort. The native executor is a quota-exhaustion-only fallback. The reviewer receives the request, current diff, relevant evidence, and current head SHA in fresh read-only context.

The author and reviewer are different GitHub identities. The author must be the GitHub actor that pushes the branch; on a shared machine, explicitly select the author's isolated Git credential helper instead of inheriting the default credential. The reviewer model's verdict, the GitHub approval, and GitHub's branch rules are separate facts. A review is valid only for the head SHA it inspected. Any later commit requires a new review.

Use the V23 delivery adapter for branch push, PR creation, GitHub review, and
merge checks. Its push operation requires an explicit worktree and refspec and
reads one raw local credential-free HTTPS `github.com` URL, then starts an
otherwise config-isolated Git push with the configured author's GH_CONFIG_DIR
credential helper. That excludes ambient Git credential/header configuration,
environment injection variables, and URL rewrites.

Merge only after the current head has the required checks, no blocking unresolved feedback, and a valid approval from the configured reviewer identity. Let GitHub enforce repository rules; do not imitate them with a local process.

Commits should be small, complete, and understandable in one sitting. Keep related tests with the behavior they protect. Do not create noisy commits merely to increase the count.

## CONSEQUENTIAL_EXTERNAL

Production releases, deletion of user data, credential changes, account operations, and other irreversible external actions require separate explicit confirmation. Ordinary Pull Request delivery does not silently expand into these actions.

## Delegation

Use subagents for independent read-heavy investigation, testing, or review when that reduces context noise. Use one writer per worktree. Do not parallelize tightly coupled edits merely for appearance. A delegated task is complete only when it returns a concrete result, evidence, diff, or blocker.

## Interruption and recovery

The Pull Request and its current branch head are the durable delivery record. After an interruption, query GitHub and the checkout, then continue only with the next operation still needed. Do not create a second task database or duplicate workflow history.

## Communication

Keep progress updates short and factual. The final report contains the result, the checks actually run, and any unresolved item. Do not include credentials, private local paths, or hidden model reasoning in commits, Pull Requests, or comments.
