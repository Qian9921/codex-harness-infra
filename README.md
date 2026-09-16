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

- `primary` is decision-only: scope, routing, targeted read-only acceptance, and the final report. It never edits files and never performs mechanical execution, including tiny code, docs, or config fixes. If the executor route is unavailable, repair a valid route; do not fall back to primary implementation.
- the selected executor performs bounded implementation and relevant verification. `native_only` uses the Codex executor as a complete path. Paid-aware modes prefer a configured paid/included executor; a config without `[routing]` keeps the legacy Grok-preferred adapter, now dispatched through the generic Pi adapter rather than GrokCLI. Backend identity and receipts load only from the selected backend skill.
- `reviewer` uses fresh context and reviews the current change read-only.

The local installation maps primary, executor, and reviewer roles to the models and tools available on that machine. Native model slugs, account mappings, credentials, opening instructions, and absolute paths remain local configuration. Shared policy names logical executor IDs and backends, not current native version strings.

## Executor routing

Copy `package/local.example.toml` and set `[routing].selection`:

- `native_only`: Codex-only. No Pi executable or quota receipt is required.
- `paid_preferred`: prefer a matching paid/included executor; fallback only if `[routing.fallback]` permits a cause such as `quota_exhausted`.
- `paid_strict`: same preference; block when the paid/included candidate is unsuitable, except for an explicit permitted fallback cause.
- omit `[routing]`: legacy Martin-like Grok-preferred, actual-model receipt, quota-only native fallback.

Supported adapters are native Codex and the generic Pi adapter (`backend = grok` or `backend = pi`). Cost preference is a user declaration, not a verified live balance. Unknown quota stays unknown. Model updates require changing local mappings only; do not treat a new native slug as an automatic migration. Do not fall back to GrokCLI.

```text
python scripts/executor_routing.py select --local-config <local-file> --capability implementation --tool workspace-write
python scripts/executor_routing.py select --local-config <local-file> --capability implementation --executor <id> --reason "<why>"
python scripts/executor_routing.py validate-receipt --local-config <local-file> --receipt <file> --task-id <id> --cwd <abs> --owned-path <abs>
```

Automatic selection prefers a single suitable paid/included executor. Multiple suitable candidates require `--executor` and `--reason`. Native dispatch is a registered Codex custom agent spawn (`invocation.agent`); reinstall after local model mapping changes. External dispatch is `invocation.kind = pi_bridge` with provider/model/thinking; native_only stays portable without Pi.

## Delivery

Discussion is read-only. Publication follows explicit `[delivery]` (`local_only` default, `pull_request`, or `merge_if_ready` plus authorized repositories). An explicit current request for a named PR may use one request-delivery-only ephemeral `--local-config` that contains only `[delivery]`; reuse it for every publication command and remove it afterward. The persistent file is unchanged. Installing or configuring credentials is not permission.

The author and reviewer use separate GitHub identities on the same machine. This is an audit and workflow boundary, not a claim of process isolation. The GitHub Pull Request, current head, checks, comments, and reviews are the durable delivery record.

Every new commit changes the review target. Merge requires a valid approval for the current head and the repository's required checks and rules.

## Installation boundary

The installer changes only explicitly owned files and marked blocks. It preserves unrelated personal configuration, tools, credentials, and user-authored rules. An unmarked file containing user content is not overwritten. It installs exactly one UserPromptSubmit hook so each new task sees installed instructions and local integrity checks (install manifest, instruction/config, Doctor subset). CodeGraph, Semble, RTK, and daemon probes are explicit Doctor flags or task-relevant; code investigation or change checks the owner CodeGraph index before exploration and cross-file impact uses a structural query. Install and migration run a bounded tool version/update check, and `doctor --probe-tools --probe-updates` re-checks on demand; both report available updates honestly and never upgrade. `install.json` and instruction files are the default authority; memory of earlier tasks is historical only. It installs no Stop hook, background service, daemon, or project-tracked index. Uninstallation removes only content owned by this project.

## Local activation

Copy `package/local.example.toml` to a local-only path. A Codex-only install needs `[models]` and `[routing].selection = "native_only"`; opening, Pi/Grok, GitHub, and optional tools may stay empty. Set `[delivery]` when GitHub publication is wanted. Run `python scripts/install.py install --local-config <local-file>`, which reports the bounded install/migration tool check; re-check on demand with `python scripts/doctor.py --probe-tools --probe-updates`. Model slugs stay in that local file; shared policy does not pin a current native version. Start `codex --profile v23-primary`. Review and trust the one V23 UserPromptSubmit hook in Codex's hook browser. Doctor reports installed skills and the runtime used at install.

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
