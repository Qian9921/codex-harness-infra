# Tool routing

The native UserPromptSubmit hook injects installed instructions and local
integrity checks. Daemon, CodeGraph, Semble, and RTK probes are explicit or
task-relevant. It is the only V23 hook and is never a Stop hook. CodeGraph,
Semble, and RTK are task-relevant; explicit Doctor/`probe_tools` checks remain
usable. A tool failure must not block unrelated work.

| Need | Tool | Use it for |
| --- | --- | --- |
| Task-relevant source index/query | CodeGraph | Index status/sync plus a real file query |
| Task-relevant semantic search | Semble | Bounded search of a known repo or module |
| Task-relevant workspace command | RTK | Compact Git status or directory inspection |
| Local text search | installed `bin/bounded-search.py` | Exact repo, module, or file targets |
| Straightforward file work | `git`, project tools | Direct local operations |
| Follow-on investigation | Best-fit tool | Expand only if it changes the decision |

Require an exact repository, module, or explicit file set. Locate files first.
Do not scan `/home`, `/tmp`, umbrella worktrees, artifacts, or caches for local
clues. Normally one single-thread recursive search at a time, 15s timeout with
bounded kill. After timeout, narrow and report incomplete; never call timeout a
no-match. Do not bypass with grep or Python. The Harness default for recursive content search is the installed helper
`${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py` (not an OS sandbox), 15s
timeout. After timeout or incomplete, narrow and retry; never treat incomplete
as no-match. Known individual-file reads may stay direct.

The CodeGraph cache is Git-local and ignored through a V23-marked info/exclude
block; it is not committed and no daemon is started. Create or sync the index
only when the task needs it.

Repair only V23-owned setup automatically. Tool output is evidence, not a
conclusion. Cross-check source, build, test, or runtime results when the task
depends on them. Never put credentials or private machine paths in PR evidence.
