# Installation boundary

Installation is deliberately narrow. It makes the portable policy and logical role templates available to Codex, installs the single delivery Skill and its references, and configures the small local adapters needed for the required task bootstrap and authorized GitHub delivery.

## Ownership

Every inserted block has an explicit project ownership marker. Every generated asset has an entry and exact digest in the V23 local manifest. The installer may create or update only:

- a project-owned file that is absent or still owned by this project;
- a marked block whose start and end markers are both present and intact;
- the project-owned local state needed by the helper itself.

If a target exists without the expected marker and contains user content, installation stops instead of overwriting it. A broken or partially edited marker also stops and reports the exact target for manual repair. The installer never restores an old snapshot over current user configuration.

## Global instruction path

The canonical V23 global instruction file is always `${CODEX_HOME}/AGENTS.md`. Codex treats a non-empty `AGENTS.override.md` as a complete global-scope replacement for `AGENTS.md` and skips an empty override; that override path is intended for temporary user overrides, so V23 does not install or retain it. On install or upgrade the installer deletes `${CODEX_HOME}/AGENTS.override.md` whether the file is V23-owned, unowned, or modified. A symlink is unlinked without touching its target. A directory or other non-file shape is refused instead of recursive deletion.

When `${CODEX_HOME}/AGENTS.md` is byte-identical to the authorized current V21 kernel (10192 bytes, SHA-256 `49045df930cac1d0148575ad3f94b193383e4eee8abdb54e3472ccbef6a73bf7`), or matches that digest after an unambiguous newline fold, the installer treats the file as retired V21 and replaces it with only the new V23 managed blocks. The V21 title and policy lines are a signature, not a classification: if they are present but the complete content is not the exact known file, installation raises `InstallError` before any mutation. Ordinary unmanaged `AGENTS.md` content is not treated as V21 and is preserved through the marked-block mechanism.

Validation and rendering complete before any mutation. `AGENTS.md`, other managed assets, and the durable V23 manifest are written before the override is unlinked, so a failed earlier step leaves the old override active. A failure while writing the new manifest rolls back only `AGENTS.md`, `config.toml`, the generated V23 assets, and the previous manifest bytes; the override is not moved or deleted, and a retry remains safe. If the override unlink fails after the manifest is durable, the installer leaves both in place for a safe retry or Doctor failure. An existing V23 manifest whose `agents_path` still names `AGENTS.override.md` is migrated in place: the new manifest records `AGENTS.md`.

## Preserved content

User-authored rules, unrelated agents, credentials, personal tools, existing integrations, and unrelated configuration remain untouched. Local account mappings, model mappings, tool paths, and the local greeting are environment data; they are not part of the portable repository. Ordinary user text in `AGENTS.md` outside V23 markers is kept.

The project does not manage or automatically start external tools that happen to be available on the machine. The sole exception is one UserPromptSubmit hook, installed as a marked inline config block, which runs a bounded CodeGraph, Semble, and RTK bootstrap for each new user task. Each operation has a small fixed budget whose worst-case sequence stays inside the Hook budget, so a stalled CodeGraph probe still leaves time for Semble and RTK. It does not use a Stop hook, daemon, background service, or project-tracked index. CodeGraph's cache exclusion is Git-local and marked before a V23-created cache is initialized.

## Upgrade and uninstall

An upgrade updates only the project's marked content and owned files. It does not scan the home directory or infer ownership from names. The global override file is an explicit exception: it is removed because official Codex precedence would otherwise suppress the canonical `AGENTS.md`. Uninstall removes a V23 installation only when its dependent blocks and generated assets are all still intact; if any owned item was edited, it preserves the complete unit and reports the conflict for manual resolution. After a migrated install, uninstall operates on `AGENTS.md` and does not recreate `AGENTS.override.md`.

## Verification

After installation, use Codex's hook browser once to review and trust the V23 UserPromptSubmit command. Codex requires explicit trust for non-managed command hooks; the installer never bypasses that protection. The doctor command requires canonical `${CODEX_HOME}/AGENTS.md` for managed-block checks, reports a non-empty `AGENTS.override.md` as `global_override_absent=false`, and names the effective Codex source in `active_global_instruction` (the non-empty override when present, otherwise `AGENTS.md`, matching official skip-empty behavior). It also reports local marked blocks, prompt hook, logical role files, real bounded tool probes, and configured GitHub adapter. Its first CodeGraph probe can create the V23-marked Git-local cache exclusion and index, just as the task hook can. Its local terminal output may name local files for repair, but it never prints credentials and that output must not be committed. Start a new Codex task after changed instructions or hook configuration are installed.

The primary mapping is a native local profile named `v23-primary`; launch it with `codex --profile v23-primary`. Its top-level model, reasoning effort, and `review_model` are rendered from local configuration. Executor and reviewer remain separate native custom agents.

## Recovery

If installation stops, preserve the target and its current contents. Read the reported path, repair or remove only the conflicting marker with the user's intent, and run the installer again. Do not use broad deletion or restore commands.
