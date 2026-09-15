# Delivery workflow

This file defines how the portable roles cooperate with Codex and GitHub. It is a small operating agreement, not a second agent runtime. Selected-backend internals (including Pi/Grok identity, supervision, effort, PGID, and quota labels) load only from that backend's skill.

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

This contract applies to both `discuss` and `repo_change`. Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly as replies or read-only work; they never write files. Those exceptions never authorize implementation or publication by themselves. For every other task, perform a concise intent audit: desired outcome, facts, assumptions/preferences, counterevidence, and adjacent effects; bounded read-only investigation is allowed. Decide whether to ask or act. Ask 1–3 questions (`request_user_input` when available) only when the answer cannot be safely discovered and materially changes outcome, scope, risk, or cost; otherwise proceed without a separate explicit start. Disagree explicitly and propose a better route when the requested method does not serve the outcome. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery. Preserve intent across turns; separate facts from preferences; show evidence when a conclusion changes; state material assumptions only when they would change the decision.

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

The primary role is decision-only: request, scope, routing, targeted read-only
acceptance, and the final result. Primary never edits files and never performs
mechanical execution, including tiny code, docs, or config fixes. The locally
selected execution route performs one bounded implementation, test, and fix
pass and returns concise files, behavior, evidence, and unresolved items.
Primary then runs targeted independent read-only verification of those claims
instead of duplicating the full implementation. If no valid executor route is
available, repair routing; do not fall back to primary implementation.
`native_only` is a complete Codex executor path. When routing selects another
backend, load that backend's skill for identity, supervision, and receipts.
The reviewer receives the request, current diff, relevant evidence, and current
head SHA in fresh read-only context.

Executor selection is local and capability-based. Run
`python scripts/executor_routing.py select --local-config <file> --capability implementation`
(or the installed `bin/executor-routing.py`). Do not treat unknown quota as
zero, do not migrate just because a new native slug exists, and do not label
network/auth/timeout/bridge errors as quota. Model names are frozen at install,
update, or actual use of the local mapping; do not auto-upgrade shared policy
to a new native version string. Shared instructions use the logical `primary`
role name, not a native model slug.

The author and reviewer are different GitHub identities. The author must be the GitHub actor that pushes the branch; on a shared machine, explicitly select the author's isolated Git credential helper instead of inheriting the default credential. The reviewer model's verdict, the GitHub approval, and GitHub's branch rules are separate facts. A review is valid only for the head SHA it inspected. Any later commit requires a new review.

Use the V23 delivery adapter for branch push, PR creation, GitHub review, and
merge checks. Publication commands require `--local-config`. An explicit current
request to open a named PR while standing policy is `local_only` is honored
without re-asking and without rewriting the persistent config: write one
request-delivery-only ephemeral file containing only `[delivery]` with the
exact requested mode and repository, reuse that same file for every publication
command, then remove it after delivery. Do not copy the persistent config.

```text
python scripts/delivery_policy.py effective --local-config <persistent.toml> \
  --mode pull_request --repository <owner/name> --output <ephemeral.toml>
python scripts/github_delivery.py push --local-config <ephemeral.toml> ...
python scripts/github_delivery.py ensure-pr --local-config <ephemeral.toml> ...
rm <ephemeral.toml>
```

Its push operation requires an explicit worktree and refspec and
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
