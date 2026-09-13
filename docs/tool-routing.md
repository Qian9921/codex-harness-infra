# Tool routing

The native UserPromptSubmit hook injects installed instructions and a bounded
live runtime-state block (install manifest and instruction/config integrity).
Daemon probes are explicit. It uses no Stop hook, scheduler, or task database.
Memory of earlier tasks is not treated as current runtime authority. CodeGraph,
Semble, and RTK are used when they change the current task; they are not
mandatory on every prompt. Explicit Doctor `--probe-tools` / `--probe-daemons`
remain available. Tool failure must not block unrelated work.

| Situation | Preferred tool |
| --- | --- |
| Task-relevant source index/query | CodeGraph |
| Task-relevant bounded semantic search | Semble |
| Task-relevant compact workspace command | RTK |
| Local text search | installed `bin/bounded-search.py` |
| Follow-on investigation | The tool whose result best changes the decision |

For a Git checkout, CodeGraph keeps a V23-marked `.codegraph/` cache exclusion
in that checkout's Git-local info/exclude; it never changes the repository's
`.gitignore` or enables a CodeGraph daemon. Doctor or an explicit index request
may create the local cache. In a non-Git directory, CodeGraph performs a
version probe because there is no repository source graph to initialize.

When Semble is used, search a known repository or module scope, not an umbrella
workspace. Its Doctor/probe timeout is 36 seconds so a ~29-second owned-scope
search is not reported as a false tool failure. The live runtime-state probe
that runs `codex app-server daemon version` is budgeted 12 seconds so a
~9-second JSON response is not reported as a false CLI/app-server timeout.

## Bounded local search

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

When a tool fails during an explicit Doctor or task-relevant call, repair only
V23-owned setup automatically. Do not silently reinstall, upgrade, or
reconfigure an independent user tool. Tool output is evidence, not a
conclusion.
