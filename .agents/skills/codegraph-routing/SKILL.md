---
name: codegraph-routing
description: Use when a task involves code investigation or change in an owner repository with a configured CodeGraph index. Not for known-file, exact-symbol, or exact-text lookup.
---

# CodeGraph routing

Shared V23-owned guidance for the CodeGraph obligation. The full trigger table
lives in the engineering-delivery skill reference
`references/tool-routing.md`; this skill keeps the owner-index contract visible
without overwriting any user-installed `codegraph` skill.

## Obligation

1. Code investigation or change checks the owner repository index
   (`codegraph status --json <repo>`, positional path, not `-p`) BEFORE code
   exploration, then opens the cited current source.
2. Cross-file callers, dependencies, or impact uses a focused structural query
   (`codegraph query|callers|callees|impact -p <repo> --json <symbol>`); a file
   listing or lexical match does not waive it.
3. A reported `pendingChanges` of zero is not proof of freshness. CodeGraph has
   reported zero pending while `sync` then found added and modified files. An
   authorized writable owner repository refreshes (`codegraph sync`, creating
   with `codegraph init` only when missing) before relying on the index.
4. Read-only work treats freshness as unknown: it may query a usable index but
   does not init or sync, traces with `${CODEX_HOME:-$HOME/.codex}/bin/bounded-search.py`
   or direct reads, cross-checks current source, and states the limit.
5. Missing binary, failure, or an unverifiable read-only index falls back
   explicitly to bounded-search or direct reads. Never report it as "not
   needed", never scan home, tmp, or workspace trees to discover setup, and
   never start a daemon or background index.