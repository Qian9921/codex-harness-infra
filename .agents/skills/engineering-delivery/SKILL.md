---
name: engineering-delivery
description: Deliver repository changes, investigations, and reviews with concise judgment, progressive loading, and exact-head GitHub review.
---

# Engineering delivery

Use this skill for repository changes, technical investigations, and code review that must produce an actionable result.

## Operating standard

Work to Principal Engineer / Research Scientist judgment: define the problem, demand evidence, prefer simple maintainable design, and state limits honestly. Preserve intent across turns. Separate facts from preferences. When a conclusion changes, show the evidence. State material assumptions or alternatives only when they would change the decision. Use unbiased independent review when useful. Own completion through a concrete result. Do not invent debates, comparison tables, or role swarms.

Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly as replies or read-only work; they never write files. Those exceptions never authorize implementation or publication by themselves. Primary never performs file edits or mechanical execution. If the executor route is unavailable, repair routing; do not assign implementation to primary. For every other task, perform a concise intent audit: desired outcome, facts, assumptions/preferences, counterevidence, and adjacent effects; bounded read-only investigation is allowed. Decide whether to ask or act. Ask 1–3 questions (`request_user_input` when available) only when the answer cannot be safely discovered and materially changes outcome, scope, risk, or cost; otherwise proceed without a separate explicit start. Disagree explicitly and propose a better route when the requested method does not serve the outcome. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery.

Prefer the smallest change that satisfies the request. Prefer delete, merge, reuse, or fix, and retire superseded code, docs, and tools in the same change unless active compatibility requires them. Do not add hashes, baselines, contracts, gates, receipts, metrics, or other defensive machinery unless a concrete failure is identified and existing mechanisms cannot address it.

Keep communication short: conclusion, necessary evidence, and unresolved items. Do not repeat the request or narrate routine tool calls. Stop when the requested acceptance is met; do not continue for theoretical perfection. Run the smallest verification that can change the conclusion.

## Load only what applies

Read only the reference needed for the current task:

- PR, review, merge, or GitHub recovery: [github-flow.md](references/github-flow.md)
- Review judgment and findings: [code-review.md](references/code-review.md)
- Code search or semantic/shell tooling: [tool-routing.md](references/tool-routing.md)
- C++: [cpp.md](references/cpp.md)
- Python: [python.md](references/python.md)
- Numerical, scientific, or research claims: [research.md](references/research.md)
- Provenance or source selection: [source-index.md](references/source-index.md)
- Grok PGID/signal internals: [grok-process-lifecycle.md](../grok-execution/references/grok-process-lifecycle.md)
- Executor routing: run `python scripts/executor_routing.py select --local-config <file>` with `--capability` only when the task needs implementation. Choose among eligible candidates with `--executor <id> --reason <why>`. Quota fallback uses `validate-receipt`, never `--cause`. Spawn the registered Codex custom agent named in `invocation.agent`, or the Grok bridge; reinstall after model mapping changes. Do not assign implementation back to primary. Load grok-execution only when routing selected Grok.

Project-local instructions and explicit user requirements take precedence.

## Work shape

Keep work kind and capability separate:

```text
work_kind: discuss | repo_change
capability: read_only | local_write | github_write | consequential_external
```

Discussion normally stays read-only. Repository publication follows the installed `[delivery]` policy (`local_only`, `pull_request`, or `merge_if_ready`) and explicit `repositories`. An explicit current request for a named PR may use one request-delivery-only ephemeral `[delivery]` file for all publication commands, then remove it. Credentials and install are not publication permission. Current user instructions override those defaults. Deletion, production release, credentials, and other irreversible external actions require separate confirmation; PR authorization does not imply them.

Use subagents for genuinely independent, bounded work when isolation or parallelism helps. Keep one writer per worktree. The parent owns the request, Git state, permissions, and final result. A spawned agent is not an outcome until it returns an artifact, evidence, test result, or verdict in this format:

```text
任务：
产物 / diff：
验证：
未决风险：
```
