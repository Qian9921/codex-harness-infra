# Tool routing

The native UserPromptSubmit hook injects installed instructions and a bounded
live runtime-state block (install manifest and instruction/config integrity).
Daemon probes are explicit. It uses no Stop hook, scheduler, or task database.
Memory of earlier tasks is not treated as current runtime authority. Optional
`[tools]` paths are unused unless a task has a concrete need. CodeGraph,
Semble, and RTK are not mandatory on every prompt and are not started as a
batch probe. Explicit Doctor `--probe-tools` / `--probe-daemons` remain
available. Missing or failed optional tools fall back to baseline search or
direct reads; they must not block unrelated work. This file is prompt
guidance, not a classifier, hook, or gate.

| Need | Preferred tool | When and how |
| --- | --- | --- |
| Known file, exact symbol, or exact text | `bin/bounded-search.py` or a direct read | Complete, lightweight baseline. Do not open optional tools first. |
| Cross-file callers, dependencies, or impact | CodeGraph, when configured and the owner index is usable | Focused query; keep actual symbol or source evidence. |
| Unknown implementation or similar capability after bounded keywords are insufficient | Semble, when configured | One focused query in a known repo or module; inspect top files. |
| Compact supported pytest summary when details are unnecessary | `rtk pytest`, when configured | Optional. Never a Harness prerequisite install. |
| Unified diff, porcelain/JSON, exact diagnostics | Raw project commands | Do not wrap or rewrite the shell. |
| Experimental text search | tgrep | Not a default backend. This Harness does not install it; a user may already have it. |

## Baseline search

Require an exact repository, module, or explicit file list. Locate files first.
Do not scan `/home`, `/tmp`, umbrella worktrees, artifacts, or caches for local
clues. Normally run one single-thread recursive search at a time with a 15s
timeout and bounded kill. After timeout, narrow the scope and report incomplete;
never treat timeout as no-match. Do not bypass with grep, Python, or another
scanner. The Harness default helper is the installed
`${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py`. It is not an OS sandbox.
It serializes per-user cross-process searches, runs `rg --threads 1 --no-config`,
caps combined output, and distinguishes match, no-match, error, timeout, and
incomplete. Timeout and incomplete are never no-match. Known individual-file
reads may stay direct. It does not replace system `rg` or change the shell.

```text
python "${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py" \
  --root <repo-or-module> --pattern <pattern> [--path <file-or-dir>]
```

Use the helper result status (`match`, `no-match`, `error`, `timeout`,
`incomplete`) as evidence. After timeout or incomplete, narrow and retry.

If a command or flag is unknown or the installed version differs, inspect
`--help` on that binary and fall back to baseline. Do not assume a remembered
command shape.

## CodeGraph

Use only for cross-file structure (callers, callees, impact, symbol location)
when a configured binary exists and a usable owner index is available.
Read-only work may query a fresh usable owner index. Do not treat `status`
alone as proof the graph matches the tree: open the cited current files.
Graph output is not a substitute for reading the source that matters.

Verified current CLI (inspect `--help` if a local build differs):

```text
codegraph status <repo>
codegraph status --json <repo>
codegraph query -p <repo> --json <symbol>
codegraph callers -p <repo> --json <symbol>
codegraph impact -p <repo> --json <symbol>
```

`status` takes a positional path (`status [path]`, `status --json [path]`),
not `-p`. `callers`/`query`/`impact` accept `-p`/`--path`.

For a Git checkout, CodeGraph keeps a V23-marked `.codegraph/` cache exclusion
in that checkout's Git-local info/exclude; it never changes the repository's
`.gitignore` or enables a CodeGraph daemon. Refresh a missing or stale index
only when the executor has authorized write in that checkout and the task
needs structure that baseline search cannot provide. If the index is missing
or stale and write refresh is not authorized, trace callers with
bounded-search and state that limit. Do not index every task, start a
CodeGraph daemon, or edit a global registry.

In a non-Git directory, CodeGraph performs a version probe because there is no
repository source graph to initialize.

## Semble

Use only after a bounded keyword search is insufficient for unknown
implementation or similar-capability discovery. Query a known repository or
module, not an umbrella workspace. Inspect the top returned files for
relevance. Query echo, install status, or readiness is not an answer.

```text
semble search -k 5 --max-snippet-lines 20 "<query>" <repo-or-module>
```

Doctor/probe timeout is 36 seconds so a ~29-second owned-scope search is not
reported as a false tool failure. If Semble is missing, errors, or returns
nothing useful, fall back to baseline without repeated init or repair.

The live runtime-state probe that runs `codex app-server daemon version` is
budgeted 12 seconds so a ~9-second JSON response is not reported as a false
CLI/app-server timeout. That probe is explicit, not a per-task tool scan.

## RTK

Use only for supported compact test-output summaries when failure details are
unnecessary. Preserve failures, diagnostics, and the process exit status.

Dedicated pytest summary:

```text
rtk pytest <args>
```

Other test commands, when a compact wrapper is actually needed:

```text
rtk test -- <command>
```

Use raw `git diff`, `git status --porcelain`, JSON parsers, compilers, and
test runners for unified diffs, machine-readable status, and exact
diagnostics. Do not prefix arbitrary shell with RTK. Optional; this Harness
does not install it.

## tgrep

tgrep is experimental. It is not a default search backend or a Harness
service. This Harness does not install it; a user may already have it
independently. Mention freshness and resource cost only when it is actually
used; implement no adapter.

## Failure and repair

When a tool fails during an explicit Doctor or task-relevant call, repair only
V23-owned setup automatically. Do not silently reinstall, upgrade, or
reconfigure an independent user tool. Tool output is evidence, not a
conclusion. Cross-check source, build, test, or runtime results when the task
depends on them. Never put credentials or private machine paths in PR evidence.
