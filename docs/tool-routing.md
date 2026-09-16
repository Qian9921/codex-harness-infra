# Tool routing

Tool obligations have explicit triggers and are not a per-task batch probe,
classifier, or tool-call gate. Code investigation or change checks the owner
repository CodeGraph index BEFORE code exploration; cross-file callers,
dependencies, or impact use a focused structural query. Known file, exact
symbol, or exact text stays `bin/bounded-search.py` or a direct read. The
native UserPromptSubmit hook injects installed instructions and a bounded live
runtime-state block (install manifest and instruction/config integrity); it is
the only V23 hook, is never a Stop hook, and does not probe optional tools.
Daemon probes are explicit.

A missing or stale index is created or refreshed only in an authorized writable
owner repository from current-tree freshness, not commit metadata alone. A
reported zero `pendingChanges` is not proof of freshness: CodeGraph has
reported zero while `sync` then found added and modified files, so writable
work syncs before relying on the index. Read-only work does not refresh: it
treats freshness as unknown, traces source with the bounded-search helper, and
states the limit. Optional CodeGraph is for structural
disambiguation, callers, dependencies, or impact. Semble stays optional after
bounded keywords fail. RTK routes only a finite verified supported set
(`rtk git`, `rtk ls`, `rtk pytest`); the explicitly loaded owned Pi extension
routes bare `pytest` through `rtk pytest` by default, while `python -m pytest`,
`pytest3`, and explicit interpreter or pytest paths stay raw because that
route does not prove it preserves the selected executable. Optional tools
resolve from the `V23_*` override, then the configured `[tools]` path, then
PATH only when unconfigured; the bridge reads the local config
(`--local-config`, else `${CODEX_HOME}/harness/v23/local.toml`, else
`~/.config/codex-harness/local.toml`) and propagates `[tools].rtk` to the Pi
child as `V23_RTK_BIN`.
Exact JSON, porcelain, diffs, and
necessary raw diagnostics stay raw and preserve exit status; unsupported
compound shell syntax is never silently rewritten, and a missing rtk falls
back to the explicit raw command. A tool that is
absent, fails, or cannot provide a writable index uses an explicit short
baseline fallback and says so; never report it as not needed. Tool output is
evidence, not a conclusion.

Explicit Doctor `--probe-tools` / `--probe-daemons` remain available. Install
and migration report a bounded version/update result for configured tools, and
explicit maintenance can rerun it with `--probe-updates` (installed versions
plus `codegraph upgrade --check`), reported honestly when offline or
unsupported; it never upgrades, starts a daemon, background index, or installs
another hook.

Preferred selection, CLI examples, and result use live in the installed
engineering-delivery reference:
[`.agents/skills/engineering-delivery/references/tool-routing.md`](../.agents/skills/engineering-delivery/references/tool-routing.md).

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

The Pi path receives these obligations through the adapter's bound prompt and
the project `.agents/skills`; this repository ships no Pi tool-call gate.
When a tool fails during an explicit Doctor or task-relevant call, repair only
V23-owned setup automatically. Do not silently reinstall, upgrade, or
reconfigure an independent user tool. Never put credentials or private machine
paths in PR evidence.