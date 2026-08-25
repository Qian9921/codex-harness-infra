# Architecture

## Purpose

Codex Harness Infra adds only the durable policy and local-to-GitHub integration that Codex and GitHub do not already provide. Codex remains responsible for agent execution, permissions, skills, and subagents. GitHub remains responsible for Pull Request state, review state, checks, and merge rules.

## Layers

### Portable policy

`AGENTS.md` is the short, always-relevant policy. In a repository it is the project instruction file. At Codex home it is also the canonical V23 global instruction file: `${CODEX_HOME}/AGENTS.md`. Official Codex precedence makes a non-empty `AGENTS.override.md` completely suppress `AGENTS.md` at that scope and skips an empty override, so V23 never installs that override and removes it on install or upgrade. A known V21 installed kernel is retired in full and replaced by V23 managed blocks only when `${CODEX_HOME}/AGENTS.md` is the exact authorized legacy file (10192 bytes, SHA-256 `49045df930cac1d0148575ad3f94b193383e4eee8abdb54e3472ccbef6a73bf7`) or an unambiguous newline-normalized form of that same digest. If the V21 identity markers are present but the complete content is not that file, installation fails before mutation. Ordinary unmanaged `AGENTS.md` text is preserved. The portable file contains the identity standard, direct-work preference, review boundary, delegation limits, and evidence rules. It contains no machine facts.

### Workflow agreement

`WORKFLOW.md` defines the three work kinds and the authorized repository delivery path. It makes the difference between discussing a change, writing locally, writing to GitHub, and taking an irreversible external action explicit.

### On-demand guidance

`.agents/skills/engineering-delivery/` contains the delivery skill and focused references for GitHub, tools, C++, Python, and research work. The references are loaded when relevant; they are not copied into the permanent context.

### Small helpers and one native task hook

`scripts/install.py`, `scripts/doctor.py`, `scripts/task_bootstrap.py`, and `scripts/github_delivery.py` support local installation, actionable setup checks, the required three-tool bootstrap, and the GitHub delivery adapter. The installer registers one UserPromptSubmit command hook that calls the installed bootstrap script. It does not run a replacement agent loop, scheduler, background service, Stop hook, or permission system. The Pull Request and current head remain the durable workflow record.

## Work and capability

Task kind and capability are independent:

| Work kind | Capability | Typical result |
| --- | --- | --- |
| `discuss` | `read_only` | Explanation, investigation, or review. |
| `repo_change` | `local_write` | Local implementation and relevant checks. |
| `repo_change` | `github_write` | Commit, push, Pull Request, review, and merge. |
| any | `consequential_external` | Separately confirmed production, account, data, or release action. |

An authorized repository change does not authorize unrelated external actions.

## Roles and delegation

`primary` owns the request, scope, decisions, repository state, and final communication. `executor` performs bounded implementation and verification. `reviewer` receives a fresh read-only context containing the request, current diff, relevant evidence, and current head SHA.

Independent read-heavy work can run in parallel. A worktree has one writer. Parallel writers require isolated worktrees and explicit file ownership. Delegation is complete only when it returns a concrete result, evidence, diff, or blocker.

## Design constraints

- Keep the permanent context short and load detail progressively.
- Prefer one coherent change that a reviewer can understand in one sitting.
- On every new task, health-check and actually use CodeGraph, Semble, and RTK once through the single native prompt hook.
- Use project-native tools and checks before adding a new dependency.
- Add a mechanism only when a concrete failure is identified and the existing mechanism cannot address it with less complexity.
- Preserve user-authored local state outside the marked ownership boundary.

## Non-goals

This project does not create a second agent runtime, a task database, a background coordinator, a Stop-hook loop, or a parallel source of GitHub truth. Apart from the three explicitly required tools, it does not turn every task into a ceremony or require every available tool on every task.

## Sources

The design follows the public Google code-review guidance on code health, coherent change size, and reviewable descriptions, together with Codex's native model of repository instructions, Skills, and Subagents:

- [Google code review standard](https://google.github.io/eng-practices/review/reviewer/standard.html)
- [Google small changes](https://google.github.io/eng-practices/review/developer/small-cls.html)
- [OpenAI AGENTS.md guidance](https://developers.openai.com/codex/guides/agents-md)
- [OpenAI Codex Skills](https://developers.openai.com/codex/skills)
- [OpenAI Codex Subagents](https://developers.openai.com/codex/subagents)
- [OpenAI Codex Hooks](https://learn.chatgpt.com/docs/hooks)
