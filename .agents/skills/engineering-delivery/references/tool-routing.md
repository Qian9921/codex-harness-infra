# Tool routing

These obligations have explicit triggers. They are never a batch probe of every
task, a classifier, or a tool-call gate; trigger them from the task's own work
kind and questions, and say when a fallback was used.

| Trigger | Obligation |
| --- | --- |
| Code investigation or change | Check the owner repository CodeGraph index (`codegraph status --json <repo>`) BEFORE code exploration, then cross-check the cited current source. |
| Cross-file callers, dependencies, or impact | Run a focused structural query (`codegraph query|callers|callees|impact`); a file listing or lexical match cannot waive it. |
| Known file, exact symbol, or exact text | Baseline: `bin/bounded-search.py` or a direct read. |
| Insufficient bounded keywords for an unknown implementation | Optional focused Semble in a known repo/module; inspect the cited files. Query echo is not an answer. |
| Compact supported pytest summary | `rtk pytest <args>` when that route is verified. |
| Exact JSON, porcelain, unified diff, or necessary raw diagnostic | Raw command; never route it through a summarizer. |
| Missing or stale index | Create or refresh (`codegraph init`/`sync`) only in an authorized writable owner repository, judging freshness from the current tree rather than commit metadata alone. A reported zero `pendingChanges` is not proof of freshness: CodeGraph has reported zero while `sync` then found added and modified files, so writable work syncs before relying on the index. Read-only work does not refresh: treat freshness as unknown, trace with bounded-search, and state the limit. |
| Tool absent, real failure, or read-only missing index | Explicit short baseline fallback (bounded-search/direct reads or raw commands) and say so; never report it as "not needed". |

Recursive content search MUST use the installed Harness helper
`${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py` (not an OS sandbox). 15s
timeout; after timeout or incomplete, narrow and retry; never treat incomplete
as no-match. Known individual-file reads may stay direct. Do not bypass with
grep or Python.

```text
python "${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py" \
  --root <repo-or-module> --pattern <pattern> [--path <file-or-dir>]
```

CodeGraph, when triggered:

```text
codegraph status --json <repo>
codegraph query -p <repo> --json <symbol>
codegraph callers -p <repo> --json <symbol>
codegraph impact -p <repo> --json <symbol>
```

`status` takes a positional path, not `-p`. The declared state is not proof the
graph matches the tree: a zero `pendingChanges` can hide added or modified
files, so refresh with `sync` in the authorized writable owner checkout before
relying on it and still open the cited current files. Read-only work treats
freshness as unknown and does not refresh. The cache is Git-local through the
V23-marked info/exclude block; no daemon and no global registry.

The Harness verifies `rtk git`, `rtk ls`, and `rtk pytest` help and then
dispatches the pytest route, so RTK routes only that finite verified supported
set. The explicitly loaded owned Pi extension routes bare `pytest` through
`rtk pytest` by default; `python -m pytest`, `pytest3`, and explicit
interpreter or pytest paths stay raw because that route does not prove it
preserves the selected executable. Exact JSON, porcelain,
diffs, and necessary raw diagnostics stay raw, unsupported compound shell
syntax is never silently rewritten, a missing rtk falls back to the explicit
raw command, and a routed failure preserves exit status and diagnostics. Other
RTK commands and exact-format diagnostics stay raw.

Optional tools resolve from the `V23_*` override, then the configured `[tools]`
path, then PATH via `command -v` only when unconfigured. The bridge reads the
local config (`--local-config`, else the installed
`${CODEX_HOME}/harness/v23/local.toml`, else `~/.config/codex-harness/local.toml`)
and propagates `[tools].rtk` to the Pi child as `V23_RTK_BIN`. Missing, failed,
or unsupported tools fall back to baseline
without repeated repair. Do not scan home, tmp, or workspace trees or internal
tool databases merely to discover setup. Availability checking is not required
on every task. tgrep is experimental, not a default backend, and is not
installed by this Harness; a user may already have it.

Install, migration, or explicit maintenance may run
`python scripts/doctor.py --probe-tools --probe-updates` again: bounded
versions and `codegraph upgrade --check`, reported honestly when offline or
unsupported. The installer reports the same bounded check at install time.
Never auto-upgrade, add a daemon, background index, or extra Stop hook.