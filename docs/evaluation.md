# V23 evaluation

V23 has a small, versioned scenario suite at `evals/v23-scenarios.toml`. It
measures the Harness itself, rather than a particular model's benchmark score.
The suite is deliberately offline, deterministic, and dependency-free: every
scenario runs a controlled temporary environment against the production
installer, tool bootstrap, or GitHub delivery adapter.

Run it with:

```bash
python scripts/evaluate.py
```

The JSON report always includes the complete denominator (`total`, `passed`,
`failed`, and every scenario), the current Git HEAD, whether the worktree was
clean, and a counted list of unobserved claims. A green offline result means
that the tested Harness mechanics worked; it does not mean that an LLM followed
prompts reliably, that token cost was acceptable, or that GitHub delivery
succeeded remotely.

Use the optional local smoke only on a configured V23 machine:

```bash
python scripts/evaluate.py --live
```

`--live` executes the installed UserPromptSubmit Hook and Doctor against the
local project, checks the real CodeGraph/Semble/RTK results, and performs no
GitHub write. It may create only the V23-owned Git-local CodeGraph cache.

For a model or Harness comparison, create a disposable repository, freeze the
prompt, initial revision, tool availability, model/profile, and task budget;
then run each configuration in fresh contexts. Report `pass@1`, `pass^k`,
wall-clock distribution, and tokens per successful task, along with every
failure. Do not call mocked adapter scenarios model success, real GitHub E2E,
or `pass@k`.
