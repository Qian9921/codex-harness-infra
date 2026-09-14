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

Preferred selection, CLI examples, and result use live in the installed
engineering-delivery reference:
[`.agents/skills/engineering-delivery/references/tool-routing.md`](../.agents/skills/engineering-delivery/references/tool-routing.md).
Exact file, symbol, or text lookup stays `bin/bounded-search.py` or a direct
read. Optional CodeGraph is for structural disambiguation, callers,
dependencies, or impact after that lexical lookup is insufficient.

The Harness default recursive search helper is the installed
`${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py` (not an OS sandbox): 15s
timeout; after timeout or incomplete, narrow and retry; never treat incomplete
as no-match. Known individual-file reads may stay direct.

For a Git checkout, CodeGraph keeps a V23-marked `.codegraph/` cache exclusion
in that checkout's Git-local info/exclude; it never changes the repository's
`.gitignore` or enables a CodeGraph daemon. In a non-Git directory, CodeGraph
performs a version probe because there is no repository source graph to
initialize. Semble Doctor/probe timeout is 36 seconds so a ~29-second
owned-scope search is not reported as a false tool failure. The live
runtime-state probe that runs `codex app-server daemon version` is budgeted 12
seconds so a ~9-second JSON response is not reported as a false CLI/app-server
timeout.

When a tool fails during an explicit Doctor or task-relevant call, repair only
V23-owned setup automatically. Do not silently reinstall, upgrade, or
reconfigure an independent user tool. Tool output is evidence, not a
conclusion.
