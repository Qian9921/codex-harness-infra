# Tool routing

The native UserPromptSubmit hook injects installed instructions and local
integrity checks. Daemon, CodeGraph, Semble, and RTK probes are explicit or
task-relevant. It is the only V23 hook and is never a Stop hook. Optional
tools are selected only for a concrete need and actually invoked when they
fit. Missing or failed tools fall back to baseline. Explicit Doctor/
`probe_tools` checks remain usable. A tool failure must not block unrelated
work. This is prompt guidance, not a classifier or gate.

| Need | Tool | Invoke when |
| --- | --- | --- |
| Known file, exact symbol, or exact text | `bin/bounded-search.py` or a direct read | Always the baseline. |
| Cross-file callers, dependencies, or impact | CodeGraph | Configured binary, owner index, current content. |
| Unknown implementation after insufficient keywords | Semble | Focused known repo/module; inspect top files. |
| Compact supported pytest summary | `rtk pytest` | Prefer when available and full diagnostics are unnecessary. Harness does not install it. |
| Diff, porcelain/JSON, exact diagnostics | Raw commands | Do not wrap the shell. |
| Experimental text search | tgrep | Not a default backend. Harness does not install it; a user may have it. |

Require an exact repository, module, or explicit file set. Locate files first.
Do not scan `/home`, `/tmp`, umbrella worktrees, artifacts, or caches for local
clues. Normally one single-thread recursive search at a time, 15s timeout with
bounded kill. After timeout, narrow and report incomplete; never call timeout a
no-match. Do not bypass with grep or Python.

```text
python "${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py" \
  --root <repo-or-module> --pattern <pattern> [--path <file-or-dir>]
```

The Harness default for recursive content search is that helper (not an OS
sandbox). Known individual-file reads may stay direct.

CodeGraph, when used:

```text
codegraph status <repo>
codegraph status --json <repo>
codegraph query -p <repo> --json <symbol>
codegraph callers -p <repo> --json <symbol>
codegraph impact -p <repo> --json <symbol>
```

`status` takes a positional path, not `-p`. Read-only work may use a fresh
usable owner index and must still open cited current files; do not rely on
status alone. Refresh a missing or stale index only under an authorized write
executor. Otherwise trace source with bounded-search and state the limit. Do
not index every task, start a daemon, or edit a global registry.

Semble, when used after bounded keywords fail:

```text
semble search -k 5 --max-snippet-lines 20 "<query>" <repo-or-module>
```

Inspect top files. Query echo or status is not an answer. On failure, baseline
without repeated init repair.

RTK, when used for compact pytest summaries:

```text
rtk pytest <args>
```

Preserve failures, diagnostics, and exit status. Use raw commands for unified
diff and porcelain/JSON.

tgrep is experimental, not a default backend, and is not installed by this
Harness; a user may already have it. Mention freshness/resource cost only if
used; implement no adapter. Unknown or version-different commands: inspect
`--help`, then baseline.

Resolve an optional tool only from its configured path or PATH via
`command -v`. If it is unavailable, use baseline. Do not scan home, tmp, or
workspace trees, or inspect internal tool databases, merely to discover
setup. After sufficient evidence, do not probe again. Availability checking
is not required on every task.

Prefer available RTK for supported compact test summaries when full
diagnostics are unnecessary. Use raw commands for exact details. Do not
force RTK on a decisive diagnostic task.

The CodeGraph cache is Git-local and ignored through a V23-marked info/exclude
block; it is not committed and no daemon is started.

Repair only V23-owned setup automatically. Tool output is evidence, not a
conclusion. Cross-check source, build, test, or runtime results when the task
depends on them. Never put credentials or private machine paths in PR evidence.
