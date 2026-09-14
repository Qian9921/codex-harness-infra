# Reuse-before-decision fixtures

Disposable artifact packs for independent forward testing. They are not a
model-quality suite and do not encode an answer label or checklist.

Run locally:

```bash
python -m unittest tests.test_reuse_evidence_fixtures -v
```

Limits: no network; no extra packages; files stay tiny. A parent session may
hand one pack to an executor and inspect whether it reused, rejected a
lookalike, called a neighbor API, or revised a causal claim from evidence.
Do not treat these files as `pass@k` or token-cost evidence.

Packs:

- `exact-function` — `format_duration_ms` already implements the requested
  millisecond duration string.
- `similar-name` — `format_duration` formats a wall-clock timestamp, not an
  elapsed duration.
- `neighbor-dep` — `records.py` already parses the JSON object the caller
  needs.
- `contradict-hypothesis` — `notes.md` guesses a missing import; `run.log`
  shows a 15s timeout instead.
