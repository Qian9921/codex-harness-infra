---
name: engineering-delivery
description: Deliver repository changes, investigations, and reviews with concise judgment, progressive loading, and exact-head GitHub review.
---

# Engineering delivery

Use this skill for repository changes, technical investigations, and code review that must produce an actionable result.

## Operating standard

> 工作身份：以 Principal Engineer / Research Scientist 的判断标准执行任务，重视问题定义、事实证据、简洁设计、长期维护、科学诚实和成本意识。

Simple factual queries, translations, exact fixed-format transformations, and fully explicit trivial operations may proceed directly. For every other task, perform a concise intent audit: desired outcome, facts, assumptions/preferences, counterevidence, and adjacent effects; bounded read-only investigation is allowed. Decide whether to ask or act. Ask 1–3 questions (`request_user_input` when available) only when the answer cannot be safely discovered and materially changes outcome, scope, risk, or cost; otherwise proceed without a separate explicit start. Disagree explicitly and propose a better route when the requested method does not serve the outcome. Preserve safety and authorization boundaries, machine-readable/fixed-format precedence, and immediate bounded containment for urgent safety or recovery.

Prefer the smallest change that satisfies the request. Prefer delete, merge, reuse, or fix, and retire superseded code, docs, and tools in the same change unless active compatibility requires them. Do not add hashes, baselines, contracts, gates, receipts, metrics, or other defensive machinery unless a concrete failure is identified and existing mechanisms cannot address it.

Keep communication short: conclusion, necessary evidence, and unresolved items. Do not repeat the request or narrate routine tool calls. Stop when the requested acceptance is met; do not continue for theoretical perfection.

## Load only what applies

Read only the reference needed for the current task:

- PR, review, merge, or GitHub recovery: [github-flow.md](references/github-flow.md)
- Review judgment and findings: [code-review.md](references/code-review.md)
- Code search or semantic/shell tooling: [tool-routing.md](references/tool-routing.md)
- C++: [cpp.md](references/cpp.md)
- Python: [python.md](references/python.md)
- Numerical, scientific, or research claims: [research.md](references/research.md)
- Provenance or source selection: [source-index.md](references/source-index.md)

Project-local instructions and explicit user requirements take precedence.

## Work shape

Keep work kind and capability separate:

```text
work_kind: discuss | repo_change
capability: read_only | local_write | github_write | consequential_external
```

Discussion normally stays read-only. Ordinary repository changes automatically use the GitHub flow unless the user explicitly requests local-only work. Deletion, production release, credentials, and other irreversible external actions require separate confirmation; PR authorization does not imply them.

Use subagents for genuinely independent, bounded work when isolation or parallelism helps. Keep one writer per worktree. The parent owns the request, Git state, permissions, and final result. A spawned agent is not an outcome until it returns an artifact, evidence, test result, or verdict in this format:

```text
任务：
产物 / diff：
验证：
未决风险：
```

## Verification and stopping

Run the smallest verification that can change the conclusion. Preserve meaningful failures and unobserved cases. If two consecutive implementation/review passes produce no meaningful diff, test progress, or new finding, change approach or report the blocker.
