# GitHub delivery

Use this card for repository changes delivered through a pull request. It complements, and does not replace, repository branch protection.

Use this path only when `[delivery].mode` is `pull_request` or `merge_if_ready` and the repository is listed. Installing or configuring credentials is not authorization.

## Flow

```text
understand request
→ make the smallest useful change
→ run relevant tests
→ commit logical milestones
→ author pushes and opens or updates the PR
→ fresh reviewer examines the current head SHA
→ reviewer identity submits the GitHub review
→ fix findings and repeat from the new SHA when needed
→ verify CI, threads, approval, head, and branch rules
→ merge
```

The local configuration maps author and reviewer roles to separate accounts on the same machine. This is an audit-identity separation, not a claim of a separate security boundary. The author must also be the GitHub actor that pushes the branch: select that author's isolated Git credential helper before pushing, rather than relying on the machine's default Git credential. Do not put account names, tokens, `GH_CONFIG_DIR` values, or machine paths in the repository.

## Invariants

- Every review verdict names `reviewed_sha`.
- Any commit after approval requires a fresh review of the new head.
- The final merge check confirms current head, required CI, no unresolved blocking threads, reviewer identity, and branch rules or merge-queue state.
- Model approval and formal GitHub approval are separate facts.
- Resume from current GitHub state after interruption; retry only idempotent operations.
- Keep secrets out of commits, PR text, logs, command arguments, and evidence.

Use a bounded review/fix loop. If the same finding repeats or a round makes no observable progress, treat that as stalled progress and report it. If CI, permissions, missing approval, or network prevent the next step, treat that as an external block. Do not stop because a review-round counter expired.

Make one logical change per commit. Keep tests with the behavior they verify. Do not manufacture commits for volume. Preserve intent, changed behavior, actual tests, findings, dispositions, and the final SHA in the PR; never paste private reasoning or noisy transcripts.
