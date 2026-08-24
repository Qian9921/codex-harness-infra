# Codex Harness Infra

Codex Harness Infra is a small, portable policy and delivery layer for Codex. It defines a durable engineering quality bar, loads detailed guidance only when needed, and connects authorized repository work to a GitHub Pull Request with independent review. It is not another agent runtime.

## What belongs here

```text
AGENTS.md                         short repository-wide rules
WORKFLOW.md                       work and delivery agreement
.agents/skills/                   on-demand delivery guidance
package/agents/                   portable logical role templates
scripts/                          small local and GitHub helpers
tests/                            focused behavior and installation tests
.github/                          repository review and CI configuration
docs/                             architecture and operating references
```

Codex supplies the agent loop, permissions, skills, and subagent primitives. This repository supplies only the project-specific policy and the small adapter needed for the GitHub workflow.

## Roles

The portable roles are `primary`, `executor`, and `reviewer`:

- `primary` owns the request, scope, decisions, and final communication.
- the external Grok bridge performs bounded implementation and relevant verification at low effort; the native `executor` is used only after a verified Grok quota-exhaustion receipt.
- `reviewer` uses fresh context and reviews the current change read-only.

The local installation maps primary, fallback executor, and reviewer roles to the native models and tools available on that machine. Native model slugs, account mappings, credentials, opening instructions, and absolute paths remain local configuration; the external Grok execution identity is a portable product contract.

## Delivery

Discussion is read-only. A normal repository change automatically follows this path unless the user explicitly requests local-only work:

```text
understand → implement → verify → commit → push → Pull Request
          → independent review → feedback/fix → approval → merge
```

The author and reviewer use separate GitHub identities on the same machine. This is an audit and workflow boundary, not a claim of process isolation. The GitHub Pull Request, current head, checks, comments, and reviews are the durable delivery record.

Every new commit changes the review target. Merge requires a valid approval for the current head and the repository's required checks and rules.

## Installation boundary

The installer changes only explicitly owned files and marked blocks. It preserves unrelated personal configuration, tools, credentials, and user-authored rules. An unmarked file containing user content is not overwritten. It installs exactly one UserPromptSubmit hook because this V23 requires CodeGraph, Semble, and RTK to be health-checked and used for every new task; it installs no Stop hook, background service, daemon, or project-tracked index. Uninstallation removes only content owned by this project.

## Local activation

Copy `package/local.example.toml` to a local-only path, fill its model, opening, GitHub, Python runtime, and all three tool fields, then run `python scripts/install.py install --local-config <local-file>`. Start the primary V23 profile with `codex --profile v23-primary`; it selects the local primary model and maps native `/review` to the local review model. Review and trust the one V23 UserPromptSubmit hook in Codex's hook browser before relying on task bootstrap. The V23 executor and reviewer remain separately registered custom agents.

## Start here

- [Architecture](docs/architecture.md)
- [Workflow](WORKFLOW.md)
- [GitHub delivery](docs/github-flow.md)
- [Tool routing](docs/tool-routing.md)
- [Evaluation](docs/evaluation.md)
- [Engineering standards](docs/engineering-standards.md)
- [Installation boundary](docs/installation-boundary.md)

## Development

Use the repository's supported Python environment and focused tests. Keep each change coherent and small enough for one independent review. Record a non-obvious architectural decision in `docs/decisions/`.

## License

See [LICENSE](LICENSE).
