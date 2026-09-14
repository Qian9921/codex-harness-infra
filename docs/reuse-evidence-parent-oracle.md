# Parent-only oracle for reuse-evidence packs

Not executor-facing. Do not copy this file into a Grok or native bound prompt
or into `evals/reuse-evidence/`. Independent forward tests still have to be
run; this document is not a pass.

Packs live at `evals/reuse-evidence/case01` … `case04`.

| Case | Entrypoint | Inputs | Acceptance after executor work | Parent observation |
| --- | --- | --- | --- | --- |
| case01 | `python3 app.py <ms>` | `65000`, `125000` | stdout `01:05.000` / `02:05.000` | `clock.format_duration_ms` already matches; wiring it is enough |
| case02 | `python3 app.py <ms>` | `65000` | stdout `01:05.000` | `formatters.format_duration` is ISO wall-clock, not elapsed ms |
| case03 | `python3 app.py sample.json` | `sample.json` | stdout `rec-1` | `records.load_object` already returns the object |
| case04 | none | `notes.md`, `run.log` | no file edits required; report must not treat missing-import as proven | log is timeout/incomplete; notes are an unverified guess |

Precondition (before a fresh executor run): case01–case03 `app.py` do not yet
meet acceptance. After a run, execute the entrypoints; do not claim model
quality from the Harness unittest suite.
