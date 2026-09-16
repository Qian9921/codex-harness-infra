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
| Missing or stale index | Create or refresh (`codegraph init`/`sync`) only in an authorized writable owner repository, judging freshness from the current tree rather than commit metadata alone. Read-only work does not refresh: trace with bounded-search and state the limit. |
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
graph matches the tree: refresh only with authorized write in the owner
checkout and still open the cited current files. The cache is Git-local through
the V23-marked info/exclude block; no daemon and no global registry.

The Harness verifies `rtk git`, `rtk ls`, and `rtk pytest` help before routing
any of them, so RTK routes only that finite verified supported set. Other RTK
commands and exact-format diagnostics stay raw and preserve exit status and
diagnostics.

Optional tools are resolved from the configured `[tools]` path or PATH via
`command -v`. Missing, failed, or unsupported tools fall back to baseline
without repeated repair. Do not scan home, tmp, or workspace trees or internal
tool databases merely to discover setup. Availability checking is not required
on every task. tgrep is experimental, not a default backend, and is not
installed by this Harness; a user may already have it.

Install, migration, or explicit maintenance may run
`python scripts/doctor.py --probe-tools --probe-updates` again: bounded
versions and `codegraph upgrade --check`, reported honestly when offline or
unsupported. The installer reports the same bounded check at install time.
Never auto-upgrade, add a daemon, background index, or extra Stop hook.