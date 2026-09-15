# Pi adapter process lifecycle

This is the canonical technical reference for dedicated process-group
ownership, signal coordination, and cleanup in `scripts/grok_execution.py`.
Runtime guarantees remain in `AGENTS.md`, `WORKFLOW.md`, and the grok-execution
skill; this file holds the internals so they are not repeated in every prompt.

## Guarantees

- External dispatch is the generic Pi adapter. GrokCLI is not invoked.
- Provider, model, and thinking come from the selected executor. `xai/grok-4.6`
  uses `xhigh`; `max` is accepted only where valid for that identity.
- `run`/`resume` wait without a wall-clock timeout while the dedicated Pi
  process group is alive and not a zombie. An explicit positive timeout is
  optional.
- Cleanup ownership is taken immediately after spawn.
- Public registry and signal cleanup require a validated dedicated PGID
  (`getpgid == pid`).
- All Codex Pi `run`/`resume` invocations are supervised by a separately
  spawned generic Luna-low native subagent that watches lifecycle and receipt
  only and never edits. The parent waits for the supervisor completion event
  and does not narrate or poll the adapter.
- `v23_executor` is the quota-exhaustion-only fallback and is not the
  supervisor. Timeout, authentication, network, bridge, model-identity, or
  receipt errors do not authorize fallback.

## Spawn and cleanup token

`Popen(..., start_new_session=True)` is used; `preexec_fn` is forbidden. Spawn
immediately issues an internal cleanup-ownership token. Because
`start_new_session=True`, the candidate PGID is `proc.pid`. Local
validation/recording failure kills only through that spawn-issued candidate
token, then performs a bounded reap. Dedicated-PGID validation failure,
registry-full errors, boundary-hook exceptions, and later setup failures
kill/reap the spawned group and unregister only if registered. After the group
is validated it is registered in the process-global PGID table immediately;
validation failure fails closed.

## Registry and signals

Before `run`/`resume`/`batch` the main process blocks SIGTERM/SIGHUP/SIGINT and
starts a sigwait coordinator. The registry lock spans termination check, spawn,
PGID validation, and publication so concurrent batch registrations cannot
overwrite slots. The coordinator uses the same lock, marks terminating,
snapshots validated dedicated PGIDs, SIGKILLs those groups with bounded
nonblocking `killpg`, then restores default disposition and re-raises the
signal.

## Post-exec launcher

A dedicated post-exec Python launcher, started with `Popen`/`start_new_session`
and no `preexec_fn`, unblocks and resets termination-signal dispositions,
restores SIGPIPE and SIGXFSZ to SIG_DFL when `getattr` finds them on the
platform (matching Python `subprocess.restore_signals`), then `execvpe`s the
real Pi command so the inherited blocked-signal mask is cleared after exec.
The launcher interval stays inside the validated dedicated PGID that cleanup
SIGKILLs.

## Drain

Every normal return and `BaseException` path terminates remaining group members
with a bounded drain/reap, preserving the direct child's stdout, stderr, and
returncode on normal completion.
