# Engineering standards

These are concise operating standards for work performed through this repository. A host repository's explicit build, test, API, and style rules take precedence.

## Change quality

- Define the requested behavior before choosing an implementation.
- Inspect the current call path and owner helpers, then existing dependencies
  and neighbor APIs, before commitment or a new implementation. Name similarity
  is not fitness; copying is not reuse; new code needs an evidenced gap.
  Match evidence to the claim; insufficient evidence stays a hypothesis.
  Delegation carries the gap and unknowns, not a prescribed new component.
- Prefer the smallest design that satisfies that behavior.
- Keep one change internally coherent and easy to review.
- Separate unrelated refactoring, formatting, generated output, and behavior changes unless combining them is clearly safer.
- Keep tests with the behavior they protect when the host project supports that structure.
- Report what was actually verified and what remains unverified.

These rules follow Google's code-review standard: the target is sustained code health, not perfection. A change should improve the local system without adding unnecessary complexity.

## Review quality

Review in this order:

1. Correctness and requested behavior.
2. Data loss, security, and external-action risk.
3. Design ownership, dependency direction, and rollback path.
4. Tests, determinism, and failure handling.
5. Readability, naming, and local style.

Separate blocking defects from optional polish. A review comment should identify the concrete behavior or evidence that makes it actionable. Keep the change small enough for one reviewer to understand; split or justify a change that is conceptually too large.

## C++

Use the host repository's compiler, formatter, warnings, ownership conventions, and test runner. Prefer clear value and lifetime semantics, explicit interfaces, and focused tests for behavior and numerical boundaries. Follow the [Google C++ Style Guide](https://google.github.io/styleguide/cppguide.html) when the host project has no stronger local rule.

## Python

Use the host repository's supported interpreter, formatter, type checker, and test runner. Keep modules focused, interfaces explicit, and error handling close to the operation that can fail. Avoid hidden global state and unbounded retries. Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) when the host project has no stronger local rule.

## Research and numerical work

State the comparison target, input domain, metric, and limitations. Distinguish observed results from planned work. Preserve a reference implementation and pathological cases when they are needed to support the claim. Do not generalize a narrow experiment beyond its evidence.

## Documentation

Write for the reader's next action: explain what the reader can do, prerequisites, expected result, failure recovery, and source provenance where it matters. Record why a non-obvious architectural decision was made in `docs/decisions/`. Keep permanent instructions short; put reusable detail in the relevant on-demand reference. Search only an explicit repository, module, or file set.

## Sources

- [Google Engineering Practices](https://google.github.io/eng-practices/)
- [Google Developer Documentation Style Guide](https://developers.google.com/style)
- [Google C++ Style Guide](https://google.github.io/styleguide/cppguide.html)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
